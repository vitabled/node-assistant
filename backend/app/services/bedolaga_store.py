"""
Local store for BEDOLAGA support integration settings (SQLite, per-account).

Mirrors infra_billing_store's approach: the webapi base_url + service token are
persisted ENCRYPTED with Fernet (key derived from settings.encryption_key). This
is a deliberate, scoped override of "no third-party secrets at rest", same as
infra billing's api_tokens vault — the token is needed by a background poll loop
that has no per-request context to pull it from.

The token is never returned to the frontend in cleartext: only a masked hint.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings
from app.services import accounts

_lock = threading.Lock()


def _db_path(account_id: Optional[str] = None) -> Path:
    acc = account_id or (accounts.current_account.get() or "")
    base = Path(settings.data_dir) / "accounts" / acc if acc else Path(settings.data_dir)
    base.mkdir(parents=True, exist_ok=True)
    return base / "bedolaga.db"


def _fernet() -> Fernet:
    key_material = hashlib.sha256(settings.encryption_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key_material))


def _conn(account_id: Optional[str] = None) -> sqlite3.Connection:
    con = sqlite3.connect(_db_path(account_id), check_same_thread=False)
    con.execute(
        """CREATE TABLE IF NOT EXISTS bedolaga_config (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            base_url TEXT NOT NULL DEFAULT '',
            token_enc TEXT NOT NULL DEFAULT '',
            auth_header TEXT NOT NULL DEFAULT 'X-API-Key',
            ai_enabled INTEGER NOT NULL DEFAULT 0,
            shadow_mode INTEGER NOT NULL DEFAULT 1,
            ai_provider_base_url TEXT NOT NULL DEFAULT '',
            ai_provider_key_enc TEXT NOT NULL DEFAULT '',
            ai_model TEXT NOT NULL DEFAULT '',
            telegram_topic_chat_id TEXT NOT NULL DEFAULT '',
            telegram_topic_thread_id TEXT NOT NULL DEFAULT '',
            max_ai_replies_per_ticket INTEGER NOT NULL DEFAULT 2,
            allowed_domains TEXT NOT NULL DEFAULT '[]'
        )"""
    )
    con.commit()
    return con


def get_config(account_id: Optional[str] = None) -> dict:
    with _lock:
        con = _conn(account_id)
        row = con.execute("SELECT * FROM bedolaga_config WHERE id=1").fetchone()
        con.close()
    if not row:
        return {
            "base_url": "", "has_token": False, "token_hint": "",
            "auth_header": "X-API-Key", "ai_enabled": False, "shadow_mode": True,
            "ai_provider_base_url": "", "has_ai_provider_key": False,
            "ai_model": "", "telegram_topic_chat_id": "", "telegram_topic_thread_id": "",
            "max_ai_replies_per_ticket": 2, "allowed_domains": [],
        }
    cols = [d[0] for d in _conn(account_id).execute("SELECT * FROM bedolaga_config").description]
    d = dict(zip(cols, row))
    token_enc = d.pop("token_enc", "")
    ai_key_enc = d.pop("ai_provider_key_enc", "")
    d["has_token"] = bool(token_enc)
    d["token_hint"] = _decrypt_hint(token_enc)
    d["has_ai_provider_key"] = bool(ai_key_enc)
    d["ai_enabled"] = bool(d.get("ai_enabled"))
    d["shadow_mode"] = bool(d.get("shadow_mode"))
    try:
        d["allowed_domains"] = json.loads(d.get("allowed_domains") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["allowed_domains"] = []
    return d


def _decrypt_hint(token_enc: str) -> str:
    if not token_enc:
        return ""
    try:
        raw = _fernet().decrypt(token_enc.encode()).decode()
    except (InvalidToken, ValueError):
        return ""
    return f"…{raw[-4:]}" if len(raw) > 4 else "****"


def get_token(account_id: Optional[str] = None) -> str:
    """Cleartext token for backend use only (webapi calls). Never exposed via API."""
    with _lock:
        con = _conn(account_id)
        row = con.execute("SELECT token_enc FROM bedolaga_config WHERE id=1").fetchone()
        con.close()
    if not row or not row[0]:
        return ""
    try:
        return _fernet().decrypt(row[0].encode()).decode()
    except (InvalidToken, ValueError):
        return ""


def get_ai_provider_key(account_id: Optional[str] = None) -> str:
    with _lock:
        con = _conn(account_id)
        row = con.execute("SELECT ai_provider_key_enc FROM bedolaga_config WHERE id=1").fetchone()
        con.close()
    if not row or not row[0]:
        return ""
    try:
        return _fernet().decrypt(row[0].encode()).decode()
    except (InvalidToken, ValueError):
        return ""


def save_config(
    account_id: Optional[str] = None,
    base_url: Optional[str] = None,
    token: Optional[str] = None,
    auth_header: Optional[str] = None,
    ai_enabled: Optional[bool] = None,
    shadow_mode: Optional[bool] = None,
    ai_provider_base_url: Optional[str] = None,
    ai_provider_key: Optional[str] = None,
    ai_model: Optional[str] = None,
    telegram_topic_chat_id: Optional[str] = None,
    telegram_topic_thread_id: Optional[str] = None,
    max_ai_replies_per_ticket: Optional[int] = None,
    allowed_domains: Optional[list] = None,
) -> None:
    with _lock:
        con = _conn(account_id)
        con.execute("INSERT OR IGNORE INTO bedolaga_config (id) VALUES (1)")
        updates: dict = {}
        if base_url is not None:
            updates["base_url"] = base_url
        if token:
            updates["token_enc"] = _fernet().encrypt(token.encode()).decode()
        if auth_header is not None:
            updates["auth_header"] = auth_header
        if ai_enabled is not None:
            updates["ai_enabled"] = int(ai_enabled)
        if shadow_mode is not None:
            updates["shadow_mode"] = int(shadow_mode)
        if ai_provider_base_url is not None:
            updates["ai_provider_base_url"] = ai_provider_base_url
        if ai_provider_key:
            updates["ai_provider_key_enc"] = _fernet().encrypt(ai_provider_key.encode()).decode()
        if ai_model is not None:
            updates["ai_model"] = ai_model
        if telegram_topic_chat_id is not None:
            updates["telegram_topic_chat_id"] = telegram_topic_chat_id
        if telegram_topic_thread_id is not None:
            updates["telegram_topic_thread_id"] = telegram_topic_thread_id
        if max_ai_replies_per_ticket is not None:
            updates["max_ai_replies_per_ticket"] = max_ai_replies_per_ticket
        if allowed_domains is not None:
            updates["allowed_domains"] = json.dumps(allowed_domains)
        if updates:
            set_clause = ", ".join(f"{k}=?" for k in updates)
            con.execute(f"UPDATE bedolaga_config SET {set_clause} WHERE id=1", list(updates.values()))
        con.commit()
        con.close()
