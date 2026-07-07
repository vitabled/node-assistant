"""Per-account SQLite store for user↔node history snapshots (Ф3).

A background collector periodically snapshots Remnawave's per-node `usersOnline`
count (the reliable signal) plus per-node top-users-by-usage (best-effort
membership). This store keeps that history so the «Пользователи» stats widgets
can render node-load over time, busiest nodes, and BEST-EFFORT user migrations.

Per-account isolation: each account's history lives in `accounts/<id>/
user_stats.db`. Uses `storage.py`'s explicit-`account_id` pattern (NOT a
ContextVar-only resolver) because the collector runs as a lifespan background task
with NO request context and must pass the account id explicitly.
"""
from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from app.services import accounts

# Keep ~35 days of snapshots (matches the checker metrics retention).
_RETENTION_SECONDS = 35 * 24 * 3600


def _dir(account_id: Optional[str]) -> Path:
    aid = account_id or accounts.current_account.get()
    if not aid:
        raise RuntimeError("No active account in context")
    return accounts.data_dir(aid)


def _db_path(account_id: Optional[str]) -> Path:
    return _dir(account_id) / "user_stats.db"


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
                CREATE TABLE IF NOT EXISTS node_load_samples (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts           INTEGER NOT NULL,
                    node_uuid    TEXT    NOT NULL,
                    node_name    TEXT    DEFAULT '',
                    users_online INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_load_ts ON node_load_samples(ts);
                CREATE INDEX IF NOT EXISTS idx_load_node ON node_load_samples(node_uuid, ts);

                CREATE TABLE IF NOT EXISTS node_top_users (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts          INTEGER NOT NULL,
                    node_uuid   TEXT    NOT NULL,
                    username    TEXT    NOT NULL,
                    total_bytes INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_top_ts ON node_top_users(ts);
                CREATE INDEX IF NOT EXISTS idx_top_user ON node_top_users(username, ts);
                """
            )
        _initialised.add(key)


def _connect(account_id: Optional[str]) -> sqlite3.Connection:
    path = _db_path(account_id)
    _ensure_schema(path)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


# ── writes ─────────────────────────────────────────────────────

def _record_snapshot(nodes: list[dict], top: dict[str, list[dict]],
                     account_id: Optional[str]) -> None:
    """nodes: [{nodeUuid, nodeName, usersOnline}]; top: {node_uuid: [{username,total}]}."""
    ts = int(time.time())
    load_rows = [
        (ts, n.get("nodeUuid", ""), n.get("nodeName", "") or "", int(n.get("usersOnline", 0) or 0))
        for n in nodes if n.get("nodeUuid")
    ]
    top_rows = []
    for node_uuid, users in (top or {}).items():
        for u in users or []:
            top_rows.append((ts, node_uuid, str(u.get("username", "")), int(u.get("total", 0) or 0)))
    with _connect(account_id) as conn:
        if load_rows:
            conn.executemany(
                "INSERT INTO node_load_samples (ts, node_uuid, node_name, users_online) "
                "VALUES (?, ?, ?, ?)", load_rows)
        if top_rows:
            conn.executemany(
                "INSERT INTO node_top_users (ts, node_uuid, username, total_bytes) "
                "VALUES (?, ?, ?, ?)", top_rows)
        cutoff = ts - _RETENTION_SECONDS
        conn.execute("DELETE FROM node_load_samples WHERE ts < ?", (cutoff,))
        conn.execute("DELETE FROM node_top_users WHERE ts < ?", (cutoff,))


# ── reads ──────────────────────────────────────────────────────

def _node_load(hours: int, account_id: Optional[str]) -> dict[str, Any]:
    """Per-node usersOnline series over the window + latest value + averages."""
    since = int(time.time()) - hours * 3600
    with _connect(account_id) as conn:
        series_rows = conn.execute(
            "SELECT node_uuid, node_name, ts, users_online FROM node_load_samples "
            "WHERE ts >= ? ORDER BY node_uuid, ts ASC", (since,),
        ).fetchall()
        agg_rows = conn.execute(
            "SELECT node_uuid, MAX(node_name) AS node_name, "
            "AVG(users_online) AS avg_online, MAX(users_online) AS peak_online, COUNT(*) AS n "
            "FROM node_load_samples WHERE ts >= ? GROUP BY node_uuid", (since,),
        ).fetchall()
    series: dict[str, dict[str, Any]] = {}
    for r in series_rows:
        s = series.setdefault(r["node_uuid"], {"node_uuid": r["node_uuid"],
                                               "node_name": r["node_name"] or "", "points": []})
        s["points"].append({"ts": r["ts"], "usersOnline": r["users_online"]})
    nodes = []
    for a in agg_rows:
        pts = series.get(a["node_uuid"], {}).get("points", [])
        nodes.append({
            "node_uuid": a["node_uuid"],
            "node_name": a["node_name"] or "",
            "avg_online": round(a["avg_online"], 2) if a["avg_online"] is not None else 0,
            "peak_online": a["peak_online"] or 0,
            "current_online": pts[-1]["usersOnline"] if pts else 0,
            "points": pts,
        })
    nodes.sort(key=lambda n: n["avg_online"], reverse=True)
    return {"hours": hours, "nodes": nodes}


def _top_users(hours: int, account_id: Optional[str], limit: int = 20) -> dict[str, Any]:
    """Aggregate top users by their latest cumulative usage across nodes."""
    since = int(time.time()) - hours * 3600
    with _connect(account_id) as conn:
        rows = conn.execute(
            "SELECT username, MAX(total_bytes) AS total FROM node_top_users "
            "WHERE ts >= ? GROUP BY username ORDER BY total DESC LIMIT ?",
            (since, limit),
        ).fetchall()
    return {"hours": hours, "users": [{"username": r["username"], "total": r["total"]} for r in rows]}


def _migrations(hours: int, account_id: Optional[str]) -> dict[str, Any]:
    """BEST-EFFORT user migrations: for each username, the node where they had the
    highest usage per snapshot ts is their 'dominant' node; a change between two
    consecutive snapshots is a migration from→to. Approximate (cumulative usage,
    top-N only) — the UI labels it «оценка»."""
    since = int(time.time()) - hours * 3600
    with _connect(account_id) as conn:
        rows = conn.execute(
            """
            SELECT username, ts, node_uuid, total_bytes FROM node_top_users t
            WHERE ts >= ? AND total_bytes = (
                SELECT MAX(total_bytes) FROM node_top_users t2
                WHERE t2.username = t.username AND t2.ts = t.ts)
            ORDER BY username, ts ASC
            """,
            (since,),
        ).fetchall()
    # dominant node per (username, ts) → detect transitions
    counts: dict[tuple[str, str], int] = {}
    last_node: dict[str, str] = {}
    last_ts: dict[str, int] = {}
    for r in rows:
        u = r["username"]
        # collapse duplicate ts for a user (ties) — first row wins for that ts
        if last_ts.get(u) == r["ts"]:
            continue
        last_ts[u] = r["ts"]
        prev = last_node.get(u)
        if prev is not None and prev != r["node_uuid"]:
            counts[(prev, r["node_uuid"])] = counts.get((prev, r["node_uuid"]), 0) + 1
        last_node[u] = r["node_uuid"]
    migrations = [
        {"from_node": frm, "to_node": to, "count": c}
        for (frm, to), c in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    ]
    return {"hours": hours, "approximate": True, "migrations": migrations}


# ── async wrappers ─────────────────────────────────────────────

async def record_snapshot(nodes: list[dict], top: dict[str, list[dict]],
                          account_id: Optional[str] = None) -> None:
    await asyncio.to_thread(_record_snapshot, nodes, top, account_id)


async def node_load(hours: int = 24, account_id: Optional[str] = None) -> dict[str, Any]:
    return await asyncio.to_thread(_node_load, hours, account_id)


async def top_users(hours: int = 24, account_id: Optional[str] = None) -> dict[str, Any]:
    return await asyncio.to_thread(_top_users, hours, account_id)


async def migrations(hours: int = 24, account_id: Optional[str] = None) -> dict[str, Any]:
    return await asyncio.to_thread(_migrations, hours, account_id)
