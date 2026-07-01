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
from typing import Any, Deque

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
_RING: dict[str, Deque[dict[str, Any]]] = {}
_META: dict[str, dict[str, str]] = {}   # stable_id -> {name, group_name, protocol}


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
                latency_ms INTEGER NOT NULL            -- -1 when offline/unknown
            );
            CREATE INDEX IF NOT EXISTS idx_samples_ts ON proxy_samples(ts);
            CREATE INDEX IF NOT EXISTS idx_samples_sid_ts ON proxy_samples(stable_id, ts);
            """
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
                SELECT stable_id, name, group_name, ts, online, latency_ms FROM (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY stable_id ORDER BY ts DESC) AS rn
                    FROM proxy_samples WHERE ts >= ?
                ) WHERE rn <= ? ORDER BY stable_id, ts ASC
                """,
                (since, _RING_MAX),
            )
            for r in cur.fetchall():
                sid = r["stable_id"]
                _RING.setdefault(sid, deque(maxlen=_RING_MAX)).append(
                    {"ts": r["ts"], "status": _tick_status(r["online"], r["latency_ms"])}
                )
                _META[sid] = {"name": r["name"], "group_name": r["group_name"] or "", "protocol": ""}
    except Exception:
        pass  # window functions need sqlite >= 3.25 — degrade gracefully to live-only


_warm_ring()


def _insert_batch(samples: list[dict[str, Any]]) -> None:
    ts = int(time.time())
    rows = []
    for s in samples:
        sid = s.get("stableId", "")
        online = 1 if s.get("online") else 0
        latency = int(s.get("latencyMs", -1)) if s.get("online") else -1
        rows.append((ts, sid, s.get("name", ""), s.get("groupName", "") or "", online, latency))
        # Update the in-memory ring buffer alongside the DB write.
        _RING.setdefault(sid, deque(maxlen=_RING_MAX)).append(
            {"ts": ts, "status": _tick_status(online, latency)}
        )
        _META[sid] = {
            "name": s.get("name", ""),
            "group_name": s.get("groupName", "") or "",
            "protocol": s.get("protocol", "") or "",
        }
    with _connect() as conn:
        conn.executemany(
            "INSERT INTO proxy_samples (ts, stable_id, name, group_name, online, latency_ms) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        # Opportunistic retention cleanup.
        conn.execute("DELETE FROM proxy_samples WHERE ts < ?", (ts - _RETENTION_SECONDS,))


# ── Status-page queries ───────────────────────────────────────

def _bars(n: int) -> dict[str, list[dict[str, Any]]]:
    """Last `n` ticks per node from the ring: [{ts, status: up|slow|down}]."""
    n = max(1, min(n, _RING_MAX))
    return {sid: list(dq)[-n:] for sid, dq in _RING.items()}


def _uptime_30d() -> dict[str, Any]:
    """Per-node and global uptime % over the last 30 days (from SQLite)."""
    since = int(time.time()) - 30 * 24 * 3600
    with _connect() as conn:
        cur = conn.execute(
            "SELECT stable_id, AVG(online) * 100.0 AS up FROM proxy_samples "
            "WHERE ts >= ? GROUP BY stable_id",
            (since,),
        )
        per = {r["stable_id"]: round(r["up"], 2) if r["up"] is not None else None
               for r in cur.fetchall()}
        g = conn.execute(
            "SELECT AVG(online) * 100.0 AS up FROM proxy_samples WHERE ts >= ?", (since,)
        ).fetchone()
        glob = round(g["up"], 2) if g and g["up"] is not None else None
    return {"global": glob, "per_node": per}


def _incidents(days: int) -> list[dict[str, Any]]:
    """Derive downtime incidents from the samples: a run of consecutive offline
    samples per node becomes one incident (start → recovery, with duration)."""
    since = int(time.time()) - days * 86400
    now = int(time.time())
    with _connect() as conn:
        rows = conn.execute(
            "SELECT stable_id, name, group_name, ts, online FROM proxy_samples "
            "WHERE ts >= ? ORDER BY stable_id, ts ASC",
            (since,),
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


def _history(hours: int) -> dict[str, Any]:
    """Return time-bucketed averages for the ping graph + availability over the window."""
    since = int(time.time()) - hours * 3600
    # Bucket size: aim for ~120 points across the window.
    bucket = max(60, (hours * 3600) // 120)
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT (ts / ?) * ? AS bucket_ts,
                   AVG(CASE WHEN online = 1 THEN latency_ms END) AS avg_latency,
                   AVG(online) * 100.0 AS availability
            FROM proxy_samples
            WHERE ts >= ?
            GROUP BY bucket_ts
            ORDER BY bucket_ts ASC
            """,
            (bucket, bucket, since),
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


def _node_uptime(hours: int) -> dict[str, dict[str, Any]]:
    """Per-node uptime over the window: online-fraction * 100, keyed by stable_id.

    Returns { stable_id: { uptime_pct, checks, last_seen } } where last_seen is
    the unix ts of the most recent successful (online) check, or None.
    """
    since = int(time.time()) - hours * 3600
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT stable_id,
                   AVG(online) * 100.0 AS uptime_pct,
                   COUNT(*)            AS checks,
                   MAX(CASE WHEN online = 1 THEN ts END) AS last_seen
            FROM proxy_samples
            WHERE ts >= ?
            GROUP BY stable_id
            """,
            (since,),
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

async def record_samples(samples: list[dict[str, Any]]) -> None:
    if not samples:
        return
    await asyncio.to_thread(_insert_batch, samples)


async def get_history(hours: int = 24) -> dict[str, Any]:
    return await asyncio.to_thread(_history, hours)


async def get_node_uptime(hours: int = 24) -> dict[str, dict[str, Any]]:
    return await asyncio.to_thread(_node_uptime, hours)


async def get_bars(n: int = 30) -> dict[str, list[dict[str, Any]]]:
    return await asyncio.to_thread(_bars, n)


async def get_uptime_30d() -> dict[str, Any]:
    return await asyncio.to_thread(_uptime_30d)


async def get_incidents(days: int = 7) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_incidents, days)
