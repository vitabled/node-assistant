"""
Authentication API: register, login, and the `require_account` dependency that
gates every data route.

Register creates a new empty account and immediately logs in (returns a token).
Login verifies credentials and returns a token. Tokens are stateless JWTs; the
client stores them per-account on the device (see the frontend auth store).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.services import accounts, api_tokens

router = APIRouter(prefix="/api/auth")


class Credentials(BaseModel):
    login: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


def _account_response(account: dict) -> dict:
    return {
        "id": account["id"],
        "login": account["login"],
        "token": accounts.issue_token(account["id"]),
    }


@router.post("/register", status_code=201)
async def register(body: Credentials):
    login = body.login.strip()
    if not login:
        raise HTTPException(422, "Логин не может быть пустым")
    if not body.password.strip():
        raise HTTPException(422, "Пароль не может быть пустым")
    try:
        account = accounts.create_account(login, body.password)
    except ValueError:
        raise HTTPException(409, "Логин уже занят")
    return _account_response(account)


@router.post("/login")
async def login(body: Credentials):
    account = accounts.authenticate(body.login.strip(), body.password)
    if not account:
        raise HTTPException(401, "Неверный логин или пароль")
    return _account_response(account)


_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


async def require_account(request: Request, authorization: str = Header(default="")) -> str:
    """FastAPI dependency: resolve the Bearer credential into the active account id
    and publish it on the `current_account` ContextVar. Accepts either a session
    JWT or a long-lived API token (``nai_...``, see services.api_tokens). Raises
    401 when the credential is missing, malformed, invalid, or names an account
    that no longer exists; 403 when a readonly API token is used on a mutating
    method."""
    token = ""
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()

    api_tokens.token_readonly.set(False)
    if token.startswith(api_tokens.TOKEN_PREFIX):
        resolved = api_tokens.resolve(token)
        account_id = resolved.account_id if resolved else None
        if resolved and resolved.readonly:
            if request.method not in _SAFE_METHODS:
                raise HTTPException(403, "Токен только для чтения: запись запрещена")
            api_tokens.token_readonly.set(True)
    else:
        account_id = accounts.account_id_from_token(token) if token else None

    if not account_id or not accounts.get(account_id):
        raise HTTPException(401, "Требуется авторизация")
    accounts.current_account.set(account_id)
    return account_id


@router.get("/me")
async def me(account_id: str = Depends(require_account)):
    account = accounts.get(account_id)
    return {"id": account["id"], "login": account["login"]}
