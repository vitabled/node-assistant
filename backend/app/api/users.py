"""Управление пользователями (Настройки → Пользователи).

Все маршруты требуют `admin.users` (разметка в `services/permissions.py`).

⚠️ Здесь живут три защиты, без которых модель ролей ломается:

1. **Правило неусиления** (`users.assert_can_grant`): выдать роль можно только
   обладая КАЖДОЙ её привилегией. Иначе владелец `admin.users` собрал бы себе
   роль с `admin.infrastructure` и получил всю установку.
2. **Последний суперпользователь** не выключается, не разжаловывается и не
   удаляется (`users._assert_not_last_superuser`) — иначе установка остаётся без
   владельца, и вернуть его можно только `node-assistant reset-admin` с хоста.
3. **Флаг суперпользователя выдаёт только суперпользователь.** `admin.users` даёт
   право заводить людей, а не право создать себе равного.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import require_identity
from app.services import users

router = APIRouter(prefix="/api/users")


class CreateBody(BaseModel):
    login: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)
    role_ids: list[str] = Field(default_factory=list, max_length=20)
    is_superuser: bool = False


class RolesBody(BaseModel):
    role_ids: list[str] = Field(default_factory=list, max_length=20)


class PasswordBody(BaseModel):
    password: str = Field(..., min_length=1, max_length=256)


class FlagBody(BaseModel):
    value: bool


def _me(user_id: str) -> dict:
    return users.get(user_id) or {}


def _guard_superuser_flag(actor: dict, requested: bool) -> None:
    if requested and not actor.get("is_superuser"):
        raise HTTPException(
            403, "Выдать права суперпользователя может только суперпользователь")


@router.get("")
async def list_users(actor_id: str = Depends(require_identity)) -> dict:
    """Пользователи + сведения об архивах прежних аккаунтов.

    `legacy_workspace_id` показывается специально: при миграции с прежней модели
    данные аккаунтов 2..N остались на диске и НЕ были слиты. Не показать это —
    значит сделать вид, что их нет.
    """
    return {"users": users.list_users(),
            "roles": [{"id": r["id"], "name": r["name"]} for r in users.list_roles()]}


@router.post("", status_code=201)
async def create_user(body: CreateBody,
                      actor_id: str = Depends(require_identity)) -> dict:
    actor = _me(actor_id)
    _guard_superuser_flag(actor, body.is_superuser)
    try:
        users.assert_can_grant(actor, body.role_ids)
        return users.create_user(body.login, body.password, body.role_ids,
                                 is_superuser=body.is_superuser)
    except users.UserError as exc:
        raise HTTPException(409, str(exc))


@router.put("/{user_id}/roles")
async def set_roles(user_id: str, body: RolesBody,
                    actor_id: str = Depends(require_identity)) -> dict:
    actor = _me(actor_id)
    try:
        users.assert_can_grant(actor, body.role_ids)
        return users.set_roles(user_id, body.role_ids)
    except users.UserError as exc:
        raise HTTPException(409, str(exc))


@router.put("/{user_id}/password")
async def set_password(user_id: str, body: PasswordBody,
                       actor_id: str = Depends(require_identity)) -> dict:
    """Задать пользователю новый пароль.

    Старого не спрашиваем: владелец его не знает и знать не должен. Смена бампит
    версию токена, то есть все сессии этого человека немедленно умирают.
    """
    try:
        return users.set_password(user_id, body.password)
    except users.UserError as exc:
        raise HTTPException(422, str(exc))


@router.put("/{user_id}/disabled")
async def set_disabled(user_id: str, body: FlagBody,
                       actor_id: str = Depends(require_identity)) -> dict:
    if body.value and user_id == actor_id:
        raise HTTPException(409, "Нельзя выключить себя")
    try:
        return users.set_disabled(user_id, body.value)
    except users.UserError as exc:
        raise HTTPException(409, str(exc))


@router.put("/{user_id}/superuser")
async def set_superuser(user_id: str, body: FlagBody,
                        actor_id: str = Depends(require_identity)) -> dict:
    actor = _me(actor_id)
    if not actor.get("is_superuser"):
        raise HTTPException(403, "Только суперпользователь меняет этот флаг")
    try:
        return users.set_superuser(user_id, body.value)
    except users.UserError as exc:
        raise HTTPException(409, str(exc))


@router.delete("/{user_id}", status_code=204)
# Без аннотации возврата: у 204 тела быть не может, а `-> None` FastAPI трактует
# как модель ответа и падает на assert (идиома проекта — см. api/vault.py).
async def delete_user(user_id: str, actor_id: str = Depends(require_identity)):
    if user_id == actor_id:
        raise HTTPException(409, "Нельзя удалить себя")
    try:
        users.delete_user(user_id)
    except users.UserError as exc:
        raise HTTPException(409, str(exc))
