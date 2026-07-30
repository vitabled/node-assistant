"""Реестр пользователей и ролей: личность отдельно от рабочей области.

## Зачем расщепление

До этой волны «аккаунт» был ТЕНАНТОМ: своя папка данных, свои настройки, полная
невидимость для других аккаунтов. Суперпользователь с ролями подразумевает
обратное — одна установка, одни данные, разные люди с разными правами.

Поэтому понятие расщеплено:

* **рабочая область** (`workspace_id`) — ГДЕ лежат данные. Остаётся за прежним
  ContextVar `accounts.current_account`, который читают ~15 сторов, в том числе
  из фоновых лупов, где запроса нет вовсе;
* **личность** (эта запись) — КТО спрашивает и что ему можно.

Благодаря этому RBAC появился, не тронув ни один стор и ни один фоновый луп.
Это разница между волной и полугодовым рефакторингом.

## Отзыв сессий

Сессионный JWT по-прежнему без `exp` («постоянная сессия, как у Google» — было
осознанным решением), но теперь несёт `ver` = `token_version` пользователя.
Смена пароля, изменение ролей, выключение и удаление бампят версию, и прежние
токены умирают. Раньше уволенный сотрудник со сохранённым токеном продолжал
работать и после смены пароля.

## Защита от самоблокировки

Нельзя выключить, удалить или разжаловать ПОСЛЕДНЕГО суперпользователя, и роль
нельзя выдать шире собственных прав (`assert_can_grant`) — иначе владелец
`admin.users` собрал бы себе роль с `admin.infrastructure` и получил установку.
"""
from __future__ import annotations

import contextvars
import json
import threading
import time
import uuid as _uuid
from pathlib import Path
from typing import Optional

import jwt

from app.config import settings
from app.services import accounts, permissions

_JWT_ALG = "HS256"

# Реестр ГЛОБАЛЬНЫЙ (личности не принадлежат рабочей области).
_LOCK = threading.Lock()

#: Кто спрашивает в этом запросе. Парная величина к `accounts.current_account`
#: («где лежат данные»): первое — личность, второе — рабочая область. Копируется
#: в дочерние задачи так же, как и та, поэтому пайплайн деплоя видит обе.
current_user: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "current_user", default=None
)


class UserError(Exception):
    """Ошибка, пригодная для показа человеку (роутер отдаёт её текст)."""


def _path() -> Path:
    # Читаем путь при вызове, а не при импорте: тесты переставляют DATA_DIR.
    return accounts.DATA_DIR / "users.json"


def _marker() -> Path:
    return accounts.DATA_DIR / ".users_migrated"


def _read() -> dict:
    try:
        p = _path()
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("users", [])
                data.setdefault("roles", [])
                return data
    except Exception:
        # Битый файл не превращаем в «пустую установку»: это открыло бы bootstrap
        # и позволило любому создать нового владельца. Пусть падает громко.
        raise UserError("Реестр пользователей повреждён — восстановите users.json")
    return {"users": [], "roles": []}


def _write(data: dict) -> None:
    p = _path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def _now() -> int:
    return int(time.time())


def _norm_login(login: str) -> str:
    return (login or "").strip()


def _find(users: list[dict], login: str) -> Optional[dict]:
    key = _norm_login(login).lower()
    return next((u for u in users if str(u.get("login", "")).strip().lower() == key), None)


def _public(user: dict) -> dict:
    """Запись без пароля. `password_hash` не должен покидать модуль."""
    return {
        "id": user["id"],
        "login": user["login"],
        "is_superuser": bool(user.get("is_superuser")),
        "disabled": bool(user.get("disabled")),
        "role_ids": list(user.get("role_ids") or []),
        "workspace_id": user.get("workspace_id") or "",
        "legacy_workspace_id": user.get("legacy_workspace_id") or "",
        "created_at": user.get("created_at", 0),
        "last_login": user.get("last_login", 0),
    }


# ── первичная настройка ───────────────────────────────────────
def needs_bootstrap() -> bool:
    """Есть ли в установке хоть один пользователь.

    Единственный факт, который отдаёт ungated-ручка `/api/auth/state`: он и так
    виден по поведению экрана входа.
    """
    _ensure_migrated()
    return not _read()["users"]


def bootstrap(login: str, password: str) -> dict:
    """Создать первого владельца. Повторный вызов запрещён.

    ⚠️ Проверка «реестр пуст» и запись — под ОДНИМ замком: иначе два
    одновременных запроса создали бы двух суперпользователей.
    """
    login = _norm_login(login)
    if not login:
        raise UserError("Логин не может быть пустым")
    _check_password(password)
    with _LOCK:
        data = _read()
        if data["users"]:
            raise UserError("Первичная настройка уже выполнена")
        data["roles"] = [dict(r, builtin=True, created_at=_now())
                         for r in permissions.BUILTIN_ROLES]
        workspace = str(_uuid.uuid4())
        user = _new_user(login, password, workspace_id=workspace,
                         is_superuser=True, role_ids=[])
        data["users"] = [user]
        _write(data)
    # Папка рабочей области + перенос до-авторизационных корневых файлов: на
    # свежей установке их нет, но панель могли поднять до этой волны.
    accounts.data_dir(workspace)
    if not accounts._marker_path().exists():
        accounts._migrate_legacy(workspace)
        accounts._marker_path().write_text("done", encoding="utf-8")
    return _public(user)


_MIN_PASSWORD = 10


def _check_password(password: str) -> None:
    """Минимум применяется ТОЛЬКО при установке и смене пароля: поднять
    требование к уже сохранённым хешам невозможно, а выкидывать людей из панели
    за старый короткий пароль нельзя."""
    if not (password or "").strip():
        raise UserError("Пароль не может быть пустым")
    if len(password) < _MIN_PASSWORD:
        raise UserError(f"Пароль короче {_MIN_PASSWORD} символов")


def _new_user(login: str, password: str, workspace_id: str,
              is_superuser: bool = False,
              role_ids: Optional[list[str]] = None) -> dict:
    return {
        "id": str(_uuid.uuid4()),
        "login": login,
        "password_hash": accounts.hash_password(password),
        "is_superuser": bool(is_superuser),
        "disabled": False,
        "role_ids": list(role_ids or []),
        "workspace_id": workspace_id,
        "token_version": 1,
        "created_at": _now(),
        "last_login": 0,
    }


# ── миграция с прежних аккаунтов ──────────────────────────────
def _ensure_migrated() -> None:
    """Перенести `accounts.json` в `users.json` один раз.

    Аккаунт с минимальным `created_at` становится суперпользователем, и его
    каталог данных — рабочей областью установки. Остальные становятся
    пользователями этой же области с ролью «Наблюдатель», СОХРАНЯЯ пароли: люди
    входят прежними кредами.

    ⚠️ Их прежние каталоги `accounts/<id>/` НЕ сливаются и НЕ удаляются — id
    остаётся в `legacy_workspace_id`, чтобы владелец видел архив и мог его
    выгрузить. Автоматический мерж потребовал бы правил разрешения конфликтов для
    пятнадцати разных сторов; придумывать их за пользователя нельзя, а тихо
    потерять его данные нельзя тем более.
    """
    if _marker().exists() or _path().exists():
        return
    with _LOCK:
        if _marker().exists() or _path().exists():
            return
        legacy = sorted(accounts.list_accounts(), key=lambda a: a.get("created_at", 0))
        if not legacy:
            # Свежая установка: bootstrap создаст всё сам.
            _marker().write_text("empty", encoding="utf-8")
            return
        roles = [dict(r, builtin=True, created_at=_now())
                 for r in permissions.BUILTIN_ROLES]
        first, rest = legacy[0], legacy[1:]
        workspace = first["id"]
        users = [{
            "id": first["id"],
            "login": first["login"],
            "password_hash": first["password_hash"],
            "is_superuser": True,
            "disabled": False,
            "role_ids": [],
            "workspace_id": workspace,
            "token_version": 1,
            "created_at": first.get("created_at", _now()),
            "last_login": 0,
        }]
        for acc in rest:
            users.append({
                "id": acc["id"],
                "login": acc["login"],
                "password_hash": acc["password_hash"],
                "is_superuser": False,
                "disabled": False,
                "role_ids": ["viewer"],
                "workspace_id": workspace,
                # Куда смотреть за прежними данными этого человека.
                "legacy_workspace_id": acc["id"],
                "token_version": 1,
                "created_at": acc.get("created_at", _now()),
                "last_login": 0,
            })
        _write({"users": users, "roles": roles})
        _marker().write_text("migrated", encoding="utf-8")


# ── роли ──────────────────────────────────────────────────────
def list_roles() -> list[dict]:
    _ensure_migrated()
    return [dict(r) for r in _read()["roles"]]


def get_role(role_id: str) -> Optional[dict]:
    return next((r for r in list_roles() if r["id"] == role_id), None)


def create_role(name: str, description: str, perms: list[str]) -> dict:
    name = (name or "").strip()
    if not name:
        raise UserError("Название роли не может быть пустым")
    with _LOCK:
        data = _read()
        if any(r["name"].strip().lower() == name.lower() for r in data["roles"]):
            raise UserError("Роль с таким названием уже есть")
        role = {
            "id": _uuid.uuid4().hex[:12],
            "name": name[:64],
            "description": (description or "")[:200],
            "permissions": permissions.normalize(perms or []),
            "builtin": False,
            "created_at": _now(),
        }
        data["roles"].append(role)
        _write(data)
    return role


def update_role(role_id: str, name: Optional[str] = None,
                description: Optional[str] = None,
                perms: Optional[list[str]] = None) -> dict:
    with _LOCK:
        data = _read()
        role = next((r for r in data["roles"] if r["id"] == role_id), None)
        if role is None:
            raise UserError("Роль не найдена")
        if name is not None and name.strip():
            role["name"] = name.strip()[:64]
        if description is not None:
            role["description"] = description[:200]
        changed_perms = False
        if perms is not None:
            new = permissions.normalize(perms)
            changed_perms = new != role.get("permissions")
            role["permissions"] = new
        _write(data)
        if changed_perms:
            # Права роли поменялись → у её носителей поменялся набор привилегий,
            # а значит их живые токены больше не отражают действительность.
            _bump_versions([u["id"] for u in data["users"]
                            if role_id in (u.get("role_ids") or [])])
    return role


def delete_role(role_id: str) -> None:
    with _LOCK:
        data = _read()
        role = next((r for r in data["roles"] if r["id"] == role_id), None)
        if role is None:
            raise UserError("Роль не найдена")
        if role.get("builtin"):
            # Иначе установка может остаться без «Наблюдателя», и владелец начнёт
            # выдавать полные права за неимением ограниченных.
            raise UserError("Встроенную роль удалить нельзя — измените её")
        holders = [u["id"] for u in data["users"]
                   if role_id in (u.get("role_ids") or [])]
        data["roles"] = [r for r in data["roles"] if r["id"] != role_id]
        for u in data["users"]:
            u["role_ids"] = [r for r in (u.get("role_ids") or []) if r != role_id]
        _write(data)
        _bump_versions(holders)


# ── пользователи ──────────────────────────────────────────────
def list_users() -> list[dict]:
    _ensure_migrated()
    return [_public(u) for u in _read()["users"]]


def get(user_id: str) -> Optional[dict]:
    _ensure_migrated()
    u = next((x for x in _read()["users"] if x["id"] == user_id), None)
    return _public(u) if u else None


def create_user(login: str, password: str, role_ids: Optional[list[str]] = None,
                is_superuser: bool = False,
                workspace_id: Optional[str] = None) -> dict:
    login = _norm_login(login)
    if not login:
        raise UserError("Логин не может быть пустым")
    _check_password(password)
    with _LOCK:
        data = _read()
        if _find(data["users"], login):
            raise UserError("Логин уже занят")
        known = {r["id"] for r in data["roles"]}
        unknown = [r for r in (role_ids or []) if r not in known]
        if unknown:
            raise UserError(f"Неизвестные роли: {', '.join(unknown)}")
        workspace = workspace_id or _default_workspace(data)
        user = _new_user(login, password, workspace, is_superuser, role_ids)
        data["users"].append(user)
        _write(data)
    accounts.data_dir(workspace)
    return _public(user)


def _default_workspace(data: dict) -> str:
    """Рабочая область установки = область суперпользователя (он же первый)."""
    for u in data["users"]:
        if u.get("is_superuser"):
            return u.get("workspace_id") or u["id"]
    return data["users"][0].get("workspace_id") if data["users"] else str(_uuid.uuid4())


def set_roles(user_id: str, role_ids: list[str]) -> dict:
    with _LOCK:
        data = _read()
        user = next((u for u in data["users"] if u["id"] == user_id), None)
        if user is None:
            raise UserError("Пользователь не найден")
        known = {r["id"] for r in data["roles"]}
        unknown = [r for r in role_ids if r not in known]
        if unknown:
            raise UserError(f"Неизвестные роли: {', '.join(unknown)}")
        user["role_ids"] = list(dict.fromkeys(role_ids))
        user["token_version"] = int(user.get("token_version", 1)) + 1
        _write(data)
    return _public(user)


def set_password(user_id: str, password: str) -> dict:
    _check_password(password)
    with _LOCK:
        data = _read()
        user = next((u for u in data["users"] if u["id"] == user_id), None)
        if user is None:
            raise UserError("Пользователь не найден")
        user["password_hash"] = accounts.hash_password(password)
        # Смену пароля делают ровно тогда, когда доступ надо отобрать.
        user["token_version"] = int(user.get("token_version", 1)) + 1
        _write(data)
    return _public(user)


def set_disabled(user_id: str, disabled: bool) -> dict:
    with _LOCK:
        data = _read()
        user = next((u for u in data["users"] if u["id"] == user_id), None)
        if user is None:
            raise UserError("Пользователь не найден")
        if disabled:
            _assert_not_last_superuser(data, user, "выключить")
        user["disabled"] = bool(disabled)
        user["token_version"] = int(user.get("token_version", 1)) + 1
        _write(data)
    return _public(user)


def set_superuser(user_id: str, is_superuser: bool) -> dict:
    with _LOCK:
        data = _read()
        user = next((u for u in data["users"] if u["id"] == user_id), None)
        if user is None:
            raise UserError("Пользователь не найден")
        if not is_superuser:
            _assert_not_last_superuser(data, user, "разжаловать")
        user["is_superuser"] = bool(is_superuser)
        user["token_version"] = int(user.get("token_version", 1)) + 1
        _write(data)
    return _public(user)


def delete_user(user_id: str) -> None:
    with _LOCK:
        data = _read()
        user = next((u for u in data["users"] if u["id"] == user_id), None)
        if user is None:
            raise UserError("Пользователь не найден")
        _assert_not_last_superuser(data, user, "удалить")
        data["users"] = [u for u in data["users"] if u["id"] != user_id]
        _write(data)


def _assert_not_last_superuser(data: dict, user: dict, verb: str) -> None:
    if not user.get("is_superuser"):
        return
    others = [u for u in data["users"]
              if u["id"] != user["id"] and u.get("is_superuser")
              and not u.get("disabled")]
    if not others:
        raise UserError(
            f"Нельзя {verb} последнего суперпользователя — установка останется "
            "без владельца"
        )


def _bump_versions(user_ids: list[str]) -> None:
    """Обесточить живые токены перечисленных пользователей.

    Вызывается ИЗ-ПОД `_LOCK` не всегда (после `_write`), поэтому берёт данные
    заново — набор мог измениться, и перезаписывать его целиком опасно.
    """
    if not user_ids:
        return
    data = _read()
    ids = set(user_ids)
    for u in data["users"]:
        if u["id"] in ids:
            u["token_version"] = int(u.get("token_version", 1)) + 1
    _write(data)


# ── привилегии ────────────────────────────────────────────────
def permissions_of(user: dict) -> list[str]:
    """Объединение привилегий ролей. Суперпользователь получает всё.

    Права НЕ хранятся в токене — они считаются на каждый запрос. Поэтому снятая
    роль обесточивает и сессии, и API-токены немедленно, без обхода хранилища.
    """
    if user.get("is_superuser"):
        return list(permissions.ALL_PERMISSIONS)
    by_id = {r["id"]: r for r in list_roles()}
    out: set[str] = set()
    for rid in user.get("role_ids") or []:
        role = by_id.get(rid)
        if role:
            out.update(role.get("permissions") or [])
    return permissions.normalize(out)


def assert_can_grant(granter: dict, role_ids: list[str]) -> None:
    """Правило неусиления: выдать роль можно, только обладая КАЖДОЙ её привилегией.

    Без него пользователь с `admin.users` собрал бы себе роль с
    `admin.infrastructure` и получил всю установку. Суперпользователь освобождён —
    он и так обладает всем.
    """
    if granter.get("is_superuser"):
        return
    have = set(permissions_of(granter))
    by_id = {r["id"]: r for r in list_roles()}
    for rid in role_ids or []:
        role = by_id.get(rid)
        if not role:
            continue
        lack = [p for p in role.get("permissions") or [] if p not in have]
        if lack:
            raise UserError(
                f"Роль «{role['name']}» шире ваших прав — не хватает: "
                f"{', '.join(lack[:5])}"
            )


# ── аутентификация и сессии ───────────────────────────────────
def authenticate(login: str, password: str) -> Optional[dict]:
    _ensure_migrated()
    data = _read()
    user = _find(data["users"], login)
    if not user:
        # Уравниваем время ответа с известным логином.
        accounts.verify_password(password, accounts._DUMMY_HASH)
        return None
    if not accounts.verify_password(password, user["password_hash"]):
        return None
    if user.get("disabled"):
        return None
    user["last_login"] = _now()
    _write(data)
    return _public(user)


def issue_token(user_id: str) -> str:
    """Сессионный JWT без срока, но с версией — см. докстринг модуля."""
    user = get(user_id)
    ver = 1
    if user:
        raw = next((u for u in _read()["users"] if u["id"] == user_id), None)
        ver = int((raw or {}).get("token_version", 1))
    return jwt.encode({"sub": user_id, "ver": ver, "iat": _now()},
                      settings.encryption_key, algorithm=_JWT_ALG)


def resolve_token(token: str) -> Optional[dict]:
    """JWT → запись пользователя, либо None.

    Отвергает: битую подпись, неизвестного пользователя, выключенного и
    устаревшую версию токена (пароль сменили, роли поменяли, выключили).
    """
    try:
        payload = jwt.decode(token, settings.encryption_key, algorithms=[_JWT_ALG])
    except Exception:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    _ensure_migrated()
    raw = next((u for u in _read()["users"] if u["id"] == user_id), None)
    if raw is None or raw.get("disabled"):
        return None
    # Токены, выпущенные до введения версий, версии не несут — принимаем их как
    # первую, иначе миграция разлогинила бы всех до единого.
    if int(payload.get("ver", 1)) != int(raw.get("token_version", 1)):
        return None
    return _public(raw)


def workspace_of(user: dict) -> str:
    return user.get("workspace_id") or user.get("id") or ""


def list_workspaces() -> list[str]:
    """Рабочие области, по которым должны ходить ФОНОВЫЕ лупы.

    ⚠️ Существует потому, что лупы (мониторинг, правила, автобэкап, синк
    балансов) раньше обходили `accounts.list_accounts()`. На свежей установке
    Волны 13 файла `accounts.json` нет вовсе — и все они молча простаивали бы:
    ни проб доступности, ни правил, ни бэкапов.

    Возвращает УНИКАЛЬНЫЕ области живых пользователей, а не список людей: под
    одной областью их может быть сколько угодно, и полить её данными по разу на
    каждого — это N-кратная работа и N-кратные обращения к чужим API.

    Прежние области выключенных аккаунтов (`legacy_workspace_id`) сюда НЕ входят:
    это архив на диске, а не то, что надо опрашивать.
    """
    _ensure_migrated()
    out: list[str] = []
    for u in _read()["users"]:
        if u.get("disabled"):
            continue
        ws = u.get("workspace_id") or u.get("id")
        if ws and ws not in out:
            out.append(ws)
    return out
