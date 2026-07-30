"""Test bootstrap: point DATA_DIR at a fresh temp dir BEFORE any app module
imports (accounts/storage/infra_billing_store read it at module load).
"""
import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="ni_test_")

# ─────────────────────────────────────────────────────────────────
# Совместимость с ~140 тестовыми файлами, которые заводят себе изолированный
# аккаунт вызовом `POST /api/auth/register`.
#
# Волна 13 убрала регистрацию из продукта: пользователей теперь создаёт владелец,
# а первого — визард первого запуска. Но переписывать 140 файлов ради этого нельзя:
# они проверяют СВОИ предметы (изоляцию сторов, парсеры, пайплайн), а не модель
# доступа, и правка расползлась бы по всему набору.
#
# Поэтому маршрут остаётся, но ТОЛЬКО в тестовом процессе, и создаёт он не
# «аккаунт», а **пользователя с собственной рабочей областью и правами
# суперпользователя**:
#
#   * своя рабочая область — потому что именно её проверяют тесты изоляции
#     («данные аккаунта A не видны из B»); под RBAC это по-прежнему верно, просто
#     называется иначе;
#   * суперпользователь — потому что тесты предметных модулей не про привилегии;
#     проверки самой модели доступа живут в `test_permissions.py` и создают там
#     пользователей с конкретными ролями явно.
#
# Запись собирается низкоуровневым `users._new_user`, а не `users.create_user`,
# ровно по одной причине: у создания пароля есть минимум в 10 символов, а тесты
# передают «pw». Обходить проверку через monkeypatch на весь прогон было бы хуже —
# тогда её нельзя было бы проверить там, где она и нужна.
# ─────────────────────────────────────────────────────────────────
TEST_ONLY_ROUTES = ("/api/auth/register",)


def _install_register_shim() -> None:
    from fastapi import HTTPException
    from pydantic import BaseModel, Field

    from app.main import app
    from app.services import accounts, users

    class _Credentials(BaseModel):
        login: str = Field(..., min_length=1, max_length=64)
        password: str = Field(..., min_length=1, max_length=256)

    @app.post("/api/auth/register", status_code=201, include_in_schema=False)
    async def _test_register(body: _Credentials) -> dict:  # noqa: D401
        login = body.login.strip()
        if not login:
            raise HTTPException(422, "Логин не может быть пустым")
        if not body.password.strip():
            raise HTTPException(422, "Пароль не может быть пустым")
        with users._LOCK:
            data = users._read()
            if users._find(data["users"], login):
                raise HTTPException(409, "Логин уже занят")
            if not data["roles"]:
                from app.services import permissions

                data["roles"] = [dict(r, builtin=True, created_at=users._now())
                                 for r in permissions.BUILTIN_ROLES]
            user = users._new_user(login, body.password,
                                  workspace_id="", is_superuser=True)
            # Своя рабочая область = собственный id: так каталог данных совпадает
            # с прежним `accounts/<account_id>/`, и тесты изоляции не меняются.
            user["workspace_id"] = user["id"]
            data["users"].append(user)
            users._write(data)
        accounts.data_dir(user["id"])
        return {"id": user["id"], "login": user["login"],
                "token": users.issue_token(user["id"])}


_install_register_shim()
