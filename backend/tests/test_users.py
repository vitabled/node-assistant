"""Реестр пользователей: миграция с прежней модели, защита владельца, отзыв сессий.

Все тесты работают в ЧИСТОМ `DATA_DIR`: реестр глобальный (`users.json`), а
предметы здешних проверок — «первичная настройка ещё не выполнена» и «в установке
один суперпользователь». В общем тестовом каталоге живут пользователи, созданные
другими файлами набора, и оба факта там неверны.
"""
import json
import time

import pytest

from app.services import accounts, permissions, users


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """Пустая установка.

    `users.py` читает путь при каждом вызове (`_path()` → `accounts.DATA_DIR`) и
    ничего не кеширует, поэтому достаточно переставить каталог — как в
    `test_accounts.py::_fresh`. Константы `accounts` переставляем тоже: миграция
    читает прежний реестр через `accounts.list_accounts()`.
    """
    monkeypatch.setattr(accounts, "DATA_DIR", tmp_path)
    monkeypatch.setattr(accounts, "_REGISTRY_FILE", tmp_path / "accounts.json")
    monkeypatch.setattr(accounts, "_ACCOUNTS_DIR", tmp_path / "accounts")
    return tmp_path


def _legacy_registry(tmp_path, *rows: tuple[str, str, str, int]) -> None:
    """Прежний `accounts.json` из (id, логин, пароль, created_at)."""
    tmp_path.joinpath("accounts.json").write_text(
        json.dumps({"accounts": [
            {"id": aid, "login": login,
             "password_hash": accounts.hash_password(pw), "created_at": ts}
            for aid, login, pw, ts in rows
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )


def _owner(fresh_dir) -> dict:
    return users.bootstrap("owner", "owner-password")


# ── миграция с прежней модели ─────────────────────────────────
def test_migration_promotes_the_oldest_account_and_keeps_the_rest_as_viewers(fresh):
    """Прежние аккаунты были тенантами; после Волны 13 установка одна.

    Старший по `created_at` становится владельцем, его каталог — рабочей областью
    установки, остальные — её пользователями с ролью «Наблюдатель». Пароли
    сохраняются: люди входят прежними кредами, иначе миграция заперла бы всех.
    """
    _legacy_registry(fresh,
                     ("id-mid", "mid", "mid-password", 200),
                     ("id-first", "first", "first-password", 100),
                     ("id-last", "last", "last-password", 300))

    listed = users.list_users()
    by_login = {u["login"]: u for u in listed}
    assert set(by_login) == {"first", "mid", "last"}

    first = by_login["first"]
    assert first["is_superuser"] is True
    assert first["workspace_id"] == first["id"] == "id-first"
    # Прежний каталог владельца И становится рабочей областью, поэтому архива у
    # него нет — его данные и есть данные установки.
    assert not first["legacy_workspace_id"]

    for login in ("mid", "last"):
        u = by_login[login]
        assert u["is_superuser"] is False
        assert u["role_ids"] == ["viewer"]
        # Работают в области владельца, а прежний каталог остался архивом: слить
        # пятнадцать сторов автоматически нельзя, потерять их — тем более.
        assert u["workspace_id"] == "id-first"
        assert u["legacy_workspace_id"] == u["id"]

    for login, pw in (("first", "first-password"), ("mid", "mid-password"),
                      ("last", "last-password")):
        assert users.authenticate(login, pw), login
    assert users.authenticate("mid", "wrong") is None


def test_migration_creates_builtin_roles_and_runs_once(fresh):
    _legacy_registry(fresh, ("id-a", "a", "a-password", 1))
    assert {r["id"] for r in users.list_roles()} >= {
        r["id"] for r in permissions.BUILTIN_ROLES}

    # Повторный обход не должен ни удваивать людей, ни возвращать прежний состав:
    # маркер и наличие users.json гасят миграцию навсегда.
    users.create_user("added", "added-password", ["viewer"])
    before = [u["login"] for u in users.list_users()]
    users.needs_bootstrap()
    assert [u["login"] for u in users.list_users()] == before
    assert "added" in before


def test_fresh_install_needs_bootstrap(fresh):
    assert users.needs_bootstrap() is True


# ── первичная настройка ───────────────────────────────────────
def test_bootstrap_creates_the_owner_once(fresh):
    owner = _owner(fresh)
    assert owner["is_superuser"] is True
    assert owner["workspace_id"]
    assert users.needs_bootstrap() is False
    assert {r["id"] for r in users.list_roles()} >= {"admin", "operator",
                                                    "finance", "viewer"}
    assert users.authenticate("owner", "owner-password")

    # Иначе публичная ручка была бы способом завести себе второго владельца.
    with pytest.raises(users.UserError):
        users.bootstrap("intruder", "intruder-password")


# ── защита владельца ──────────────────────────────────────────
def test_last_superuser_cannot_be_disabled_demoted_or_deleted(fresh):
    owner = _owner(fresh)
    users.create_user("helper", "helper-password", ["viewer"])

    for call in (lambda: users.set_disabled(owner["id"], True),
                 lambda: users.set_superuser(owner["id"], False),
                 lambda: users.delete_user(owner["id"])):
        with pytest.raises(users.UserError):
            call()

    # Ничего не сломалось: владелец на месте и по-прежнему суперпользователь.
    still = users.get(owner["id"])
    assert still["is_superuser"] and not still["disabled"]


def test_superuser_can_step_down_once_there_is_another(fresh):
    owner = _owner(fresh)
    second = users.create_user("second", "second-password", [], is_superuser=True)
    users.set_superuser(owner["id"], False)
    assert users.get(owner["id"])["is_superuser"] is False
    # А теперь последний — уже второй.
    with pytest.raises(users.UserError):
        users.delete_user(second["id"])


def test_a_disabled_superuser_does_not_count_as_a_backup(fresh):
    """Выключенный владелец войти не может, поэтому «второй суперпользователь»
    из него не получается."""
    owner = _owner(fresh)
    spare = users.create_user("spare", "spare-password", [], is_superuser=True)
    users.set_disabled(spare["id"], True)
    with pytest.raises(users.UserError):
        users.set_disabled(owner["id"], True)


# ── правило неусиления ────────────────────────────────────────
def test_cannot_grant_a_role_wider_than_your_own_rights(fresh):
    owner = _owner(fresh)
    granter = users.create_user("granter", "granter-password", ["viewer"])

    with pytest.raises(users.UserError):
        users.assert_can_grant(granter, ["admin"])
    # Свою же роль выдать можно — её привилегиями он обладает.
    users.assert_can_grant(granter, ["viewer"])
    # Неизвестная роль не повод для отказа: её всё равно нельзя надеть.
    users.assert_can_grant(granter, ["no-such-role"])
    # Суперпользователь освобождён — он и так обладает всем.
    users.assert_can_grant(users.get(owner["id"]), ["admin", "operator"])


# ── отзыв сессий через token_version ──────────────────────────
@pytest.mark.parametrize("revoke", [
    lambda uid: users.set_password(uid, "brand-new-password"),
    lambda uid: users.set_roles(uid, ["operator"]),
    lambda uid: users.set_disabled(uid, True),
])
def test_password_roles_and_disabling_kill_live_tokens(fresh, revoke):
    """Прежде уволенный сотрудник со сохранённым токеном работал и после смены
    пароля: сессионный JWT без срока и без версии отозвать было нечем."""
    _owner(fresh)
    user = users.create_user("staff", "staff-password", ["viewer"])
    token = users.issue_token(user["id"])
    assert users.resolve_token(token)["id"] == user["id"]

    revoke(user["id"])
    assert users.resolve_token(token) is None


def test_a_freshly_issued_token_works_after_a_password_change(fresh):
    _owner(fresh)
    user = users.create_user("staff", "staff-password", ["viewer"])
    users.set_password(user["id"], "brand-new-password")
    assert users.resolve_token(users.issue_token(user["id"]))["id"] == user["id"]


def test_resolve_token_rejects_garbage_and_unknown_users(fresh):
    _owner(fresh)
    assert users.resolve_token("not-a-jwt") is None
    assert users.resolve_token(users.issue_token("no-such-user")) is None


# ── привилегии ────────────────────────────────────────────────
def test_permissions_of_superuser_is_everything(fresh):
    owner = _owner(fresh)
    assert set(users.permissions_of(users.get(owner["id"]))) == set(
        permissions.ALL_PERMISSIONS)


def test_permissions_of_unions_roles_and_ignores_unknown_ones(fresh):
    _owner(fresh)
    by_id = {r["id"]: set(r["permissions"]) for r in users.list_roles()}

    both = set(users.permissions_of({"role_ids": ["viewer", "finance"]}))
    assert both == by_id["viewer"] | by_id["finance"]
    # Смысл объединения: право пришло именно из второй роли.
    assert "billing.create" in both and "billing.create" not in by_id["viewer"]

    # Снятая или переименованная роль не должна ронять расчёт прав.
    assert set(users.permissions_of({"role_ids": ["viewer", "no-such-role"]})) == \
        by_id["viewer"]
    assert users.permissions_of({"role_ids": []}) == []


def test_editing_a_role_changes_its_holders_rights(fresh):
    _owner(fresh)
    user = users.create_user("staff", "staff-password", ["viewer"])
    users.update_role("viewer", perms=["hostings.view"])
    assert users.permissions_of(users.get(user["id"])) == ["hostings.view"]


# ── рабочие области для фоновых лупов ─────────────────────────
def test_list_workspaces_returns_live_unique_areas_only(fresh):
    """⚠️ Закрывает реальную регрессию: лупы (мониторинг, правила, автобэкап,
    синк балансов) обходили `accounts.list_accounts()`, а на свежей установке
    Волны 13 файла `accounts.json` нет вовсе — и все они молча простаивали.
    """
    owner = _owner(fresh)
    ws = owner["workspace_id"]

    # Двое в одной области — область одна: полить её данными по разу на каждого
    # значило бы удвоить обращения к чужим API.
    users.create_user("mate", "mate-password", ["viewer"])
    assert users.list_workspaces() == [ws]

    other = users.create_user("other", "other-password", ["viewer"],
                              workspace_id="ws-other")
    assert set(users.list_workspaces()) == {ws, "ws-other"}

    # Выключенного не опрашиваем — он и войти не может.
    users.set_disabled(other["id"], True)
    assert users.list_workspaces() == [ws]


def test_legacy_workspaces_are_not_polled(fresh):
    """Прежние каталоги аккаунтов 2..N — архив на диске, а не то, что надо
    обходить фоном."""
    _legacy_registry(fresh,
                     ("id-first", "first", "first-password", 100),
                     ("id-second", "second", "second-password", 200))
    assert users.list_workspaces() == ["id-first"]


# ── минимум пароля ────────────────────────────────────────────
def test_password_minimum_applies_on_set_but_not_on_login(fresh):
    """Требование нельзя предъявить уже сохранённому хешу, поэтому вход прежним
    коротким паролем работает — а вот задать короткий заново нельзя."""
    _legacy_registry(fresh, ("id-old", "old", "pw", 1))
    assert users.authenticate("old", "pw")

    with pytest.raises(users.UserError):
        users.create_user("newbie", "short", ["viewer"])
    with pytest.raises(users.UserError):
        users.set_password("id-old", "short")
    with pytest.raises(users.UserError):
        users.set_password("id-old", "   ")

    users.set_password("id-old", "long-enough-password")
    assert users.authenticate("old", "long-enough-password")
    assert users.authenticate("old", "pw") is None


def test_bootstrap_rejects_a_short_password(fresh):
    with pytest.raises(users.UserError):
        users.bootstrap("owner", "short")
    assert users.needs_bootstrap() is True


# ── прочее ────────────────────────────────────────────────────
def test_duplicate_login_is_rejected_case_insensitively(fresh):
    _owner(fresh)
    users.create_user("Person", "person-password", ["viewer"])
    with pytest.raises(users.UserError):
        users.create_user("person", "person-password", ["viewer"])


def test_unknown_role_is_rejected_when_creating_a_user(fresh):
    _owner(fresh)
    with pytest.raises(users.UserError):
        users.create_user("staff", "staff-password", ["no-such-role"])


def test_public_record_never_carries_the_password_hash(fresh):
    owner = _owner(fresh)
    for record in (owner, users.get(owner["id"]), *users.list_users()):
        assert "password_hash" not in record


def test_builtin_roles_cannot_be_deleted(fresh):
    _owner(fresh)
    with pytest.raises(users.UserError):
        users.delete_role("viewer")
    custom = users.create_role("Своя", "", ["hostings.view"])
    users.delete_role(custom["id"])
    assert users.get_role(custom["id"]) is None


def test_deleting_a_role_strips_it_from_holders(fresh):
    _owner(fresh)
    custom = users.create_role("Своя", "", ["hostings.view"])
    user = users.create_user("staff", "staff-password", [custom["id"]])
    users.delete_role(custom["id"])
    assert users.get(user["id"])["role_ids"] == []


def test_last_login_is_recorded(fresh):
    owner = _owner(fresh)
    assert users.get(owner["id"])["last_login"] == 0
    users.authenticate("owner", "owner-password")
    assert users.get(owner["id"])["last_login"] <= int(time.time())
    assert users.get(owner["id"])["last_login"] > 0


def test_disabled_user_cannot_log_in(fresh):
    _owner(fresh)
    user = users.create_user("staff", "staff-password", ["viewer"])
    users.set_disabled(user["id"], True)
    assert users.authenticate("staff", "staff-password") is None
    users.set_disabled(user["id"], False)
    assert users.authenticate("staff", "staff-password")
