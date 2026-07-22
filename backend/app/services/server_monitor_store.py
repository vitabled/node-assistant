"""Per-account SQLite store for the «Server uptime» monitor (Wave-3 Plan A Ф2).

A lightweight availability monitor independent of the xray-checker: each tracked
server is probed by IP (TCP connect + ICMP) and recorded as up/slow/down. Servers
are either MANUAL (operator-added: name/country/ip/port/note) or DEPLOYED
(auto-synced from the browser's `deploy_jobs`, read-only). Analytics mirror
`metrics_store` (bars / 30-day uptime / incidents / per-node uptime) so the
frontend can reuse the status-page components; per-account isolation mirrors
`user_stats_store` (explicit `account_id` — the poller has no request context).
"""
from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
import uuid
from itertools import groupby
from pathlib import Path
from typing import Any, Optional

from app.services import accounts

# Keep ~35 days of samples (matches the checker metrics retention → 30-day uptime).
_RETENTION_SECONDS = 35 * 24 * 3600
SLOW_MS = 800            # latency ≥ this → "slow" tick (same threshold as metrics_store)
DEFAULT_PORT = 443       # default TCP probe port; falls back to 22


def _tick_status(online: int, latency_ms: int) -> str:
    if not online:
        return "down"
    if latency_ms is not None and latency_ms >= 0 and latency_ms >= SLOW_MS:
        return "slow"
    return "up"


def _dir(account_id: Optional[str]) -> Path:
    aid = account_id or accounts.current_account.get()
    if not aid:
        raise RuntimeError("No active account in context")
    return accounts.data_dir(aid)


def _db_path(account_id: Optional[str]) -> Path:
    return _dir(account_id) / "server_monitor.db"


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
                CREATE TABLE IF NOT EXISTS servers (
                    id         TEXT PRIMARY KEY,
                    name       TEXT NOT NULL DEFAULT '',
                    country    TEXT NOT NULL DEFAULT '',
                    ip         TEXT NOT NULL,
                    port       INTEGER NOT NULL DEFAULT 443,
                    note       TEXT NOT NULL DEFAULT '',
                    source     TEXT NOT NULL DEFAULT 'manual',  -- manual | deployed
                    created_at INTEGER NOT NULL DEFAULT 0,
                    hidden     INTEGER NOT NULL DEFAULT 0        -- убран с глаз, но продолжает пробиться
                );
                CREATE INDEX IF NOT EXISTS idx_srv_source ON servers(source);

                CREATE TABLE IF NOT EXISTS server_samples (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts         INTEGER NOT NULL,
                    server_id  TEXT    NOT NULL,
                    online     INTEGER NOT NULL,      -- 0/1
                    latency_ms INTEGER NOT NULL       -- -1 when offline/unknown
                );
                CREATE INDEX IF NOT EXISTS idx_ssamp_ts ON server_samples(ts);
                CREATE INDEX IF NOT EXISTS idx_ssamp_sid_ts ON server_samples(server_id, ts);
                """
            )
            # Миграция для БД, созданных до Волны 6: CREATE TABLE IF NOT EXISTS
            # выше не добавит колонку в уже существующую таблицу. Тот же приём,
            # что применялся в metrics_store для checker_id (CLAUDE.md §4b).
            cols = {r[1] for r in conn.execute("PRAGMA table_info(servers)")}
            if "hidden" not in cols:
                conn.execute("ALTER TABLE servers ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")
        _initialised.add(key)


def _connect(account_id: Optional[str]) -> sqlite3.Connection:
    path = _db_path(account_id)
    _ensure_schema(path)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_server(r: sqlite3.Row) -> dict[str, Any]:
    keys = r.keys()
    return {
        "id": r["id"], "name": r["name"], "country": r["country"],
        "ip": r["ip"], "port": r["port"], "note": r["note"],
        "source": r["source"], "created_at": r["created_at"],
        # БД, созданная до Волны 6, колонки не имеет — читаем защитно.
        "hidden": bool(r["hidden"]) if "hidden" in keys else False,
    }


def _set_hidden(sid: str, hidden: bool, account_id: Optional[str]) -> Optional[dict[str, Any]]:
    """Скрыть/показать сервер — ДЛЯ ЛЮБОГО source.

    Отдельно от `_update_server`, который намеренно ограничен `source='manual'`:
    скрывать нужно в первую очередь deployed-строки, которые иначе убрать с глаз
    невозможно вообще (их возвращает каждый ре-синк из deploy_jobs).
    """
    with _connect(account_id) as conn:
        conn.execute("UPDATE servers SET hidden = ? WHERE id = ?", (1 if hidden else 0, sid))
        r = conn.execute("SELECT * FROM servers WHERE id = ?", (sid,)).fetchone()
    return _row_to_server(r) if r else None


# ── server registry (CRUD) ─────────────────────────────────────

def _list_servers(account_id: Optional[str]) -> list[dict[str, Any]]:
    with _connect(account_id) as conn:
        rows = conn.execute(
            "SELECT * FROM servers ORDER BY country, name, ip"
        ).fetchall()
    return [_row_to_server(r) for r in rows]


def _add_server(name: str, country: str, ip: str, port: int, note: str,
                source: str, account_id: Optional[str]) -> dict[str, Any]:
    sid = uuid.uuid4().hex[:12]
    row = (sid, name.strip(), country.strip(), ip.strip(), int(port or DEFAULT_PORT),
           note.strip(), source, int(time.time()))
    with _connect(account_id) as conn:
        conn.execute(
            "INSERT INTO servers (id, name, country, ip, port, note, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", row,
        )
    return {"id": sid, "name": row[1], "country": row[2], "ip": row[3],
            "port": row[4], "note": row[5], "source": source, "created_at": row[7],
            "hidden": False}


def _update_server(sid: str, fields: dict[str, Any],
                   account_id: Optional[str]) -> Optional[dict[str, Any]]:
    allowed = {"name", "country", "ip", "port", "note"}
    sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not sets:
        with _connect(account_id) as conn:
            r = conn.execute("SELECT * FROM servers WHERE id = ?", (sid,)).fetchone()
        return _row_to_server(r) if r else None
    cols = ", ".join(f"{k} = ?" for k in sets)
    with _connect(account_id) as conn:
        # Only manual servers are editable; deployed ones are re-synced from
        # deploy_jobs and would be overwritten anyway.
        cur = conn.execute(
            f"UPDATE servers SET {cols} WHERE id = ? AND source = 'manual'",
            (*sets.values(), sid),
        )
        if cur.rowcount == 0:
            r = conn.execute("SELECT * FROM servers WHERE id = ?", (sid,)).fetchone()
            return _row_to_server(r) if r else None
        r = conn.execute("SELECT * FROM servers WHERE id = ?", (sid,)).fetchone()
    return _row_to_server(r) if r else None


def _delete_server(sid: str, account_id: Optional[str]) -> bool:
    with _connect(account_id) as conn:
        cur = conn.execute("DELETE FROM servers WHERE id = ?", (sid,))
        conn.execute("DELETE FROM server_samples WHERE server_id = ?", (sid,))
    return cur.rowcount > 0


def _sync_deployed(items: list[dict[str, Any]], account_id: Optional[str]) -> int:
    """Upsert deployed nodes (source='deployed') by IP, and drop deployed rows no
    longer present. Manual servers are untouched. items: [{name,country,ip,port}]."""
    now = int(time.time())
    keep_ips = {str(i.get("ip", "")).strip() for i in items if str(i.get("ip", "")).strip()}
    with _connect(account_id) as conn:
        existing = {
            r["ip"]: r["id"]
            for r in conn.execute("SELECT id, ip FROM servers WHERE source = 'deployed'").fetchall()
        }
        for i in items:
            ip = str(i.get("ip", "")).strip()
            if not ip:
                continue
            name = str(i.get("name", "") or "").strip()
            country = str(i.get("country", "") or "").strip()
            port = int(i.get("port") or DEFAULT_PORT)
            if ip in existing:
                conn.execute(
                    "UPDATE servers SET name = ?, country = ?, port = ? WHERE id = ?",
                    (name, country, port, existing[ip]),
                )
            else:
                conn.execute(
                    "INSERT INTO servers (id, name, country, ip, port, note, source, created_at) "
                    "VALUES (?, ?, ?, ?, ?, '', 'deployed', ?)",
                    (uuid.uuid4().hex[:12], name, country, ip, port, now),
                )
        # Drop deployed rows whose IP is gone from the browser's deploy_jobs.
        stale = [sid for ip, sid in existing.items() if ip not in keep_ips]
        for sid in stale:
            conn.execute("DELETE FROM servers WHERE id = ?", (sid,))
            conn.execute("DELETE FROM server_samples WHERE server_id = ?", (sid,))
    return len(keep_ips)


# ── samples (writes) ───────────────────────────────────────────

def _record_samples(samples: list[dict[str, Any]], account_id: Optional[str]) -> None:
    """samples: [{server_id, online(bool/int), latency_ms}]."""
    if not samples:
        return
    ts = int(time.time())
    rows = [
        (ts, str(s.get("server_id", "")), 1 if s.get("online") else 0,
         int(s.get("latency_ms", -1)))
        for s in samples if s.get("server_id")
    ]
    if not rows:
        return
    with _connect(account_id) as conn:
        conn.executemany(
            "INSERT INTO server_samples (ts, server_id, online, latency_ms) "
            "VALUES (?, ?, ?, ?)", rows,
        )
        conn.execute("DELETE FROM server_samples WHERE ts < ?", (ts - _RETENTION_SECONDS,))


# ── analytics (reads) — mirror metrics_store shapes ────────────

def _get_bars(n: int, account_id: Optional[str]) -> dict[str, list[dict[str, Any]]]:
    """{ server_id: [{ts, status}] } — the last n samples per server."""
    with _connect(account_id) as conn:
        rows = conn.execute(
            "SELECT server_id, ts, online, latency_ms FROM server_samples "
            "ORDER BY server_id, ts ASC"
        ).fetchall()
    out: dict[str, list[dict[str, Any]]] = {}
    for sid, grp in groupby(rows, key=lambda r: r["server_id"]):
        ticks = [{"ts": r["ts"], "status": _tick_status(r["online"], r["latency_ms"])} for r in grp]
        out[sid] = ticks[-n:]
    return out


def _latest(account_id: Optional[str]) -> dict[str, dict[str, Any]]:
    """{ server_id: {online, latency_ms, ts} } — the most recent sample per server.
    (SQLite returns the bare columns from the MAX(ts) row.)"""
    with _connect(account_id) as conn:
        rows = conn.execute(
            "SELECT server_id, online, latency_ms, MAX(ts) AS ts FROM server_samples "
            "GROUP BY server_id"
        ).fetchall()
    return {
        r["server_id"]: {"online": bool(r["online"]), "latency_ms": r["latency_ms"], "ts": r["ts"]}
        for r in rows
    }


def _uptime_30d(account_id: Optional[str]) -> dict[str, Any]:
    since = int(time.time()) - 30 * 24 * 3600
    with _connect(account_id) as conn:
        per = {
            r["server_id"]: round(r["up"], 2) if r["up"] is not None else None
            for r in conn.execute(
                "SELECT server_id, AVG(online) * 100.0 AS up FROM server_samples "
                "WHERE ts >= ? GROUP BY server_id", (since,),
            ).fetchall()
        }
        g = conn.execute(
            "SELECT AVG(online) * 100.0 AS up FROM server_samples WHERE ts >= ?", (since,),
        ).fetchone()
    return {"global": round(g["up"], 2) if g and g["up"] is not None else None, "per_node": per}


def _node_uptime(hours: int, account_id: Optional[str]) -> dict[str, dict[str, Any]]:
    since = int(time.time()) - hours * 3600
    with _connect(account_id) as conn:
        rows = conn.execute(
            "SELECT server_id, AVG(online) * 100.0 AS uptime_pct, COUNT(*) AS checks, "
            "MAX(CASE WHEN online = 1 THEN ts END) AS last_seen FROM server_samples "
            "WHERE ts >= ? GROUP BY server_id", (since,),
        ).fetchall()
    return {
        r["server_id"]: {
            "uptime_pct": round(r["uptime_pct"], 1) if r["uptime_pct"] is not None else None,
            "checks": r["checks"], "last_seen": r["last_seen"],
        }
        for r in rows
    }


def _incidents(days: int, account_id: Optional[str]) -> list[dict[str, Any]]:
    since = int(time.time()) - days * 86400
    now = int(time.time())
    with _connect(account_id) as conn:
        rows = conn.execute(
            "SELECT s.server_id AS sid, srv.name AS name, srv.country AS country, "
            "s.ts AS ts, s.online AS online FROM server_samples s "
            "LEFT JOIN servers srv ON srv.id = s.server_id "
            "WHERE s.ts >= ? ORDER BY s.server_id, s.ts ASC", (since,),
        ).fetchall()
    incidents: list[dict[str, Any]] = []
    for sid, grp in groupby(rows, key=lambda r: r["sid"]):
        down_start: Optional[int] = None
        name = group = ""
        for r in grp:
            name, group = (r["name"] or ""), (r["country"] or "")
            if r["online"] == 0 and down_start is None:
                down_start = r["ts"]
            elif r["online"] == 1 and down_start is not None:
                incidents.append({
                    "stableId": sid, "name": name, "group": group,
                    "start": down_start, "end": r["ts"], "durationSec": r["ts"] - down_start,
                    "reason": "Сервер недоступен (нет ответа по IP)", "ongoing": False,
                })
                down_start = None
        if down_start is not None:
            incidents.append({
                "stableId": sid, "name": name, "group": group,
                "start": down_start, "end": now, "durationSec": now - down_start,
                "reason": "Сервер недоступен (нет ответа по IP)", "ongoing": True,
            })
    incidents.sort(key=lambda x: x["start"], reverse=True)
    return incidents


# ── async wrappers ─────────────────────────────────────────────

async def list_servers(account_id: Optional[str] = None) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_list_servers, account_id)


async def add_server(name: str, country: str, ip: str, port: int, note: str,
                     source: str = "manual", account_id: Optional[str] = None) -> dict[str, Any]:
    return await asyncio.to_thread(_add_server, name, country, ip, port, note, source, account_id)


async def update_server(sid: str, fields: dict[str, Any],
                        account_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    return await asyncio.to_thread(_update_server, sid, fields, account_id)


async def set_hidden(sid: str, hidden: bool, account_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    return await asyncio.to_thread(_set_hidden, sid, hidden, account_id)


async def delete_server(sid: str, account_id: Optional[str] = None) -> bool:
    return await asyncio.to_thread(_delete_server, sid, account_id)


async def sync_deployed(items: list[dict[str, Any]], account_id: Optional[str] = None) -> int:
    return await asyncio.to_thread(_sync_deployed, items, account_id)


async def record_samples(samples: list[dict[str, Any]], account_id: Optional[str] = None) -> None:
    await asyncio.to_thread(_record_samples, samples, account_id)


async def get_latest(account_id: Optional[str] = None) -> dict[str, dict[str, Any]]:
    return await asyncio.to_thread(_latest, account_id)


async def get_bars(n: int = 30, account_id: Optional[str] = None) -> dict[str, list[dict[str, Any]]]:
    return await asyncio.to_thread(_get_bars, n, account_id)


async def get_uptime_30d(account_id: Optional[str] = None) -> dict[str, Any]:
    return await asyncio.to_thread(_uptime_30d, account_id)


async def get_node_uptime(hours: int = 24, account_id: Optional[str] = None) -> dict[str, dict[str, Any]]:
    return await asyncio.to_thread(_node_uptime, hours, account_id)


async def get_incidents(days: int = 7, account_id: Optional[str] = None) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_incidents, days, account_id)
