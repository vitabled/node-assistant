"""
Per-account store for the rules engine.

Two artefacts per account under `DATA_DIR/accounts/<id>/`:
  - `rules.json`        — the rule definitions (JSON, mirrors storage.py).
  - `rules_secrets.db`  — a Fernet-encrypted vault (SQLite) for action secrets
    (e.g. a Telegram bot-token). A rule action stores a `token_ref` (a secret id),
    NEVER the plaintext token.

⚠️ SECURITY OVERRIDE (module-scoped, same as infra_billing_store): the background
rules loop has no per-request creds, so action secrets MUST persist. They are
encrypted with Fernet (key = SHA-256 of `settings.encryption_key`) and are never
returned to the client — the API masks them. A weak `ENCRYPTION_KEY` in prod
weakens this vault; document + set a strong key.

Every function takes an optional explicit `account_id` for background callers
(the loop/webhook), falling back to the `current_account` ContextVar for
request-scoped calls — the same pattern as storage.py / infra_billing_store.py.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import sqlite3
import threading
import time
import uuid as _uuid
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings
from app.services import accounts, storage


# ── ids / time ────────────────────────────────────────────────
def _id() -> str:
    return _uuid.uuid4().hex[:12]


def _now() -> int:
    return int(time.time())


# ── Fernet vault (secrets at rest) ────────────────────────────
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


MASK = "••••"


def _dir(account_id: Optional[str]) -> Path:
    aid = account_id or accounts.current_account.get()
    if not aid:
        raise RuntimeError("No active account in context")
    return accounts.data_dir(aid)


def _secrets_path(account_id: Optional[str]) -> Path:
    return _dir(account_id) / "rules_secrets.db"


_initialised: set[str] = set()
_init_lock = threading.Lock()


def _connect(account_id: Optional[str]) -> sqlite3.Connection:
    path = _secrets_path(account_id)
    key = str(path)
    if key not in _initialised:
        with _init_lock:
            if key not in _initialised:
                with sqlite3.connect(path, timeout=10) as conn:
                    conn.execute(
                        "CREATE TABLE IF NOT EXISTS secrets ("
                        "id TEXT PRIMARY KEY, secret_enc BLOB NOT NULL, created_at INTEGER NOT NULL)"
                    )
                _initialised.add(key)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def put_secret(plaintext: str, account_id: Optional[str] = None) -> str:
    """Encrypt + store a secret, returning its opaque ref (safe to expose)."""
    ref = _id()
    with _connect(account_id) as conn:
        conn.execute(
            "INSERT INTO secrets (id, secret_enc, created_at) VALUES (?, ?, ?)",
            (ref, _encrypt(plaintext), _now()),
        )
    return ref


def read_secret(ref: str, account_id: Optional[str] = None) -> Optional[str]:
    """Decrypt a stored secret by ref (None if missing/undecryptable)."""
    if not ref:
        return None
    with _connect(account_id) as conn:
        row = conn.execute(
            "SELECT secret_enc FROM secrets WHERE id=?", (ref,)
        ).fetchone()
    return _decrypt(row["secret_enc"]) if row else None


def delete_secret(ref: str, account_id: Optional[str] = None) -> None:
    if not ref:
        return
    with _connect(account_id) as conn:
        conn.execute("DELETE FROM secrets WHERE id=?", (ref,))


# ── rule CRUD (JSON) ──────────────────────────────────────────
def list_rules(account_id: Optional[str] = None) -> list[dict]:
    return storage.load_rules(account_id)


def get_rule(rule_id: str, account_id: Optional[str] = None) -> Optional[dict]:
    return next((r for r in list_rules(account_id) if r.get("id") == rule_id), None)


def add_rule(rule: dict, account_id: Optional[str] = None) -> dict:
    rules = list_rules(account_id)
    rule = {**rule}
    rule.setdefault("id", _id())
    rule.setdefault("last_fired_at", None)
    rules.append(rule)
    storage.save_rules(rules, account_id)
    return rule


def update_rule(
    rule_id: str, patch: dict, account_id: Optional[str] = None
) -> Optional[dict]:
    rules = list_rules(account_id)
    found = next((r for r in rules if r.get("id") == rule_id), None)
    if not found:
        return None
    # GC secrets orphaned when the actions list is replaced (a token_ref present in
    # the old actions but absent from the new ones is no longer referenced).
    if "actions" in patch:
        old_refs = _token_refs(found.get("actions"))
        new_refs = _token_refs(patch.get("actions"))
        for ref in old_refs - new_refs:
            delete_secret(ref, account_id)
    found.update(patch)
    storage.save_rules(rules, account_id)
    return found


def mark_fired(
    rule_id: str, scope: str, now: int, account_id: Optional[str] = None
) -> None:
    """Record a fire for a cooldown scope (per-node for xray_down, "" global for
    webhook/cron). Read-modify-write so sequential fires in one tick (node A then
    node B) accumulate distinct scope keys instead of clobbering each other."""
    rules = list_rules(account_id)
    found = next((r for r in rules if r.get("id") == rule_id), None)
    if not found:
        return
    fired_map = dict(found.get("last_fired") or {})
    fired_map[scope] = now
    found["last_fired"] = fired_map
    found["last_fired_at"] = now  # legacy scalar (display / "" bucket fallback)
    storage.save_rules(rules, account_id)


def remove_rule(rule_id: str, account_id: Optional[str] = None) -> bool:
    rules = list_rules(account_id)
    kept = [r for r in rules if r.get("id") != rule_id]
    if len(kept) == len(rules):
        return False
    # GC secrets owned only by this rule's actions.
    for a in _telegram_actions(next(r for r in rules if r.get("id") == rule_id)):
        ref = (a.get("params") or {}).get("token_ref")
        if ref:
            delete_secret(ref, account_id)
    storage.save_rules(kept, account_id)
    return True


def _telegram_actions(rule: dict) -> list[dict]:
    return [a for a in (rule.get("actions") or []) if a.get("type") == "telegram"]


def _token_refs(actions: Optional[list[dict]]) -> set[str]:
    """Set of non-empty token_refs referenced by a list of actions."""
    refs: set[str] = set()
    for a in actions or []:
        ref = (a.get("params") or {}).get("token_ref")
        if ref:
            refs.add(ref)
    return refs


# ── async wrappers (blocking sqlite/json in a thread) ─────────
async def a_put_secret(plaintext: str, account_id: Optional[str] = None) -> str:
    return await asyncio.to_thread(put_secret, plaintext, account_id)


async def a_read_secret(ref: str, account_id: Optional[str] = None) -> Optional[str]:
    return await asyncio.to_thread(read_secret, ref, account_id)
