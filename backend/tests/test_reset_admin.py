"""Сброс пароля суперпользователя с хоста (`python -m app.reset_admin`).

Реестр здесь ИЗОЛИРУЕТСЯ (`users._path`/`users._marker` в tmp_path): общий на
прогон `users.json` набивают все остальные тестовые файлы через шим регистрации,
и в нём заведомо больше одного суперпользователя — ни «пустой реестр», ни «ровно
один владелец» на нём не проверить.
"""
import io
import json

import pytest

from app import reset_admin
from app.services import accounts, users


@pytest.fixture()
def registry(tmp_path, monkeypatch):
    """Свой users.json. Маркер миграции ставим сразу: иначе `_ensure_migrated`
    полез бы переносить сюда прежние аккаунты общего DATA_DIR."""
    path = tmp_path / "users.json"
    marker = tmp_path / ".users_migrated"
    marker.write_text("empty", encoding="utf-8")
    monkeypatch.setattr(users, "_path", lambda: path)
    monkeypatch.setattr(users, "_marker", lambda: marker)

    def _put(*records: dict) -> None:
        path.write_text(json.dumps({"users": list(records), "roles": []}),
                        encoding="utf-8")

    return _put


def _superuser(login: str, password: str) -> dict:
    return users._new_user(login, password, workspace_id="ws", is_superuser=True)


def test_reset_changes_the_password_and_kills_old_sessions(registry, monkeypatch):
    user = _superuser("owner", "old-password-1")
    registry(user)
    token = users.issue_token(user["id"])
    assert users.resolve_token(token) is not None

    monkeypatch.setattr("sys.stdin", io.StringIO("brand-new-password\n"))
    assert reset_admin.main(["--password-stdin"]) == 0

    assert users.authenticate("owner", "brand-new-password") is not None
    assert users.authenticate("owner", "old-password-1") is None
    # token_version забампился — прежний токен больше не резолвится, иначе
    # украденная сессия пережила бы сброс пароля.
    assert users.resolve_token(token) is None


def test_generate_prints_the_password_once(registry, capsys):
    user = _superuser("owner", "old-password-1")
    registry(user)

    assert reset_admin.main(["--generate"]) == 0
    printed = capsys.readouterr().out
    password = printed.split("новый пароль:")[1].splitlines()[0].strip()
    assert len(password) >= 10
    assert users.authenticate("owner", password) is not None


def test_empty_registry_does_not_create_an_owner(registry, capsys):
    registry()  # пустой реестр

    assert reset_admin.main(["--generate"]) == 1
    assert "первичную настройку" in capsys.readouterr().err
    assert users.list_users() == []


def test_two_superusers_without_login_refuses(registry, capsys):
    first = _superuser("alice", "alice-password")
    second = _superuser("bob", "bob-password")
    registry(first, second)

    assert reset_admin.main(["--generate"]) == 1
    err = capsys.readouterr().err
    assert "alice" in err and "bob" in err
    # Ни одного пароля не тронули — угадывать, чей сбрасывать, нельзя.
    assert users.authenticate("alice", "alice-password") is not None
    assert users.authenticate("bob", "bob-password") is not None


def test_login_selects_the_target_among_several(registry, monkeypatch):
    first = _superuser("alice", "alice-password")
    second = _superuser("bob", "bob-password")
    registry(first, second)

    monkeypatch.setattr("sys.stdin", io.StringIO("bobs-new-password"))
    assert reset_admin.main(["--login", "BOB", "--password-stdin"]) == 0

    assert users.authenticate("bob", "bobs-new-password") is not None
    assert users.authenticate("alice", "alice-password") is not None


def test_unknown_login_refuses(registry, capsys):
    registry(_superuser("owner", "old-password-1"))

    assert reset_admin.main(["--login", "ghost", "--generate"]) == 1
    assert "ghost" in capsys.readouterr().err
    assert users.authenticate("owner", "old-password-1") is not None


def test_password_never_travels_through_argv(registry, monkeypatch, capsys):
    """Пароль из argv попал бы в /proc/<pid>/cmdline и в историю оболочки."""
    registry(_superuser("owner", "old-password-1"))

    # Такого аргумента нет — argparse отвергает его сам (код 2).
    with pytest.raises(SystemExit) as exc:
        reset_admin.main(["--password", "secret-in-argv"])
    assert exc.value.code == 2
    capsys.readouterr()

    # Ни одного флага источника пароля → отказ, а не чтение из TTY.
    assert reset_admin.main([]) == 1
    assert "--password-stdin" in capsys.readouterr().err

    # А с флагом пароль берётся именно из stdin.
    monkeypatch.setattr("sys.stdin", io.StringIO("from-stdin-only"))
    assert reset_admin.main(["--password-stdin"]) == 0
    assert users.authenticate("owner", "from-stdin-only") is not None


def test_short_password_rejected_with_the_registry_rule(registry, monkeypatch, capsys):
    registry(_superuser("owner", "old-password-1"))

    monkeypatch.setattr("sys.stdin", io.StringIO("short"))
    assert reset_admin.main(["--password-stdin"]) == 1
    assert "10" in capsys.readouterr().err
    assert users.authenticate("owner", "old-password-1") is not None


def test_empty_stdin_rejected(registry, monkeypatch, capsys):
    registry(_superuser("owner", "old-password-1"))

    monkeypatch.setattr("sys.stdin", io.StringIO("\n"))
    assert reset_admin.main(["--password-stdin"]) == 1
    assert "stdin" in capsys.readouterr().err
    assert users.authenticate("owner", "old-password-1") is not None


def test_hash_is_written_not_the_plaintext(registry, monkeypatch, tmp_path):
    """Смена пароля не должна оставить его в реестре открытым текстом."""
    registry(_superuser("owner", "old-password-1"))
    monkeypatch.setattr("sys.stdin", io.StringIO("plaintext-check-1"))
    assert reset_admin.main(["--password-stdin"]) == 0

    raw = (tmp_path / "users.json").read_text(encoding="utf-8")
    assert "plaintext-check-1" not in raw
    stored = json.loads(raw)["users"][0]["password_hash"]
    assert accounts.verify_password("plaintext-check-1", stored)
