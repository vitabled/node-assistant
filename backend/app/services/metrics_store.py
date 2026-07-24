"""
SQLite time-series store for xray-checker samples.

xray-checker itself only exposes CURRENT state (via /api/v1/*), it does not
retain history. node-assistant polls the checker and appends samples here so the
dashboard can draw the 24h ping/availability graph.

Uses stdlib `sqlite3` (no extra pip dependency). Calls are small and infrequent
(one poll every `poll_interval` seconds), so synchronous access wrapped in a
thread executor is more than fast enough.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from collections import deque
from itertools import groupby
from pathlib import Path
from typing import Any, Deque, Optional

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
_DB_PATH = DATA_DIR / "xray_checker_metrics.db"

# Keep ~35 days so the status page can compute 30-day uptime.
_RETENTION_SECONDS = 35 * 24 * 3600

# Latency at/above this is "slow" (orange bar on the status page).
SLOW_MS = 800
_RING_MAX = 90

# In-memory ring buffer of the last N ticks per node — the status-page uptime
# bars are served from here so frequent dashboard polls don't hit the disk.
# SQLite remains the source of truth for 30-day uptime and incidents.
# (Single-process/single-worker backend; SQLite is the cross-process fallback.)
# Keyed by (checker_id, stable_id): the same stable_id can exist on different
# checker instances (local + remotes), so the checker id disambiguates.
_RING: dict[tuple[str, str], Deque[dict[str, Any]]] = {}
_META: dict[tuple[str, str], dict[str, str]] = {}   # (checker_id, stable_id) -> {name, group_name, protocol}

# Default instance id for the shared local Docker checker.
LOCAL_CHECKER_ID = "local"


def _tick_status(online: int, latency_ms: int) -> str:
    if not online:
        return "down"
    if latency_ms is not None and latency_ms >= 0 and latency_ms >= SLOW_MS:
        return "slow"
    return "up"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _init() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS proxy_samples (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ts         INTEGER NOT NULL,           -- unix seconds
                stable_id  TEXT    NOT NULL,
                name       TEXT    NOT NULL,
                group_name TEXT    DEFAULT '',
                online     INTEGER NOT NULL,           -- 0/1
                latency_ms INTEGER NOT NULL,           -- -1 when offline/unknown
                checker_id TEXT    NOT NULL DEFAULT 'local'  -- which checker instance sampled this
            );
            CREATE INDEX IF NOT EXISTS idx_samples_ts ON proxy_samples(ts);
            CREATE INDEX IF NOT EXISTS idx_samples_sid_ts ON proxy_samples(stable_id, ts);
            """
        )
        # Migration for DBs created before checker_id existed: add the column
        # (SQLite backfills existing rows to the DEFAULT 'local') + its index.
        # Idempotent — guarded on PRAGMA so re-runs are no-ops.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(proxy_samples)")}
        if "checker_id" not in cols:
            conn.execute(
                "ALTER TABLE proxy_samples ADD COLUMN checker_id TEXT NOT NULL DEFAULT 'local'"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_samples_cid_ts ON proxy_samples(checker_id, ts)"
        )
        # Covering index for `_bars`' node discovery: it turns "which nodes does
        # this checker have?" into an ordered covering scan instead of a temp
        # B-tree DISTINCT (measured 388ms -> 98ms on 504k rows). Built once on
        # first start for existing DBs (~0.6 s at that size).
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_samples_cid_sid ON proxy_samples(checker_id, stable_id)"
        )
        # Covering index for `_uptime_30d`: (checker_id, stable_id, ts, online).
        # Leads with (checker_id, stable_id) so the GROUP BY stable_id needs NO
        # temp sort, and carries ts+online so the 30-day aggregation is
        # index-only (no table-row visits). Measured 925ms -> 291ms on 504k rows
        # (a plain covering (cid,ts,sid,online) only reached 569ms — the
        # stable_id-leading order is what removes the sort).
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_samples_cid_sid_ts_online "
            "ON proxy_samples(checker_id, stable_id, ts, online)"
        )


# Initialise the schema on import (idempotent — this is the "migration").
_init()


def _warm_ring() -> None:
    """Load the last _RING_MAX ticks per node from SQLite into the in-memory ring
    so the status-page bars are populated immediately after a backend restart."""
    since = int(time.time()) - _RETENTION_SECONDS
    try:
        with _connect() as conn:
            cur = conn.execute(
                """
                SELECT checker_id, stable_id, name, group_name, ts, online, latency_ms FROM (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY checker_id, stable_id ORDER BY ts DESC) AS rn
                    FROM proxy_samples WHERE ts >= ?
                ) WHERE rn <= ? ORDER BY checker_id, stable_id, ts ASC
                """,
                (since, _RING_MAX),
            )
            for r in cur.fetchall():
                key = (r["checker_id"] or LOCAL_CHECKER_ID, r["stable_id"])
                _RING.setdefault(key, deque(maxlen=_RING_MAX)).append(
                    {"ts": r["ts"], "status": _tick_status(r["online"], r["latency_ms"])}
                )
                _META[key] = {"name": r["name"], "group_name": r["group_name"] or "", "protocol": ""}
    except Exception:
        pass  # window functions need sqlite >= 3.25 — degrade gracefully to live-only


_warm_ring()


def _insert_batch(samples: list[dict[str, Any]], checker_id: str = LOCAL_CHECKER_ID) -> None:
    ts = int(time.time())
    rows = []
    for s in samples:
        sid = s.get("stableId", "")
        online = 1 if s.get("online") else 0
        latency = int(s.get("latencyMs", -1)) if s.get("online") else -1
        rows.append((ts, sid, s.get("name", ""), s.get("groupName", "") or "", online, latency, checker_id))
        # Update the in-memory ring buffer alongside the DB write.
        key = (checker_id, sid)
        _RING.setdefault(key, deque(maxlen=_RING_MAX)).append(
            {"ts": ts, "status": _tick_status(online, latency)}
        )
        _META[key] = {
            "name": s.get("name", ""),
            "group_name": s.get("groupName", "") or "",
            "protocol": s.get("protocol", "") or "",
        }
    with _connect() as conn:
        conn.executemany(
            "INSERT INTO proxy_samples (ts, stable_id, name, group_name, online, latency_ms, checker_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        # Opportunistic retention cleanup.
        conn.execute("DELETE FROM proxy_samples WHERE ts < ?", (ts - _RETENTION_SECONDS,))


# ── Status-page queries ───────────────────────────────────────
# Every read accepts an optional `checker_id`: None = aggregate across ALL
# instances (back-compat), a value = restrict to that one checker instance.

def _cid_clause(checker_id: Optional[str]) -> tuple[str, tuple]:
    """SQL fragment + params for an optional checker_id filter.

    CAVEAT: `checker_id=None` aggregates across ALL instances keyed by stable_id
    alone (`_bars`, and GROUP BY stable_id in `_uptime_30d`/`_node_uptime`). If two
    instances ever share a stable_id, that view silently merges them. No API route
    calls these with None today — they always pass an explicit checker_id — so this
    is latent. A future "all instances merged" view must group by (checker_id,
    stable_id), not stable_id alone.
    """
    if checker_id is None:
        return "", ()
    return " AND checker_id = ?", (checker_id,)


def _bars_from_ring(n: int, checker_id: Optional[str]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for (cid, sid), dq in _RING.items():
        if checker_id is not None and cid != checker_id:
            continue
        out[sid] = list(dq)[-n:]
    return out


def _bars(n: int, checker_id: Optional[str] = None) -> dict[str, list[dict[str, Any]]]:
    """Last `n` ticks per node: [{ts, status: up|slow|down}], keyed by stable_id.
    Filtered to `checker_id` when given.

    Reads SQLite, NOT the in-process ring. The ring is only ever appended to by
    `record_samples`, i.e. by whichever process runs the poller — so under
    `--profile split` (sampler in the `monitoring` container) a ring-backed read
    in the gateway would be frozen at whatever `_warm_ring` loaded at boot, and
    the status page would show stale bars forever. The ring survives as a
    fallback for sqlite < 3.25, which has no window functions — the same
    graceful degradation `_warm_ring` already takes.
    """
    n = max(1, min(n, _RING_MAX))
    since = int(time.time()) - _RETENTION_SECONDS
    cc, cp = _cid_clause(checker_id)
    try:
        with _connect() as conn:
            # One indexed tail per node, NOT a single ROW_NUMBER() query: the
            # window function has to rank every row inside the 35-day retention
            # window before the outer `rn <= n` filter, which measured at ~1.5-2 s
            # on a half-million-row DB — on an endpoint the dashboard polls every
            # 10 s. Per-node `WHERE stable_id=? ORDER BY ts DESC LIMIT n` rides
            # idx_samples_sid_ts and touches only the n rows it returns.
            # No ts filter on the discovery query: that is what lets it ride
            # idx_samples_cid_sid as a covering scan. Retention already bounds the
            # table to 35 days, and nodes with nothing inside the window are
            # dropped below.
            where = " WHERE checker_id = ?" if checker_id is not None else ""
            ids = [r["stable_id"] for r in conn.execute(
                "SELECT DISTINCT stable_id FROM proxy_samples" + where, cp,
            ).fetchall()]
            out: dict[str, list[dict[str, Any]]] = {}
            for sid in ids:
                rows = conn.execute(
                    "SELECT ts, online, latency_ms FROM proxy_samples "
                    "WHERE stable_id=? AND ts >= ?" + cc + " ORDER BY ts DESC LIMIT ?",
                    (sid, since, *cp, n),
                ).fetchall()
                if not rows:
                    continue
                out[sid] = [
                    {"ts": r["ts"], "status": _tick_status(r["online"], r["latency_ms"])}
                    for r in reversed(rows)
                ]
            return out
    except Exception:
        return _bars_from_ring(n, checker_id)


def _uptime_30d(checker_id: Optional[str] = None) -> dict[str, Any]:
    """Per-node and global uptime % over the last 30 days (from SQLite).

    ONE grouped scan, not two: the global % is derived from the per-node sums
    (Σonline / Σcount), which is identical to a second `AVG(online)` scan over
    the whole window but avoids re-reading every row. Combined with the covering
    index `idx_samples_cid_sid_ts_online` this is index-only. (925ms -> 291ms.)"""
    since = int(time.time()) - 30 * 24 * 3600
    cc, cp = _cid_clause(checker_id)
    per: dict[str, Optional[float]] = {}
    tot_online = tot_n = 0
    with _connect() as conn:
        cur = conn.execute(
            "SELECT stable_id, SUM(online) AS s, COUNT(*) AS c FROM proxy_samples "
            "WHERE ts >= ?" + cc + " GROUP BY stable_id",
            (since, *cp),
        )
        for r in cur.fetchall():
            s, c = r["s"] or 0, r["c"] or 0
            per[r["stable_id"]] = round(s * 100.0 / c, 2) if c else None
            tot_online += s
            tot_n += c
    glob = round(tot_online * 100.0 / tot_n, 2) if tot_n else None
    return {"global": glob, "per_node": per}


def _incidents(days: int, checker_id: Optional[str] = None) -> list[dict[str, Any]]:
    """Derive downtime incidents from the samples: a run of consecutive offline
    samples per node becomes one incident (start → recovery, with duration)."""
    since = int(time.time()) - days * 86400
    now = int(time.time())
    cc, cp = _cid_clause(checker_id)
    with _connect() as conn:
        rows = conn.execute(
            "SELECT stable_id, name, group_name, ts, online FROM proxy_samples "
            "WHERE ts >= ?" + cc + " ORDER BY stable_id, ts ASC",
            (since, *cp),
        ).fetchall()

    incidents: list[dict[str, Any]] = []
    for sid, grp in groupby(rows, key=lambda r: r["stable_id"]):
        down_start: int | None = None
        name = group = ""
        for r in grp:
            name, group = r["name"], (r["group_name"] or "")
            if r["online"] == 0 and down_start is None:
                down_start = r["ts"]
            elif r["online"] == 1 and down_start is not None:
                incidents.append({
                    "stableId": sid, "name": name, "group": group,
                    "start": down_start, "end": r["ts"],
                    "durationSec": r["ts"] - down_start,
                    "reason": "Проверка не пройдена (таймаут/недоступна)",
                    "ongoing": False,
                })
                down_start = None
        if down_start is not None:  # still down at the end of the window
            incidents.append({
                "stableId": sid, "name": name, "group": group,
                "start": down_start, "end": now, "durationSec": now - down_start,
                "reason": "Проверка не пройдена (таймаут/недоступна)", "ongoing": True,
            })
    incidents.sort(key=lambda x: x["start"], reverse=True)
    return incidents


def _history(hours: int, checker_id: Optional[str] = None) -> dict[str, Any]:
    """Return time-bucketed averages for the ping graph + availability over the window."""
    since = int(time.time()) - hours * 3600
    # Bucket size: aim for ~120 points across the window.
    bucket = max(60, (hours * 3600) // 120)
    cc, cp = _cid_clause(checker_id)
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT (ts / ?) * ? AS bucket_ts,
                   AVG(CASE WHEN online = 1 THEN latency_ms END) AS avg_latency,
                   AVG(online) * 100.0 AS availability
            FROM proxy_samples
            WHERE ts >= ?""" + cc + """
            GROUP BY bucket_ts
            ORDER BY bucket_ts ASC
            """,
            (bucket, bucket, since, *cp),
        )
        points = [
            {
                "ts": int(r["bucket_ts"]),
                "avg_latency_ms": round(r["avg_latency"], 1) if r["avg_latency"] is not None else None,
                "availability_pct": round(r["availability"], 1) if r["availability"] is not None else None,
            }
            for r in cur.fetchall()
        ]
    return {"hours": hours, "points": points}


def _node_uptime(hours: int, checker_id: Optional[str] = None) -> dict[str, dict[str, Any]]:
    """Per-node uptime over the window: online-fraction * 100, keyed by stable_id.

    Returns { stable_id: { uptime_pct, checks, last_seen } } where last_seen is
    the unix ts of the most recent successful (online) check, or None.
    """
    since = int(time.time()) - hours * 3600
    cc, cp = _cid_clause(checker_id)
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT stable_id,
                   AVG(online) * 100.0 AS uptime_pct,
                   COUNT(*)            AS checks,
                   MAX(CASE WHEN online = 1 THEN ts END) AS last_seen
            FROM proxy_samples
            WHERE ts >= ?""" + cc + """
            GROUP BY stable_id
            """,
            (since, *cp),
        )
        return {
            r["stable_id"]: {
                "uptime_pct": round(r["uptime_pct"], 1) if r["uptime_pct"] is not None else None,
                "checks": r["checks"],
                "last_seen": r["last_seen"],
            }
            for r in cur.fetchall()
        }


# ── Async wrappers (run the blocking sqlite calls in a thread) ──

async def record_samples(samples: list[dict[str, Any]], checker_id: str = LOCAL_CHECKER_ID) -> None:
    if not samples:
        return
    await asyncio.to_thread(_insert_batch, samples, checker_id)


async def get_history(hours: int = 24, checker_id: Optional[str] = None) -> dict[str, Any]:
    return await asyncio.to_thread(_history, hours, checker_id)


async def get_node_uptime(hours: int = 24, checker_id: Optional[str] = None) -> dict[str, dict[str, Any]]:
    return await asyncio.to_thread(_node_uptime, hours, checker_id)


async def get_bars(n: int = 30, checker_id: Optional[str] = None) -> dict[str, list[dict[str, Any]]]:
    return await asyncio.to_thread(_bars, n, checker_id)


async def get_uptime_30d(checker_id: Optional[str] = None) -> dict[str, Any]:
    return await asyncio.to_thread(_uptime_30d, checker_id)


async def get_incidents(days: int = 7, checker_id: Optional[str] = None) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_incidents, days, checker_id)
