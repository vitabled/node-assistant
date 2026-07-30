"""Per-account API access tokens — long-lived, revocable credentials for external
integrations (the MCP container, scripts, the AI agent) so they need not carry a
browser-session JWT.

The secret is shown ONCE at creation; only an HMAC-SHA256 digest is stored
(key = settings.encryption_key). Verification runs on every request, so we use a
fast MAC rather than bcrypt — the token itself carries 256 bits of entropy
(secrets.token_urlsafe(32)), making offline brute-force infeasible.

Token format: ``nai_<user_id>_<secret>`` — the id is embedded so the resolver
works in O(1) by loading only one token file (no global index). The id is a uuid4
(has '-' but no '_'), so the id/secret boundary is the FIRST underscore.

⚠️ **Волна 13: токен принадлежит ПОЛЬЗОВАТЕЛЮ, а не рабочей области.** Привилегии
токена не хранятся — они берутся у владельца в момент запроса, поэтому снятая
роль обесточивает и его токены. Флаг `readonly` при этом только СУЖАЕТ права до
безопасных методов, никогда не расширяет.

⚠️ Записи БЕЗ поля `user_id` (выпущенные до Волны 13) не резолвятся никогда, и
это не недоделка. Идентификатор первого прежнего аккаунта после миграции стал
одновременно id суперпользователя и id рабочей области — старый токен
резолвился бы и получил бы ПОЛНЫЕ права владельца вместо прав своего тенанта.
Такие токены надо перевыпустить.
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
    account_id: str      # рабочая область владельца (для сторов)
    token_id: str
    readonly: bool
    user_id: str = ""    # владелец: у него и спрашиваются привилегии


def _hmac(secret: str) -> str:
    return hmac.new(
        settings.encryption_key.encode("utf-8"),
        secret.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _mask(rec: dict) -> dict:
    """A token row without its secret hash (safe to return to the client)."""
    return {k: v for k, v in rec.items() if k != "hash"}


def _current_user_id() -> str:
    from app.services import users

    user = users.current_user.get()
    return (user or {}).get("id") or ""


def list_tokens(account_id: Optional[str] = None,
                user_id: Optional[str] = None) -> list[dict]:
    """Токены ВЛАДЕЛЬЦА. Чужие не показываем: под одной рабочей областью файл
    общий, и без фильтра оператор видел бы токены администратора."""
    uid = user_id or _current_user_id()
    return [_mask(t) for t in storage.load_api_tokens(account_id)
            if t.get("user_id") and (not uid or t.get("user_id") == uid)]


def create(
    name: str,
    readonly: bool = False,
    expires_in: Optional[int] = None,
    account_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> tuple[dict, str]:
    """Create a token. Returns (masked_record, plaintext_token); the plaintext is
    returned ONCE and never persisted."""
    aid = account_id or accounts.current_account.get()
    if not aid:
        raise RuntimeError("No active account in context")
    uid = user_id or _current_user_id()
    if not uid:
        raise RuntimeError("No active user in context")
    secret = secrets.token_urlsafe(32)
    token = f"{TOKEN_PREFIX}{uid}_{secret}"
    now = int(time.time())
    rec = {
        "id": uuid.uuid4().hex,
        "name": name,
        "user_id": uid,
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


def revoke(token_id: str, account_id: Optional[str] = None,
           user_id: Optional[str] = None) -> bool:
    aid = account_id or accounts.current_account.get()
    uid = user_id or _current_user_id()
    toks = storage.load_api_tokens(aid)
    # Отзывать можно только свой токен: id токена приходит из URL, а файл под
    # одной рабочей областью общий.
    kept = [t for t in toks
            if not (t.get("id") == token_id and (not uid or t.get("user_id") == uid))]
    if len(kept) == len(toks):
        return False
    storage.save_api_tokens(kept, aid)
    return True


def resolve(token: str) -> Optional[Resolved]:
    """Resolve a plaintext API token → Resolved, or None on any failure (unknown
    prefix, bad format, since-deleted or disabled user, no matching hash, expired).
    Silent like users.resolve_token."""
    from app.services import users

    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    uid, sep, secret = token[len(TOKEN_PREFIX):].partition("_")
    if not sep or not uid or not secret:
        return None
    owner = users.get(uid)
    if owner is None or owner.get("disabled"):
        return None
    aid = users.workspace_of(owner)
    digest = _hmac(secret)
    toks = storage.load_api_tokens(aid)
    now = int(time.time())
    for rec in toks:
        # ⚠️ Сверяем и владельца: без этого токен, выпущенный до Волны 13 (без
        # `user_id`), резолвился бы под id первого прежнего аккаунта, который
        # стал суперпользователем, — и получил бы полные права вместо своих.
        if rec.get("user_id") != uid:
            continue
        if hmac.compare_digest(rec.get("hash", ""), digest):
            exp = rec.get("expires_at", 0)
            if exp and now > exp:
                return None
            _touch_last_used(aid, toks, rec, now)
            return Resolved(aid, rec["id"], bool(rec.get("readonly")), uid)
    return None


def _touch_last_used(aid: str, toks: list, rec: dict, now: int) -> None:
    try:
        if now - int(rec.get("last_used_at", 0)) >= _LAST_USED_THROTTLE:
            rec["last_used_at"] = now
            storage.save_api_tokens(toks, aid)
    except Exception:
        pass


def mint_managed(name: str, readonly: bool = True, account_id: Optional[str] = None,
                 user_id: Optional[str] = None) -> str:
    """Rotate a managed token: revoke any existing token with this name, issue a
    fresh one, return the plaintext. Used by the MCP orchestrator so the container
    carries a revocable API token instead of a raw session JWT.

    ⚠️ Токен привязывается к ПОЛЬЗОВАТЕЛЮ, включившему интеграцию: иначе контейнер
    получил бы права рабочей области, а не своего хозяина."""
    aid = account_id or accounts.current_account.get()
    uid = user_id or _current_user_id()
    if not aid:
        raise RuntimeError("No active account in context")
    if not uid:
        raise RuntimeError("No active user in context")
    toks = [t for t in storage.load_api_tokens(aid)
            if not (t.get("name") == name and t.get("user_id") == uid)]
    storage.save_api_tokens(toks, aid)
    _masked, token = create(name, readonly=readonly, account_id=aid, user_id=uid)
    return token
