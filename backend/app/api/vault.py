"""«Хранилище» routes (Wave-9 Plan A Ф1) — per-account CRUD over the secret vault.

Gated by require_account (wired in main.py). The list/create/update responses are
the store's public shape: field names + a masked hint, never a secret value. A
plaintext value leaves the backend only through two explicit routes — `reveal`
and, for SSH keys, `download`.
"""
from __future__ import annotations

import re
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from app.services import vault_store as store

router = APIRouter(prefix="/api/vault")

# Mirrors store.KINDS (kept explicit for pydantic/type-checkers; test_vault.py
# asserts the two stay in sync).
VaultKind = Literal["api_key", "ssh_password", "ssh_key", "login", "provider_creds", "note"]


class VaultEntryBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=store.MAX_NAME)
    kind: VaultKind
    resource: str = Field(default="", max_length=store.MAX_RESOURCE)
    username: str = ""
    note: str = ""
    tags: list[str] = Field(default_factory=list)
    fields: dict[str, str] = Field(default_factory=dict)


class VaultEntryUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=store.MAX_NAME)
    kind: Optional[VaultKind] = None
    resource: Optional[str] = Field(default=None, max_length=store.MAX_RESOURCE)
    username: Optional[str] = None
    note: Optional[str] = None
    tags: Optional[list[str]] = None
    # None → keep the stored secret (renaming an entry must not require the client
    # to hold, and re-send, the plaintext).
    fields: Optional[dict[str, str]] = None


# Field sets per kind — the frontend builds its form from this instead of
# hardcoding six shapes. `provider_creds` is intentionally empty: Plan C's
# provider registry contributes those schemas later.
_SCHEMAS: list[dict[str, Any]] = [
    {"kind": "api_key", "title": "API-ключ", "fields": [
        {"key": "token", "label": "Токен", "kind": "password", "required": True},
    ]},
    {"kind": "ssh_password", "title": "SSH-пароль", "fields": [
        {"key": "password", "label": "Пароль", "kind": "password", "required": True},
    ]},
    {"kind": "ssh_key", "title": "SSH-ключ", "fields": [
        {"key": "private_key", "label": "Приватный ключ", "kind": "textarea", "required": True},
        {"key": "passphrase", "label": "Пароль ключа", "kind": "password", "required": False},
    ]},
    {"kind": "login", "title": "Логин и пароль", "fields": [
        {"key": "username", "label": "Пользователь", "kind": "text", "required": True},
        {"key": "password", "label": "Пароль", "kind": "password", "required": True},
    ]},
    {"kind": "provider_creds", "title": "Доступ к хостинг-провайдеру", "fields": []},
    {"kind": "note", "title": "Заметка", "fields": [
        {"key": "text", "label": "Текст", "kind": "textarea", "required": True},
    ]},
]


@router.get("")
async def list_entries() -> list[dict[str, Any]]:
    return await store.a_list_entries()


# Declared before "/{entry_id}/…" so a literal path segment can never be captured
# as an entry id.
@router.get("/schemas")
async def list_schemas() -> list[dict[str, Any]]:
    return _SCHEMAS


@router.post("", status_code=201)
async def create_entry(body: VaultEntryBody) -> dict[str, Any]:
    try:
        return await store.a_create_entry(
            name=body.name, kind=body.kind, resource=body.resource,
            username=body.username, note=body.note, tags=body.tags, fields=body.fields,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.put("/{entry_id}")
async def update_entry(entry_id: str, body: VaultEntryUpdate) -> dict[str, Any]:
    try:
        updated = await store.a_update_entry(
            entry_id, name=body.name, kind=body.kind, resource=body.resource,
            username=body.username, note=body.note, tags=body.tags, fields=body.fields,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if updated is None:
        raise HTTPException(404, "Запись не найдена")
    return updated


@router.delete("/{entry_id}", status_code=204)
async def delete_entry(entry_id: str):
    if not await store.a_delete_entry(entry_id):
        raise HTTPException(404, "Запись не найдена")


@router.post("/{entry_id}/reveal")
async def reveal_entry(entry_id: str) -> dict[str, Any]:
    """POST, not GET: an id in a URL lands in nginx access logs and browser
    history, a body does not — and GET is exposed to prefetch/speculative loads."""
    fields = await store.a_read_fields(entry_id)
    if fields is None:
        raise HTTPException(404, "Запись не найдена или секрет не расшифровывается")
    if not fields:
        # An entry can legitimately carry no secret — most often it came back from
        # an import, where the ciphertext is stripped. Say so instead of opening an
        # empty reveal panel the user would read as a bug.
        raise HTTPException(404, "У записи нет сохранённого секрета — введите его заново")
    await store.a_touch_revealed(entry_id)
    return {"fields": fields}


@router.get("/{entry_id}/download")
async def download_key(entry_id: str) -> Response:
    entry = await store.a_get_entry(entry_id)
    if entry is None:
        raise HTTPException(404, "Запись не найдена")
    if entry["kind"] != "ssh_key":
        raise HTTPException(400, "Скачивание доступно только для SSH-ключей")
    fields = await store.a_read_fields(entry_id)
    private_key = (fields or {}).get("private_key") or ""
    if not private_key:
        raise HTTPException(404, "Приватный ключ недоступен")
    # Opaque download (§11h pattern): a private key is never a document our origin
    # renders — octet-stream + attachment + nosniff.
    safe = re.sub(r"[^A-Za-z0-9._-]", "", entry.get("name") or "") or "key"
    return Response(
        content=private_key,
        media_type="application/octet-stream",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="{safe}.pem"',
        },
    )
