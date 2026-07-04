"""
Account registry, password hashing, JWT sessions and per-account data isolation.

Every account owns an isolated data namespace under `DATA_DIR/accounts/<id>/`
(settings.json, templates.json, traffic_rules.json, infra_billing.db). The
active account for a request is resolved from a Bearer JWT into `current_account`
(a ContextVar) by the `require_account` dependency — storage layers read that
ContextVar to pick the right directory.

Sessions are **stateless JWTs** (HS256, signed with `settings.encryption_key`),
so they survive a backend restart with nothing session-related stored at rest.
Logout / "remove from device" are purely client-side (drop the token).

The account registry itself (`DATA_DIR/accounts.json`) is GLOBAL — it holds only
{id, login, password_hash}. Passwords are bcrypt-hashed (salted); the plaintext
is never stored.
"""
from __future__ import annotations

import base64
import contextvars
import hashlib
import json
import os
import shutil
import threading
import time
import uuid as _uuid
from pathlib import Path
from typing import Optional

import bcrypt
import jwt

from app.config import settings

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

_REGISTRY_FILE = DATA_DIR / "accounts.json"
_ACCOUNTS_DIR = DATA_DIR / "accounts"

# Legacy root-level files migrated into the FIRST account created (the pre-auth
# panel wrote these directly under DATA_DIR). Originals are left as a backup.
_LEGACY_FILES = ("settings.json", "templates.json", "traffic_rules.json", "infra_billing.db")

_JWT_ALG = "HS256"

# Active account id for the current request/task. Copied into child tasks
# (asyncio.create_task) and threads (asyncio.to_thread) automatically, so the
# deploy pipeline and threaded sqlite calls resolve the right account.
current_account: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_account", default=None
)

# Serialises registry read-modify-write so concurrent registrations can't both
# pass the uniqueness check and create duplicate logins.
_lock = threading.Lock()


# ── password hashing ──────────────────────────────────────────
def _hash_password(password: str) -> str:
    # sha256+base64 first: gives bcrypt a fixed 44-byte input with no NUL bytes,
    # sidestepping bcrypt's 72-byte truncation limit for long passwords.
    pre = base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())
    return bcrypt.hashpw(pre, bcrypt.gensalt()).decode("ascii")


def _verify_password(password: str, hashed: str) -> bool:
    try:
        pre = base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())
        return bcrypt.checkpw(pre, hashed.encode("ascii"))
    except Exception:
        return False


# A throwaway hash used to spend the same bcrypt time on an unknown login as on a
# known one, so response timing doesn't reveal which logins exist.
_DUMMY_HASH = bcrypt.hashpw(b"timing-equalizer", bcrypt.gensalt()).decode("ascii")


# ── registry persistence ──────────────────────────────────────
def _read_registry() -> list[dict]:
    try:
        if _REGISTRY_FILE.exists():
            data = json.loads(_REGISTRY_FILE.read_text(encoding="utf-8"))
            return data.get("accounts", []) if isinstance(data, dict) else []
    except Exception:
        pass
    return []


def _write_registry(accounts: list[dict]) -> None:
    _REGISTRY_FILE.write_text(
        json.dumps({"accounts": accounts}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── public API ────────────────────────────────────────────────
def list_accounts() -> list[dict]:
    """All accounts (raw registry rows, including password_hash)."""
    return _read_registry()


def get(account_id: str) -> Optional[dict]:
    return next((a for a in _read_registry() if a["id"] == account_id), None)


def _find_by_login(accounts: list[dict], login: str) -> Optional[dict]:
    key = login.strip().lower()
    return next((a for a in accounts if a["login"].strip().lower() == key), None)


def data_dir(account_id: str) -> Path:
    """The isolated data directory for an account (created on demand)."""
    # Defence-in-depth: account_id comes from a JWT `sub`, and require_account
    # already rejects ids not in the registry — but never let a stray separator
    # or traversal build a path outside DATA_DIR/accounts.
    if not account_id or "/" in account_id or "\\" in account_id or ".." in account_id:
        raise ValueError("invalid account id")
    d = _ACCOUNTS_DIR / account_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_account(login: str, password: str) -> dict:
    """Create a new empty account. Raises ValueError('login_taken') if the login
    already exists. The very first account created inherits the pre-auth panel's
    legacy root data (migrated in, originals kept as backup)."""
    login = login.strip()
    with _lock:
        accounts = _read_registry()
        if _find_by_login(accounts, login):
            raise ValueError("login_taken")
        # Migrate legacy data into the first account that completes creation.
        # Gated on a marker file (not len(accounts)==0) so a crash between the
        # registry write and the migration can't permanently orphan the data —
        # the next account creation finishes it.
        do_migrate = not _marker_path().exists()
        account = {
            "id": str(_uuid.uuid4()),
            "login": login,
            "password_hash": _hash_password(password),
            "created_at": int(time.time()),
        }
        accounts.append(account)
        _write_registry(accounts)
        data_dir(account["id"])
        if do_migrate:
            _migrate_legacy(account["id"])
            _marker_path().write_text("done", encoding="utf-8")
    return {"id": account["id"], "login": account["login"]}


def authenticate(login: str, password: str) -> Optional[dict]:
    """Return {id, login} on valid credentials, else None."""
    account = _find_by_login(_read_registry(), login)
    if not account:
        _verify_password(password, _DUMMY_HASH)  # equalise timing vs known login
        return None
    if not _verify_password(password, account["password_hash"]):
        return None
    return {"id": account["id"], "login": account["login"]}


def issue_token(account_id: str) -> str:
    """Sign a stateless, non-expiring session JWT for the account."""
    payload = {"sub": account_id, "iat": int(time.time())}
    return jwt.encode(payload, settings.encryption_key, algorithm=_JWT_ALG)


def account_id_from_token(token: str) -> Optional[str]:
    """Decode a session JWT → account id, or None if invalid."""
    try:
        payload = jwt.decode(token, settings.encryption_key, algorithms=[_JWT_ALG])
    except Exception:
        return None
    return payload.get("sub")


def _marker_path() -> Path:
    """Marker file recording that legacy migration has run (read at call time so
    tests that repoint DATA_DIR see the right path)."""
    return DATA_DIR / ".legacy_migrated"


def _migrate_legacy(account_id: str) -> None:
    """Copy pre-auth root-level data into the first account's namespace. Copies
    (not moves) so the originals remain under DATA_DIR as a backup."""
    dest = data_dir(account_id)
    for name in _LEGACY_FILES:
        src = DATA_DIR / name
        target = dest / name
        if src.exists() and not target.exists():
            try:
                shutil.copy2(src, target)
            except Exception:
                pass
