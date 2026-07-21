"""
Local store for the Infra-billing subsystem (SQLite, stdlib — no new pip dep).

Remnawave's InfraBillingController only exposes providers/nodes/history. The full
8-tab billing product (projects, services, payments, settings, api-tokens,
dashboard) has NO Remnawave backing, so node-assistant owns that data here.

⚠️ SECURITY OVERRIDE (user-approved for this module): hosting API secrets in the
`api_tokens` vault ARE persisted, **encrypted with Fernet** (key derived from
`settings.encryption_key`). This intentionally overrides the project-wide rule
"third-party secrets must not be stored at rest" — scoped to this module only.
Secrets are NEVER returned to the frontend: DTOs expose only a masked hint.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sqlite3
import threading
import time
import uuid as _uuid
from pathlib import Path
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings
from app.services import accounts


def _db_path(account_id: Optional[str] = None) -> Path:
    """Per-account infra-billing DB path.

    Mirrors `user_stats_store`/`server_monitor_store`: an explicit `account_id`
    wins, otherwise it falls back to the request's `current_account` ContextVar.
    The explicit form exists so callers WITHOUT a request context (a background
    loop, or this subsystem running in a separate process) can reach the store.
    """
    aid = account_id or accounts.current_account.get()
    if not aid:
        raise RuntimeError("No active account in context")
    return accounts.data_dir(aid) / "infra_billing.db"


# ── Encryption (Fernet key derived from the app encryption key) ──
def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.encryption_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode())


def _decrypt(token: bytes) -> Optional[str]:
    try:
        return _fernet().decrypt(token).decode()
    except (InvalidToken, Exception):
        return None


def _mask(secret: str) -> str:
    """sel-api-d3f9************ — keep a short prefix, mask the rest."""
    if not secret:
        return ""
    keep = min(8, max(2, len(secret) // 3))
    return secret[:keep] + "*" * max(4, len(secret) - keep)


def _now() -> int:
    return int(time.time())


def _id() -> str:
    return str(_uuid.uuid4())


# DB paths whose schema has already been ensured this process (per account).
_initialised: set[str] = set()
_init_lock = threading.Lock()


def _connect(account_id: Optional[str] = None) -> sqlite3.Connection:
    path = _db_path(account_id)
    _ensure_schema(path)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


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
            -- Local financial metadata for Remnawave providers.
            CREATE TABLE IF NOT EXISTS provider_meta (
                provider_uuid         TEXT PRIMARY KEY,
                balance               REAL DEFAULT 0,
                currency              TEXT DEFAULT 'RUB',
                low_balance_threshold REAL DEFAULT 0,
                api_token_id          TEXT DEFAULT '',
                status                TEXT DEFAULT 'active'   -- active | auth_error | unknown
            );
            -- Local monthly cost per Remnawave billing node.
            CREATE TABLE IF NOT EXISTS node_meta (
                billing_node_uuid TEXT PRIMARY KEY,
                monthly_cost      REAL DEFAULT 0
            );
            -- Projects: logical grouping of nodes.
            CREATE TABLE IF NOT EXISTS projects (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                description TEXT DEFAULT '',
                node_uuids  TEXT DEFAULT '[]',   -- json array of deploy-node uuids
                created_at  INTEGER NOT NULL
            );
            -- Services: billable line items.
            CREATE TABLE IF NOT EXISTS services (
                id             TEXT PRIMARY KEY,
                name           TEXT NOT NULL,
                kind           TEXT DEFAULT 'vps',   -- vps|dedicated|storage|domain|ip|other
                node_uuid      TEXT DEFAULT '',
                provider_uuid  TEXT DEFAULT '',
                project_id     TEXT DEFAULT '',
                billing_type   TEXT DEFAULT 'fixed', -- fixed | hourly
                cost           REAL DEFAULT 0,
                next_billing_at TEXT DEFAULT '',
                created_at     INTEGER NOT NULL
            );
            -- Payments ledger.
            CREATE TABLE IF NOT EXISTS payments (
                id            TEXT PRIMARY KEY,
                ts            INTEGER NOT NULL,
                provider_uuid TEXT DEFAULT '',
                project_id    TEXT DEFAULT '',
                type          TEXT DEFAULT 'charge', -- charge | topup | adjustment
                amount        REAL DEFAULT 0,
                currency      TEXT DEFAULT 'RUB',
                status        TEXT DEFAULT 'success',-- success | pending | error
                note          TEXT DEFAULT ''
            );
            -- Encrypted hosting API tokens (vault).
            CREATE TABLE IF NOT EXISTS api_tokens (
                id            TEXT PRIMARY KEY,
                name          TEXT NOT NULL,
                provider_kind TEXT DEFAULT 'generic',
                secret_enc    BLOB NOT NULL,
                created_at    INTEGER NOT NULL
            );
            -- Singleton key/value billing settings.
            CREATE TABLE IF NOT EXISTS billing_settings (
                k TEXT PRIMARY KEY,
                v TEXT
            );
            """
            )
        _initialised.add(key)


# ── Generic helpers ───────────────────────────────────────────
def _rows(sql: str, args: tuple = (), account_id: Optional[str] = None) -> list[dict]:
    with _connect(account_id) as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]

def _one(sql: str, args: tuple = (), account_id: Optional[str] = None) -> Optional[dict]:
    with _connect(account_id) as conn:
        r = conn.execute(sql, args).fetchone()
        return dict(r) if r else None

def _exec(sql: str, args: tuple = (), account_id: Optional[str] = None) -> None:
    with _connect(account_id) as conn:
        conn.execute(sql, args)


# ── Provider meta ─────────────────────────────────────────────
# Every private below takes `account_id` explicitly (see `_db_path`). It is the
# LAST positional on plain functions and the SECOND on the `**fields` ones, so
# it can never be swallowed by the kwargs bag.
def _provider_meta_all(account_id: Optional[str] = None) -> dict[str, dict[str, Any]]:
    return {r["provider_uuid"]: r for r in _rows("SELECT * FROM provider_meta", (), account_id)}

def _upsert_provider_meta(uuid: str, account_id: Optional[str] = None, **fields: Any) -> None:
    with _connect(account_id) as conn:
        if not conn.execute("SELECT 1 FROM provider_meta WHERE provider_uuid=?", (uuid,)).fetchone():
            conn.execute("INSERT INTO provider_meta (provider_uuid) VALUES (?)", (uuid,))
        sets, args = [], []
        for k, v in fields.items():
            if v is not None:
                sets.append(f"{k}=?"); args.append(v)
        if sets:
            args.append(uuid)
            conn.execute(f"UPDATE provider_meta SET {', '.join(sets)} WHERE provider_uuid=?", args)

def _delete_provider_meta(uuid: str, account_id: Optional[str] = None) -> None:
    _exec("DELETE FROM provider_meta WHERE provider_uuid=?", (uuid,), account_id)


# ── Node meta ─────────────────────────────────────────────────
def _node_meta_all(account_id: Optional[str] = None) -> dict[str, float]:
    return {r["billing_node_uuid"]: r["monthly_cost"]
            for r in _rows("SELECT * FROM node_meta", (), account_id)}

def _set_node_cost(uuid: str, cost: float, account_id: Optional[str] = None) -> None:
    _exec("INSERT INTO node_meta (billing_node_uuid, monthly_cost) VALUES (?, ?) "
          "ON CONFLICT(billing_node_uuid) DO UPDATE SET monthly_cost=excluded.monthly_cost",
          (uuid, cost), account_id)

def _delete_node_meta(uuid: str, account_id: Optional[str] = None) -> None:
    _exec("DELETE FROM node_meta WHERE billing_node_uuid=?", (uuid,), account_id)


# ── Projects ──────────────────────────────────────────────────
def _projects(account_id: Optional[str] = None) -> list[dict]:
    out = _rows("SELECT * FROM projects ORDER BY created_at DESC", (), account_id)
    for p in out:
        p["node_uuids"] = json.loads(p.get("node_uuids") or "[]")
    return out

def _create_project(name: str, description: str, node_uuids: list[str],
                    account_id: Optional[str] = None) -> str:
    pid = _id()
    _exec("INSERT INTO projects (id, name, description, node_uuids, created_at) VALUES (?,?,?,?,?)",
          (pid, name, description, json.dumps(node_uuids), _now()), account_id)
    return pid

def _update_project(pid: str, account_id: Optional[str] = None, **f: Any) -> None:
    sets, args = [], []
    for k, v in f.items():
        if v is None: continue
        sets.append(f"{k}=?")
        args.append(json.dumps(v) if k == "node_uuids" else v)
    if sets:
        args.append(pid)
        _exec(f"UPDATE projects SET {', '.join(sets)} WHERE id=?", tuple(args), account_id)

def _delete_project(pid: str, account_id: Optional[str] = None) -> None:
    _exec("DELETE FROM projects WHERE id=?", (pid,), account_id)


# ── Services ──────────────────────────────────────────────────
def _services(account_id: Optional[str] = None) -> list[dict]:
    return _rows("SELECT * FROM services ORDER BY created_at DESC", (), account_id)

def _create_service(account_id: Optional[str] = None, **f: Any) -> str:
    sid = _id()
    _exec("INSERT INTO services (id, name, kind, node_uuid, provider_uuid, project_id, billing_type, cost, next_billing_at, created_at) "
          "VALUES (?,?,?,?,?,?,?,?,?,?)",
          (sid, f["name"], f.get("kind", "vps"), f.get("node_uuid", ""), f.get("provider_uuid", ""),
           f.get("project_id", ""), f.get("billing_type", "fixed"), f.get("cost", 0),
           f.get("next_billing_at", ""), _now()), account_id)
    return sid

def _update_service(sid: str, account_id: Optional[str] = None, **f: Any) -> None:
    sets, args = [], []
    for k, v in f.items():
        if v is not None:
            sets.append(f"{k}=?"); args.append(v)
    if sets:
        args.append(sid)
        _exec(f"UPDATE services SET {', '.join(sets)} WHERE id=?", tuple(args), account_id)

def _delete_service(sid: str, account_id: Optional[str] = None) -> None:
    _exec("DELETE FROM services WHERE id=?", (sid,), account_id)


# ── Payments ──────────────────────────────────────────────────
def _payments(account_id: Optional[str] = None) -> list[dict]:
    return _rows("SELECT * FROM payments ORDER BY ts DESC", (), account_id)

def _create_payment(account_id: Optional[str] = None, **f: Any) -> str:
    pid = _id()
    _exec("INSERT INTO payments (id, ts, provider_uuid, project_id, type, amount, currency, status, note) "
          "VALUES (?,?,?,?,?,?,?,?,?)",
          (pid, f.get("ts") or _now(), f.get("provider_uuid", ""), f.get("project_id", ""),
           f.get("type", "charge"), f.get("amount", 0), f.get("currency", "RUB"),
           f.get("status", "success"), f.get("note", "")), account_id)
    return pid

def _delete_payment(pid: str, account_id: Optional[str] = None) -> None:
    _exec("DELETE FROM payments WHERE id=?", (pid,), account_id)


# ── API tokens (encrypted) ────────────────────────────────────
def _api_tokens(account_id: Optional[str] = None) -> list[dict]:
    """Returns masked token metadata — never the plaintext secret."""
    out = []
    for r in _rows("SELECT id, name, provider_kind, secret_enc, created_at FROM api_tokens ORDER BY created_at DESC",
                   (), account_id):
        secret = _decrypt(r["secret_enc"]) or ""
        out.append({"id": r["id"], "name": r["name"], "providerKind": r["provider_kind"],
                    "masked": _mask(secret), "createdAt": r["created_at"]})
    return out

def _create_api_token(name: str, provider_kind: str, secret: str,
                      account_id: Optional[str] = None) -> str:
    tid = _id()
    _exec("INSERT INTO api_tokens (id, name, provider_kind, secret_enc, created_at) VALUES (?,?,?,?,?)",
          (tid, name, provider_kind, _encrypt(secret), _now()), account_id)
    return tid

def _get_api_token_secret(tid: str, account_id: Optional[str] = None) -> Optional[str]:
    r = _one("SELECT secret_enc FROM api_tokens WHERE id=?", (tid,), account_id)
    return _decrypt(r["secret_enc"]) if r else None

def _delete_api_token(tid: str, account_id: Optional[str] = None) -> None:
    _exec("DELETE FROM api_tokens WHERE id=?", (tid,), account_id)


# ── Settings (singleton k/v) ──────────────────────────────────
_SETTINGS_DEFAULTS = {
    "base_currency": "RUB",
    "fx_rates": json.dumps({"RUB": 1.0, "USD": 90.0, "EUR": 98.0}),  # 1 unit = X base
    "low_balance_threshold": "1000",
    "refresh_interval": "daily",   # hourly | daily
}

def _get_settings(account_id: Optional[str] = None) -> dict[str, Any]:
    rows = {r["k"]: r["v"] for r in _rows("SELECT * FROM billing_settings", (), account_id)}
    merged = {**_SETTINGS_DEFAULTS, **rows}
    return {
        "baseCurrency": merged["base_currency"],
        "fxRates": json.loads(merged["fx_rates"]),
        "lowBalanceThreshold": float(merged["low_balance_threshold"] or 0),
        "refreshInterval": merged["refresh_interval"],
    }

def _put_settings(base_currency=None, fx_rates=None, low_balance_threshold=None,
                  refresh_interval=None, account_id: Optional[str] = None) -> None:
    def _set(k, v):
        _exec("INSERT INTO billing_settings (k, v) VALUES (?, ?) "
              "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v), account_id)
    if base_currency is not None:         _set("base_currency", base_currency)
    if fx_rates is not None:              _set("fx_rates", json.dumps(fx_rates))
    if low_balance_threshold is not None: _set("low_balance_threshold", str(low_balance_threshold))
    if refresh_interval is not None:      _set("refresh_interval", refresh_interval)


# ── Async wrappers (all blocking sqlite calls run in a thread) ──
# `account_id=None` keeps every request-scoped call site unchanged (the
# ContextVar is copied into the `to_thread` worker); background/out-of-process
# callers pass it explicitly.
async def provider_meta_all(account_id=None):      return await asyncio.to_thread(_provider_meta_all, account_id)
async def upsert_provider_meta(uuid, account_id=None, **f):
    await asyncio.to_thread(lambda: _upsert_provider_meta(uuid, account_id, **f))
async def delete_provider_meta(uuid, account_id=None):   await asyncio.to_thread(_delete_provider_meta, uuid, account_id)
async def node_meta_all(account_id=None):          return await asyncio.to_thread(_node_meta_all, account_id)
async def set_node_cost(uuid, cost, account_id=None):    await asyncio.to_thread(_set_node_cost, uuid, cost, account_id)
async def delete_node_meta(uuid, account_id=None):       await asyncio.to_thread(_delete_node_meta, uuid, account_id)

async def projects(account_id=None):               return await asyncio.to_thread(_projects, account_id)
async def create_project(name, desc, nodes, account_id=None):
    return await asyncio.to_thread(_create_project, name, desc, nodes, account_id)
async def update_project(pid, account_id=None, **f):
    await asyncio.to_thread(lambda: _update_project(pid, account_id, **f))
async def delete_project(pid, account_id=None):    await asyncio.to_thread(_delete_project, pid, account_id)

async def services(account_id=None):               return await asyncio.to_thread(_services, account_id)
async def create_service(account_id=None, **f):    return await asyncio.to_thread(lambda: _create_service(account_id, **f))
async def update_service(sid, account_id=None, **f):
    await asyncio.to_thread(lambda: _update_service(sid, account_id, **f))
async def delete_service(sid, account_id=None):    await asyncio.to_thread(_delete_service, sid, account_id)

async def payments(account_id=None):               return await asyncio.to_thread(_payments, account_id)
async def create_payment(account_id=None, **f):    return await asyncio.to_thread(lambda: _create_payment(account_id, **f))
async def delete_payment(pid, account_id=None):    await asyncio.to_thread(_delete_payment, pid, account_id)

async def api_tokens(account_id=None):             return await asyncio.to_thread(_api_tokens, account_id)
async def create_api_token(name, kind, secret, account_id=None):
    return await asyncio.to_thread(_create_api_token, name, kind, secret, account_id)
async def get_api_token_secret(tid, account_id=None):    return await asyncio.to_thread(_get_api_token_secret, tid, account_id)
async def delete_api_token(tid, account_id=None):        await asyncio.to_thread(_delete_api_token, tid, account_id)

async def get_settings(account_id=None):           return await asyncio.to_thread(_get_settings, account_id)
async def put_settings(account_id=None, **f):      await asyncio.to_thread(lambda: _put_settings(account_id=account_id, **f))
