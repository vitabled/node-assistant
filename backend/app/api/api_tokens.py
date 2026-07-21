"""API tokens router — issue/list/revoke per-account API access tokens.

All routes are gated by require_account (wired in main.py). The plaintext token is
returned only once, at creation; the store keeps only its HMAC hash.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import api_tokens

router = APIRouter(prefix="/api/api-tokens")


class CreateBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    readonly: bool = False
    expires_in_days: Optional[int] = Field(default=None, ge=1, le=3650)


@router.get("")
async def list_tokens():
    return api_tokens.list_tokens()


@router.post("", status_code=201)
async def create_token(body: CreateBody):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Имя не может быть пустым")
    expires_in = body.expires_in_days * 86400 if body.expires_in_days else None
    masked, plaintext = api_tokens.create(name, readonly=body.readonly, expires_in=expires_in)
    # `token` (plaintext) is returned ONCE — the client must copy it now.
    return {**masked, "token": plaintext}


@router.delete("/{token_id}")
async def revoke_token(token_id: str):
    if not api_tokens.revoke(token_id):
        raise HTTPException(404, "Токен не найден")
    return {"ok": True}
