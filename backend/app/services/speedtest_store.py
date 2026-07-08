"""Per-account SQLite store for node speed-test history (Ф2, wave1).

`POST /api/stats/node-speedtest` records each run (characteristics + iperf/
speedtest/xray measurements) here so deploy cards can show the last result
without re-running SSH probes. Shared with Ф2b (the pair-matrix «Тесты
скорости» writes `kind='pair'` rows keyed by the pair).

Per-account isolation: `accounts/<id>/node_speedtests.db`. Explicit-`account_id`
pattern (like `user_stats_store.py`) — callers pass the account id; the
ContextVar is only a request-scope fallback. Sync sqlite via `asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from app.services import accounts

# Keep 90 days of runs (enough for a quarter of speed history).
_RETENTION_SECONDS = 90 * 24 * 3600

# Metric/characteristic columns accepted from `record_run` rows (everything
# except id/ts, which the store owns). Unknown row keys are silently dropped.
_COLUMNS = (
    "resource_key",
    "kind",
    "iperf_mbps",
    "iperf_jitter",
    "ping_ms",
    "traceroute",
    "st_down",
    "st_up",
    "st_ping",
    "xray_down",
    "xray_up",
    "xray_ping",
    "cpu",
    "ram_mb",
    "disk",
)


def _dir(account_id: Optional[str]) -> Path:
    aid = account_id or accounts.current_account.get()
    if not aid:
        raise RuntimeError("No active account in context")
    return accounts.data_dir(aid)


def _db_path(account_id: Optional[str]) -> Path:
    return _dir(account_id) / "node_speedtests.db"


# Per-path schema-init guard (one CREATE per db file per process).
_initialised: set[str] = set()
_init_lock = threading.Lock()


def _ensure_schema(path: Path) -> None:
    key = str(path)
    if key in _initialised:
        return
    with _init_lock:
        if key in _initialised:
            return
        with sqlite3.connect(path, timeout=10) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts           INTEGER NOT NULL,
                    resource_key TEXT    NOT NULL,
                    kind         TEXT    NOT NULL DEFAULT 'node',
                    iperf_mbps   REAL,
                    iperf_jitter REAL,
                    ping_ms      REAL,
                    traceroute   TEXT,
                    st_down      REAL,
                    st_up        REAL,
                    st_ping      REAL,
                    xray_down    REAL,
                    xray_up      REAL,
                    xray_ping    REAL,
                    cpu          TEXT,
                    ram_mb       INTEGER,
                    disk         TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_runs_key ON runs(resource_key, ts);
                """
            )
        _initialised.add(key)


def _connect(account_id: Optional[str]) -> sqlite3.Connection:
    path = _db_path(account_id)
    _ensure_schema(path)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


# ── sync bodies ────────────────────────────────────────────────


def _record_run(account_id: Optional[str], row: dict) -> None:
    ts = int(time.time())
    data = {k: row[k] for k in _COLUMNS if row.get(k) is not None}
    data.setdefault("kind", "node")
    cols = ["ts", *data.keys()]
    placeholders = ", ".join("?" for _ in cols)
    with _connect(account_id) as conn:
        conn.execute(
            f"INSERT INTO runs ({', '.join(cols)}) VALUES ({placeholders})",
            [ts, *data.values()],
        )
        conn.execute("DELETE FROM runs WHERE ts < ?", (ts - _RETENTION_SECONDS,))


def _history(
    account_id: Optional[str], resource_key: str, limit: int
) -> list[dict[str, Any]]:
    with _connect(account_id) as conn:
        rows = conn.execute(
            "SELECT * FROM runs WHERE resource_key = ? ORDER BY ts DESC, id DESC LIMIT ?",
            (resource_key, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def _history_by_kind(
    account_id: Optional[str], kinds: tuple[str, ...], limit: int
) -> list[dict[str, Any]]:
    if not kinds:
        return []  # `IN ()` is a SQLite syntax error
    placeholders = ", ".join("?" for _ in kinds)
    with _connect(account_id) as conn:
        rows = conn.execute(
            f"SELECT * FROM runs WHERE kind IN ({placeholders}) "
            "ORDER BY ts DESC, id DESC LIMIT ?",
            (*kinds, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ── async API ──────────────────────────────────────────────────


async def record_run(account_id: Optional[str], row: dict) -> None:
    await asyncio.to_thread(_record_run, account_id, row)


async def history(
    account_id: Optional[str], resource_key: str, limit: int = 20
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_history, account_id, resource_key, limit)


async def history_by_kind(
    account_id: Optional[str], kinds: tuple[str, ...], limit: int = 50
) -> list[dict[str, Any]]:
    """Recent runs of the given kinds (newest first), across all resource keys.
    Used by the «Тесты скорости» section to list pair + xray runs together."""
    return await asyncio.to_thread(_history_by_kind, account_id, tuple(kinds), limit)


async def latest(
    account_id: Optional[str], resource_key: str
) -> Optional[dict[str, Any]]:
    rows = await history(account_id, resource_key, limit=1)
    return rows[0] if rows else None
