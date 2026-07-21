"""Per-account API access tokens — long-lived, revocable credentials for external
integrations (the MCP container, scripts, the AI agent) so they need not carry a
browser-session JWT.

The secret is shown ONCE at creation; only an HMAC-SHA256 digest is stored
(key = settings.encryption_key). Verification runs on every request, so we use a
fast MAC rather than bcrypt — the token itself carries 256 bits of entropy
(secrets.token_urlsafe(32)), making offline brute-force infeasible.

Token format: ``nai_<account_id>_<secret>`` — the account_id is embedded so
require_account resolves it in O(1) by loading only that account's token file
(no global index → per-account isolation preserved). account_id is a uuid4 (has
'-' but no '_'), so the account_id/secret boundary is the FIRST underscore.
"""
from __future__ import annotations

import contextvars
import hashlib
import hmac
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from app.config import settings
from app.services import accounts, storage

TOKEN_PREFIX = "nai_"

# Published by require_account when the request authenticated with a readonly API
# token; require_account itself rejects mutating methods (see api/auth.py).
token_readonly: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "token_readonly", default=False
)

# last_used_at is written at most once per this many seconds per token, so we
# don't do a filesystem write on every authenticated request.
_LAST_USED_THROTTLE = 60


@dataclass
class Resolved:
    account_id: str
    token_id: str
    readonly: bool


def _hmac(secret: str) -> str:
    return hmac.new(
        settings.encryption_key.encode("utf-8"),
        secret.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _mask(rec: dict) -> dict:
    """A token row without its secret hash (safe to return to the client)."""
    return {k: v for k, v in rec.items() if k != "hash"}


def list_tokens(account_id: Optional[str] = None) -> list[dict]:
    return [_mask(t) for t in storage.load_api_tokens(account_id)]


def create(
    name: str,
    readonly: bool = False,
    expires_in: Optional[int] = None,
    account_id: Optional[str] = None,
) -> tuple[dict, str]:
    """Create a token. Returns (masked_record, plaintext_token); the plaintext is
    returned ONCE and never persisted."""
    aid = account_id or accounts.current_account.get()
    if not aid:
        raise RuntimeError("No active account in context")
    secret = secrets.token_urlsafe(32)
    token = f"{TOKEN_PREFIX}{aid}_{secret}"
    now = int(time.time())
    rec = {
        "id": uuid.uuid4().hex,
        "name": name,
        "prefix": token[: len(TOKEN_PREFIX) + 8],  # e.g. "nai_1a2b3c4d" — display hint
        "hash": _hmac(secret),
        "readonly": bool(readonly),
        "expires_at": now + int(expires_in) if expires_in else 0,
        "created_at": now,
        "last_used_at": 0,
    }
    toks = storage.load_api_tokens(aid)
    toks.append(rec)
    storage.save_api_tokens(toks, aid)
    return _mask(rec), token


def revoke(token_id: str, account_id: Optional[str] = None) -> bool:
    aid = account_id or accounts.current_account.get()
    toks = storage.load_api_tokens(aid)
    kept = [t for t in toks if t.get("id") != token_id]
    if len(kept) == len(toks):
        return False
    storage.save_api_tokens(kept, aid)
    return True


def resolve(token: str) -> Optional[Resolved]:
    """Resolve a plaintext API token → (account_id, token_id, readonly), or None on
    any failure (unknown prefix, bad format, since-deleted account, no matching
    hash, expired). Silent like accounts.account_id_from_token."""
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    aid, sep, secret = token[len(TOKEN_PREFIX):].partition("_")
    if not sep or not aid or not secret:
        return None
    if not accounts.get(aid):
        return None
    digest = _hmac(secret)
    toks = storage.load_api_tokens(aid)
    now = int(time.time())
    for rec in toks:
        if hmac.compare_digest(rec.get("hash", ""), digest):
            exp = rec.get("expires_at", 0)
            if exp and now > exp:
                return None
            _touch_last_used(aid, toks, rec, now)
            return Resolved(aid, rec["id"], bool(rec.get("readonly")))
    return None


def _touch_last_used(aid: str, toks: list, rec: dict, now: int) -> None:
    try:
        if now - int(rec.get("last_used_at", 0)) >= _LAST_USED_THROTTLE:
            rec["last_used_at"] = now
            storage.save_api_tokens(toks, aid)
    except Exception:
        pass


def mint_managed(name: str, readonly: bool = True, account_id: Optional[str] = None) -> str:
    """Rotate a managed token: revoke any existing token with this name for the
    account, issue a fresh one, return the plaintext. Used by the MCP orchestrator
    so the container carries a revocable API token instead of a raw session JWT."""
    aid = account_id or accounts.current_account.get()
    if not aid:
        raise RuntimeError("No active account in context")
    toks = [t for t in storage.load_api_tokens(aid) if t.get("name") != name]
    storage.save_api_tokens(toks, aid)
    _masked, token = create(name, readonly=readonly, account_id=aid)
    return token
