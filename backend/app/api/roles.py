"""Управление ролями (Настройки → Роли). Требует `admin.roles`.

Роль — набор привилегий; пользователь получает объединение привилегий своих ролей.
Каталог привилегий (`GET /api/roles/catalogue`) отдаётся клиенту, чтобы редактор
рисовал матрицу «домен × действие» по реальному списку, а не по своей копии,
которая отстанет от бэкенда.

⚠️ Правка привилегий роли бампит `token_version` у её носителей
(`users.update_role`): иначе человек продолжал бы работать с правами, которых у
него уже нет — сессия-то живая.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import require_identity
from app.services import permissions, users

router = APIRouter(prefix="/api/roles")


class RoleBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    description: str = Field("", max_length=200)
    permissions: list[str] = Field(default_factory=list, max_length=200)


class RolePatch(BaseModel):
    name: str | None = Field(None, max_length=64)
    description: str | None = Field(None, max_length=200)
    permissions: list[str] | None = Field(None, max_length=200)


@router.get("/catalogue")
async def catalogue(_actor: str = Depends(require_identity)) -> dict:
    """Домены с осмысленными действиями + особые привилегии.

    Объявлен ДО `/{role_id}`, иначе параметризованный путь перехватит слово
    `catalogue` (та же ловушка, что у `/api/vault/schemas` и `/api/hostings/tags`).
    """
    return permissions.catalogue()


@router.get("")
async def list_roles(_actor: str = Depends(require_identity)) -> dict:
    roles = users.list_roles()
    holders: dict[str, int] = {r["id"]: 0 for r in roles}
    for u in users.list_users():
        for rid in u.get("role_ids") or []:
            if rid in holders:
                holders[rid] += 1
    return {"roles": [dict(r, holders=holders.get(r["id"], 0)) for r in roles]}


@router.post("", status_code=201)
async def create_role(body: RoleBody,
                      actor_id: str = Depends(require_identity)) -> dict:
    actor = users.get(actor_id) or {}
    _guard_not_wider(actor, body.permissions)
    try:
        return users.create_role(body.name, body.description, body.permissions)
    except users.UserError as exc:
        raise HTTPException(409, str(exc))


@router.patch("/{role_id}")
async def update_role(role_id: str, body: RolePatch,
                      actor_id: str = Depends(require_identity)) -> dict:
    actor = users.get(actor_id) or {}
    if body.permissions is not None:
        _guard_not_wider(actor, body.permissions)
    try:
        return users.update_role(role_id, body.name, body.description,
                                 body.permissions)
    except users.UserError as exc:
        raise HTTPException(409, str(exc))


@router.delete("/{role_id}", status_code=204)
async def delete_role(role_id: str, _actor: str = Depends(require_identity)):
    try:
        users.delete_role(role_id)
    except users.UserError as exc:
        raise HTTPException(409, str(exc))


def _guard_not_wider(actor: dict, perms: list[str]) -> None:
    """Нельзя собрать роль шире собственных прав.

    Без этого `admin.roles` был бы полным доступом с задержкой на один запрос:
    создал роль со всеми привилегиями, надел на себя — и всё. Проверять надо
    здесь, а не только при выдаче роли, потому что выдать её себе может и другой
    человек.
    """
    if actor.get("is_superuser"):
        return
    have = set(users.permissions_of(actor))
    lack = [p for p in permissions.normalize(perms) if p not in have]
    if lack:
        raise HTTPException(
            403, f"Роль шире ваших прав — не хватает: {', '.join(lack[:5])}")
