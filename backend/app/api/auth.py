"""Аутентификация и единственный гейт доступа.

Волна 13 переделала мультиаккаунтинг: вместо самостоятельной регистрации тенантов
— один владелец, созданный при первом запуске, и пользователи с ролями.

* `GET  /api/auth/state`     — нужна ли первичная настройка (ungated);
* `POST /api/auth/bootstrap` — создать владельца, ОДИН раз (ungated);
* `POST /api/auth/login`     — вход;
* `GET  /api/auth/me`        — кто я, какие роли и привилегии;
* `POST /api/auth/password`  — сменить СВОЙ пароль.

Регистрации больше нет: `POST /api/auth/register` удалён. Пользователей заводит
владелец в «Настройки → Пользователи».

⚠️ **`require_identity` — единственное место, где решается доступ.** Через него
проходят все 45 роутеров и 271 маршрут; привилегия для маршрута берётся из
декларативной таблицы `services/permissions.py`, а неразмеченный маршрут доступен
только суперпользователю (запрет по умолчанию).
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.services import accounts, api_tokens, audit, instances, permissions, users

router = APIRouter(prefix="/api/auth")


class Credentials(BaseModel):
    login: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class BootstrapBody(Credentials):
    # Необязательный: `BOOTSTRAP_TOKEN` в окружении по умолчанию пуст, и тогда
    # первичная настройка работает как обычный визард первого запуска.
    bootstrap_token: str = Field("", max_length=200)


class PasswordBody(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=256)
    new_password: str = Field(..., min_length=1, max_length=256)


def _session(user: dict) -> dict:
    return {
        "id": user["id"],
        "login": user["login"],
        "token": users.issue_token(user["id"]),
    }


@router.get("/state")
async def state() -> dict:
    """Нужна ли первичная настройка. Ungated и намеренно скупая на факты."""
    return {"bootstrap": users.needs_bootstrap()}


@router.post("/bootstrap", status_code=201)
async def bootstrap(body: BootstrapBody) -> dict:
    """Создать первого владельца.

    ⚠️ Ручка публичная, поэтому на свежей установке владельцем становится тот, кто
    первым до неё дошёл. Для установок, которые стоят в открытом интернете и к
    которым вернутся позже, есть `BOOTSTRAP_TOKEN` в `.env`: пусто = выключено
    (та же конвенция, что у `AGG_TOKEN`).
    """
    expected = (os.getenv("BOOTSTRAP_TOKEN") or "").strip()
    if expected and body.bootstrap_token.strip() != expected:
        raise HTTPException(403, "Неверный токен первичной настройки")
    try:
        user = users.bootstrap(body.login, body.password)
    except users.UserError as exc:
        raise HTTPException(409, str(exc))
    return _session(user)


@router.post("/login")
async def login(body: Credentials) -> dict:
    user = users.authenticate(body.login.strip(), body.password)
    if not user:
        raise HTTPException(401, "Неверный логин или пароль",
                            headers={"x-session-invalid": "1"})
    return _session(user)


_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


async def require_identity(request: Request,
                           authorization: str = Header(default=""),
                           x_instance_id: str = Header(default="")) -> str:
    """Разобрать креды, опубликовать личность и рабочую область, проверить привилегию.

    Принимает сессионный JWT или долгоживущий API-токен (`nai_…`). Возвращает id
    пользователя. `401` — кредов нет или они недействительны; `403` — не хватает
    привилегии либо readonly-токен на изменяющем методе.
    """
    token = ""
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()

    api_tokens.token_readonly.set(False)
    api_tokens.current_token_id.set(None)
    readonly_token = False
    user = None
    if token.startswith(api_tokens.TOKEN_PREFIX):
        resolved = api_tokens.resolve(token)
        if resolved:
            user = users.get(resolved.user_id)
            readonly_token = resolved.readonly
            api_tokens.current_token_id.set(resolved.token_id)
    elif token:
        user = users.resolve_token(token)

    if not user:
        # Заголовок-маркер: apiClient разлогинивает ТОЛЬКО по нему. 401 от
        # downstream (панель Remnawave с плохим токеном и т.п.) сессии не
        # касается и маркера не несёт (см. api/downstream.py).
        raise HTTPException(401, "Требуется авторизация",
                            headers={"x-session-invalid": "1"})

    if readonly_token:
        if request.method not in _SAFE_METHODS:
            raise HTTPException(403, "Токен только для чтения: запись запрещена")
        api_tokens.token_readonly.set(True)

    users.current_user.set(user)
    workspace_id = users.workspace_of(user)
    accounts.current_account.set(workspace_id)
    try:
        instances.select(x_instance_id, workspace_id)
    except KeyError:
        raise HTTPException(404, "Инстанс не найден")

    # ⚠️ Берём ШАБЛОН маршрута, а не сырой путь: Starlette к этому моменту уже
    # отмаршрутизировал запрос, поэтому шаблон канонический по построению — ни
    # `%2e%2e`, ни `//`, ни `.` в нём быть не может. Канонизировать нечего.
    route = request.scope.get("route")
    route_path = getattr(route, "path", "") or request.url.path
    needed = permissions.required(route_path, request.method)
    if needed is None:
        # Маршрут не размечен: это пробел в таблице, а не разрешение.
        if not user.get("is_superuser"):
            audit.record(user=user, method=request.method, route=route_path,
                         allowed=False, permission="(маршрут не размечен)")
            raise HTTPException(
                403, "Раздел доступен только суперпользователю")
    elif needed:
        have = users.permissions_of(user)
        lack = permissions.missing(have, needed)
        if lack:
            audit.record(user=user, method=request.method, route=route_path,
                         allowed=False, permission=",".join(lack))
            raise HTTPException(403, f"Недостаточно прав: нужна {', '.join(lack)}")
    audit.record(user=user, method=request.method, route=route_path, allowed=True,
                 permission=",".join(needed or ()))
    return user["id"]


#: Прежнее имя. Оставлено, чтобы `main.py` и ~140 тестов не переписывались одним
#: куском; новый код должен зависеть от `require_identity`.
require_account = require_identity


@router.get("/me")
async def me(user_id: str = Depends(require_identity)) -> dict:
    user = users.get(user_id) or {}
    by_id = {r["id"]: r for r in users.list_roles()}
    return {
        **user,
        "roles": [{"id": r, "name": (by_id.get(r) or {}).get("name", r)}
                  for r in user.get("role_ids") or []],
        "permissions": users.permissions_of(user),
        # Клиент прячет по этому списку разделы. ⚠️ Это КОСМЕТИКА: единственная
        # граница — сервер, см. require_identity.
    }


@router.post("/password")
async def change_password(body: PasswordBody,
                          user_id: str = Depends(require_identity)) -> dict:
    """Сменить свой пароль. Старый спрашиваем обязательно: без него украденная
    сессия меняла бы пароль и запирала владельца."""
    user = users.get(user_id) or {}
    if not users.authenticate(user.get("login", ""), body.current_password):
        raise HTTPException(403, "Текущий пароль неверен")
    try:
        users.set_password(user_id, body.new_password)
    except users.UserError as exc:
        raise HTTPException(422, str(exc))
    # Смена пароля бампит версию токена, то есть текущая сессия тоже умирает —
    # отдаём новый токен, чтобы человека не выбросило из панели.
    return {"ok": True, **_session(users.get(user_id) or user)}
