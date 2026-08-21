"""Модель доступа: полнота разметки маршрутов и поведение единственного гейта.

Это граница безопасности, поэтому тесты утверждают СМЫСЛ, а не форму: наборы
привилегий и список маршрутов будут расти, и снимок вида «привилегий ровно 63»
пришлось бы править каждой волной, а сломался бы он не там, где ошибка.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

# Список тестовых маршрутов берём из conftest, а не дублируем здесь: иначе копия
# отстанет от совместимостного слоя, и тест покрытия начнёт врать.
from conftest import TEST_ONLY_ROUTES

from app.main import app
from app.services import api_tokens, permissions, users

client = TestClient(app)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _uniq(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ── покрытие разметки ─────────────────────────────────────────
def test_every_api_route_is_mapped():
    """Каждый маршрут /api/* размечен в `permissions.RULES` (или в `EXEMPT`).

    ⚠️ Главный тест этого файла. Гейт в проекте один, и привилегию он берёт из
    таблицы; маршрут, которого в таблице нет, работает ТОЛЬКО у
    суперпользователя. То есть новый роутер, добавленный и не размеченный, тихо
    ломается у всех остальных — и заметить это на глаз невозможно: 289 пар
    «путь + метод». Поэтому полноту таблицы держит тест, а не внимательность.

    Падение означает ровно одно: допишите свой маршрут в `RULES` (или, если он
    действительно вне модели, в `EXEMPT` — с объяснением, почему).
    """
    unmapped = []
    for route in app.routes:
        path = getattr(route, "path", "") or ""
        if not path.startswith("/api"):
            continue
        if path.startswith(TEST_ONLY_ROUTES):
            # Совместимостный shim из conftest — в продукте его нет.
            continue
        for method in sorted(getattr(route, "methods", None) or []):
            if method in ("HEAD", "OPTIONS"):
                continue
            if permissions.required(path, method) is None:
                unmapped.append(f"{method} {path}")
    assert not unmapped, (
        "маршруты без привилегии (доступны только суперпользователю): "
        + ", ".join(unmapped)
    )


# ── непротиворечивость каталога ───────────────────────────────
def test_rules_reference_only_existing_permissions():
    """Опечатка в правиле («setting.edit») сделала бы маршрут недоступным всем,
    кроме суперпользователя, и молча."""
    known = set(permissions.ALL_PERMISSIONS)
    unknown = []
    for prefix, table in permissions.RULES:
        for method, perm in table.items():
            needed = (perm,) if isinstance(perm, str) else tuple(perm)
            unknown += [f"{prefix} {method} → {p}" for p in needed if p not in known]
    assert not unknown


def test_builtin_roles_reference_only_existing_permissions():
    ids = {r["id"] for r in permissions.BUILTIN_ROLES}
    assert {"admin", "operator", "finance", "viewer"} <= ids
    known = set(permissions.ALL_PERMISSIONS)
    for role in permissions.BUILTIN_ROLES:
        assert set(role["permissions"]) <= known, role["id"]


def test_domains_declare_only_known_actions():
    for domain, actions in permissions.DOMAINS.items():
        assert actions, domain
        assert set(actions) <= set(permissions.ACTIONS), domain


def test_normalize_drops_unknown_and_dedupes():
    out = permissions.normalize(
        ["hostings.view", "hostings.view", "no.such", "vault.reveal", ""]
    )
    assert out == permissions.normalize(["vault.reveal", "hostings.view"])
    assert "no.such" not in out
    assert permissions.normalize([]) == []


def test_builtin_roles_are_ordered_by_breadth():
    """Смысловое утверждение вместо счётчиков: администратор шире оператора,
    оператор шире наблюдателя. Так тест не ломается от каждой новой привилегии,
    но ловит роль, случайно собранную «наоборот»."""
    by_id = {r["id"]: set(r["permissions"]) for r in permissions.BUILTIN_ROLES}
    assert by_id["viewer"] < by_id["operator"] < by_id["admin"]
    # Наблюдатель — только просмотр (личное не считаем: свой пароль и своя тема
    # есть у любого, иначе человек не сменит украденный пароль).
    assert all(p.endswith(".view") for p in by_id["viewer"]
               if not p.startswith("account."))
    # Секреты и покупки не даёт ни одна роль, кроме администратора.
    for rid in ("operator", "finance", "viewer"):
        assert "vault.reveal" not in by_id[rid]
        assert "billing.purchase" not in by_id[rid]


def test_finance_can_see_billing_but_not_spend():
    finance = {r["id"]: r for r in permissions.BUILTIN_ROLES}["finance"]
    perms = set(finance["permissions"])
    assert {"billing.view", "billing.create", "billing.edit"} <= perms
    assert "billing.purchase" not in perms


# ── порядок правил: специфичное раньше общего ─────────────────
# Пары взяты РЕАЛЬНЫЕ: если маршрут переименуют, правило станет мёртвым, а
# запрос уедет в общий префикс с более слабой привилегией — ровно то, что этот
# тест обязан заметить. Поэтому существование пути проверяется тут же.
_SPECIFIC = (
    # Автобэкап отправляет весь архив аккаунта на сторону — это власть экспорта,
    # а не «изменить настройку».
    ("/api/settings/auto-backup", "POST", "admin.export", "settings.edit"),
    # Видеть список записей Хранилища и видеть пароли — разные права.
    ("/api/vault/{entry_id}/reveal", "POST", "vault.reveal", "vault.edit"),
    # Заказ тратит деньги.
    ("/api/infra-billing/providers/{uuid}/order", "POST",
     "billing.purchase", "billing.create"),
)


@pytest.mark.parametrize("path,method,expected,shadowed", _SPECIFIC)
def test_specific_rule_wins_over_general_prefix(path, method, expected, shadowed):
    assert path in {getattr(r, "path", "") for r in app.routes}, "правило устарело"
    needed = permissions.required(path, method)
    assert expected in needed
    assert shadowed not in needed


def test_ssh_credentials_are_a_separate_privilege():
    """Развернуть чекер на чужом сервере — это передача SSH-кредов, и она
    отделена от «выполнить»: без неё роль не может гонять наши скрипты под root
    на произвольной машине."""
    assert "/api/checker/instances/deploy" in {getattr(r, "path", "") for r in app.routes}
    needed = permissions.required("/api/checker/instances/deploy", "POST")
    assert "deploy.credentials" in needed
    # Обычное CRUD-соседство по тому же префиксу креды НЕ требует.
    assert "deploy.credentials" not in permissions.required("/api/checker/instances", "GET")


def test_waiting_deploy_restart_requires_operator_execution_permission():
    assert permissions.required("/api/deploy/restart", "POST") == ("deploy.execute",)


def test_exempt_routes_need_no_privilege():
    for path in ("/api/health", "/api/auth/login", "/api/auth/me"):
        assert permissions.required(path, "GET") == ()


def test_unmapped_route_is_denied_by_default():
    assert permissions.required("/api/brand-new-thing", "GET") is None
    # Префикс совпал, а метод не описан — это тоже пробел, а не разрешение.
    assert permissions.required("/api/updates/status", "DELETE") is None


def test_holds_and_missing():
    have = ["hostings.view", "billing.view"]
    assert permissions.holds(have, ["hostings.view"])
    assert not permissions.holds(have, ["hostings.view", "vault.reveal"])
    assert permissions.missing(have, ["vault.reveal", "billing.view"]) == ["vault.reveal"]


# ── матрица «роль × маршрут» через живой гейт ─────────────────
def _seed_roles() -> None:
    """Встроенные роли создаёт первичная настройка или миграция.

    В общем тестовом DATA_DIR их может ещё не быть: порядок файлов в прогоне не
    определён, а этот файл может оказаться первым. Совместимостный shim их
    засевает, поэтому дёргаем его, а не пишем реестр руками.
    """
    if any(r["id"] == "viewer" for r in users.list_roles()):
        return
    client.post("/api/auth/register",
                json={"login": _uniq("seed"), "password": "seed-password"})


def _user_with(role_id: str) -> str:
    """Пользователь с ОДНОЙ ролью → токен.

    ⚠️ Намеренно не через тестовый `/api/auth/register`: тот делает
    суперпользователя, а суперпользователь проходит везде и матрицу бы обнулил.
    """
    _seed_roles()
    user = users.create_user(_uniq(role_id), "role-password", [role_id])
    return users.issue_token(user["id"])


def _superuser() -> str:
    r = client.post("/api/auth/register",
                    json={"login": _uniq("root"), "password": "root-password"})
    assert r.status_code == 201
    return r.json()["token"]


def test_viewer_reads_catalogue_but_cannot_write():
    h = _auth(_user_with("viewer"))
    assert client.get("/api/hostings", headers=h).status_code == 200
    assert client.post("/api/hostings", headers=h, json={"name": "x"}).status_code == 403


def test_viewer_has_no_vault_at_all():
    """У «Наблюдателя» нет даже `vault.view`: список записей Хранилища — это
    инвентарь секретов установки."""
    h = _auth(_user_with("viewer"))
    assert client.get("/api/vault", headers=h).status_code == 403


def test_operator_cannot_manage_users():
    h = _auth(_user_with("operator"))
    assert client.get("/api/users", headers=h).status_code == 403
    assert client.get("/api/roles", headers=h).status_code == 403
    # Своим предметом при этом распоряжается полностью.
    assert client.get("/api/server-monitor/servers", headers=h).status_code == 200


def test_finance_sees_billing_but_cannot_buy():
    h = _auth(_user_with("finance"))
    assert client.get("/api/infra-billing/settings", headers=h).status_code == 200
    spend = client.post("/api/infra-billing/providers/no-such/order",
                        headers=h, json={"confirm": True})
    assert spend.status_code == 403
    # Соседние ручки того же префикса гейт пропускает: 400 «панель Remnawave не
    # настроена» и 404 «нет такого провайдера» — это уже предметный ответ, а не
    # отказ доступа. Утверждаем именно отсутствие 403.
    assert client.get("/api/infra-billing/providers", headers=h).status_code != 403
    assert client.get("/api/infra-billing/providers/no-such/order-options",
                      headers=h).status_code != 403


def test_privileged_route_still_open_to_superuser():
    h = _auth(_superuser())
    assert client.get("/api/users", headers=h).status_code == 200
    assert client.get("/api/vault", headers=h).status_code == 200


def test_unmapped_route_denies_everyone_but_superuser(monkeypatch):
    """Запрет по умолчанию проверяем на живом гейте, а не только на `required`."""
    monkeypatch.setattr(permissions, "required", lambda path, method: None)
    assert client.get("/api/hostings",
                      headers=_auth(_user_with("viewer"))).status_code == 403
    assert client.get("/api/hostings",
                      headers=_auth(_superuser())).status_code == 200


def test_readonly_api_token_cannot_write_even_with_the_privilege():
    """Флаг `readonly` только СУЖАЕТ: суперпользователь с таким токеном тоже не
    пишет. Иначе токен для внешней интеграции был бы полным доступом."""
    created = client.post("/api/auth/register",
                          json={"login": _uniq("ro"), "password": "ro-password"})
    owner = created.json()
    # Контроль: сессией тот же человек пишет — значит 403 ниже даёт именно флаг
    # токена, а не нехватка привилегии.
    assert client.post("/api/hostings", headers=_auth(owner["token"]),
                       json={"name": "control"}).status_code == 201

    _rec, token = api_tokens.create("ro-test", readonly=True,
                                    account_id=owner["id"], user_id=owner["id"])
    h = _auth(token)
    assert client.get("/api/hostings", headers=h).status_code == 200
    assert client.post("/api/hostings", headers=h, json={"name": "x"}).status_code == 403
