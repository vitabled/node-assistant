"""Ф9 (wave1) — Remnawave backup / restore.

Covers:
  (a) pure script generators (`install_script`/`config_env_script`/
      `setup_cron_script`/`run_backup_script`/`restore_script`/`status_script`) —
      no SSH, no network;
  (b) the restore confirm-gate (stub vs. real) and secret shell-safety;
  (c) the /api/backup routes with a mocked SSHSession (creds transient) — setup /
      run / restore(confirm) / status parse / SSH-failure 502 / auth 401;
  (d) secrets never reach the Task log (config.env goes through the SILENT channel).
"""

import re
import uuid

import pytest
from fastapi.testclient import TestClient

import app.api.backup as backup_api
from app.main import app
from app.services import backup_service
from app.services.task_store import TaskStatus, task_store

client = TestClient(app)


def _auth():
    r = client.post(
        "/api/auth/register",
        json={"login": f"bk-{uuid.uuid4().hex[:8]}", "password": "pw"},
    )
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ── (a) generators ─────────────────────────────────────────────


def test_install_script_installs_wrapper():
    s = backup_service.install_script()
    assert "/opt/rw-backup-restore" in s
    assert "backup-restore.sh" in s
    # single-quoted heredoc → nothing expands at install time
    assert "<<'RWBR_EOF'" in s
    assert "chmod +x" in s


def test_config_env_telegram_has_required_keys_and_chmod():
    s = backup_service.config_env_script(
        {"upload_method": "telegram", "bot_token": "123:ABCdef", "chat_id": "42"}
    )
    assert "UPLOAD_METHOD='telegram'" in s
    assert "BOT_TOKEN='123:ABCdef'" in s
    assert "CHAT_ID='42'" in s
    assert "chmod 600" in s
    assert "umask 077" in s
    # SILENT single-quoted heredoc → secrets don't expand when written
    assert "<<'RWCFG_EOF'" in s


def test_config_env_s3_has_required_keys():
    s = backup_service.config_env_script(
        {
            "upload_method": "s3",
            "s3_access_key": "AK",
            "s3_secret_key": "SK",
            "s3_bucket": "b",
            "s3_endpoint": "https://s3.example.com",
            "s3_region": "eu",
        }
    )
    for key in (
        "S3_ACCESS_KEY",
        "S3_SECRET_KEY",
        "S3_BUCKET",
        "S3_ENDPOINT",
        "S3_REGION",
    ):
        assert f"{key}='" in s, f"missing {key}"


def test_config_env_google_drive_keys():
    s = backup_service.config_env_script(
        {"upload_method": "google_drive", "gd_token": "T", "gd_folder_id": "F"}
    )
    assert "GD_TOKEN='T'" in s
    assert "GD_FOLDER_ID='F'" in s


def test_config_env_rejects_unknown_method():
    with pytest.raises(ValueError):
        backup_service.config_env_script({"upload_method": "ftp"})


def test_config_env_rejects_command_substitution_secret():
    # $() / backtick / $VAR would execute when config.env is sourced → reject.
    with pytest.raises(ValueError):
        backup_service.config_env_script(
            {"upload_method": "telegram", "bot_token": "a$(reboot)", "chat_id": "1"}
        )
    with pytest.raises(ValueError):
        backup_service.config_env_script(
            {"upload_method": "telegram", "bot_token": "a`id`", "chat_id": "1"}
        )


def test_config_env_neutralizes_semicolon_by_single_quoting():
    # `;` is injection-neutral inside single quotes → escaped, not rejected.
    s = backup_service.config_env_script(
        {"upload_method": "telegram", "bot_token": "a;b", "chat_id": "1"}
    )
    assert "BOT_TOKEN='a;b'" in s


@pytest.mark.parametrize(
    "evil",
    [
        "a\nEVIL='x'",  # newline → forge a new KEY= line
        "a\nRWCFG_EOF",  # newline → close the heredoc early
        "a'b",  # single quote → break out of KEY='…'
        "a\\b",  # backslash escape
    ],
)
def test_config_env_rejects_breakout_chars(evil):
    # Locks the _UNSAFE_CHARS invariant: neither a heredoc-terminator/new-line
    # nor a single-quote breakout can be smuggled through a secret value.
    with pytest.raises(ValueError):
        backup_service.config_env_script(
            {"upload_method": "telegram", "bot_token": evil, "chat_id": "1"}
        )


def test_setup_cron_has_marker_and_schedule():
    s = backup_service.setup_cron_script("0 3 * * *")
    assert "# node-assistant rw-backup" in s
    assert "0 3 * * *" in s
    # overwrites its own line (grep -vF the marker), never duplicates
    assert "grep -vF" in s
    assert "crontab" in s


def test_setup_cron_rejects_bad_schedule():
    with pytest.raises(ValueError):
        backup_service.setup_cron_script("0 3 * * *; rm -rf /")
    with pytest.raises(ValueError):
        backup_service.setup_cron_script("$(reboot)")


def test_restore_stub_when_not_confirmed():
    s = backup_service.restore_script(False)
    assert "exit 1" in s
    assert "требует подтверждения" in s
    # the stub must NOT touch the DB volume / wrapper
    assert "remnawave-db-data" not in s
    assert "restore --confirm" not in s


def test_restore_real_when_confirmed():
    s = backup_service.restore_script(True)
    assert "restore --confirm" in s
    assert "ДЕСТРУКТИВНО" in s


def test_run_backup_script_guards_install():
    s = backup_service.run_backup_script()
    assert "backup-restore.sh backup" in s
    assert "не установлен" in s


def test_status_script_markers():
    s = backup_service.status_script()
    for marker in ("RWBK_INSTALLED", "RWBK_CRON", "RWBK_CONFIG", "RWBK_BACKUPS_START"):
        assert marker in s


def test_wrapper_notes_no_tls_backup():
    # TLS certs are explicitly out of scope for the bundle — documented in-script.
    assert "TLS" in backup_service._WRAPPER


# ── (b) status parsing ─────────────────────────────────────────


def test_parse_status():
    out = (
        "RWBK_INSTALLED=yes\n"
        "RWBK_CONFIG=yes\n"
        "RWBK_CRON=no\n"
        "RWBK_BACKUPS_START\n"
        "remnawave_backup_20260707_030000.tar.gz|1048576|1751850000\n"
        "remnawave_backup_20260706_030000.tar.gz|1000|1751763600\n"
        "RWBK_BACKUPS_END\n"
    )
    r = backup_api._parse_status(out)
    assert r["installed"] is True
    assert r["configured"] is True
    assert r["cronConfigured"] is False
    assert len(r["backups"]) == 2
    assert r["lastBackup"]["name"] == "remnawave_backup_20260707_030000.tar.gz"
    assert r["lastBackup"]["size"] == 1048576


def test_parse_status_empty():
    r = backup_api._parse_status("RWBK_INSTALLED=no\nRWBK_CONFIG=no\nRWBK_CRON=no\n")
    assert r["installed"] is False
    assert r["backups"] == []
    assert r["lastBackup"] is None


# ── (c) routes (SSH mocked) ────────────────────────────────────


class _FakeSSH:
    """Records scripts + returns the config.env write sentinel + a status blob."""

    scripts: list = []
    silent: list = []

    def __init__(self, *a, **k):
        pass

    async def connect(self, *a, **k):
        pass

    async def get_script_output(self, script, timeout=None):
        _FakeSSH.silent.append(script)
        if "RWBK_INSTALLED" in script:
            return (
                "RWBK_INSTALLED=yes\nRWBK_CONFIG=yes\nRWBK_CRON=yes\n"
                "RWBK_BACKUPS_START\n"
                "remnawave_backup_20260707_030000.tar.gz|2048|1751850000\n"
                "RWBK_BACKUPS_END\n"
            )
        return "__RWCFG_WRITTEN__"

    async def run_script(self, script, task, check=True, timeout=None):
        _FakeSSH.scripts.append(script)
        return 0

    async def close(self):
        pass


def _reset():
    _FakeSSH.scripts = []
    _FakeSSH.silent = []


def test_setup_requires_auth():
    r = client.post("/api/backup/setup", json={"ip": "1.2.3.4", "ssh_password": "pw"})
    assert r.status_code == 401


def test_setup_endpoint_mocked_ssh(monkeypatch):
    _reset()
    monkeypatch.setattr(backup_api, "SSHSession", _FakeSSH)
    r = client.post(
        "/api/backup/setup",
        headers=_auth(),
        json={
            "ip": "1.2.3.4",
            "ssh_password": "pw",
            "upload_method": "telegram",
            "bot_token": "123:ABC",
            "chat_id": "42",
            "cron_times": "0 3 * * *",
        },
    )
    assert r.status_code == 200
    assert r.json()["task_type"] == "backup-setup"
    task = task_store.get(r.json()["task_id"])
    assert task is not None and task.status == TaskStatus.SUCCESS
    # install + cron ran on the streamed channel; config.env on the SILENT one
    assert any("backup-restore.sh" in s for s in _FakeSSH.scripts)
    assert any("crontab" in s for s in _FakeSSH.scripts)
    assert any("BOT_TOKEN" in s for s in _FakeSSH.silent)


def test_setup_telegram_requires_token_422():
    r = client.post(
        "/api/backup/setup",
        headers=_auth(),
        json={"ip": "1.2.3.4", "ssh_password": "pw", "upload_method": "telegram"},
    )
    assert r.status_code == 422


def test_setup_rejects_injection_secret_422():
    r = client.post(
        "/api/backup/setup",
        headers=_auth(),
        json={
            "ip": "1.2.3.4",
            "ssh_password": "pw",
            "upload_method": "telegram",
            "bot_token": "a$(reboot)",
            "chat_id": "1",
        },
    )
    assert r.status_code == 422


def test_run_endpoint(monkeypatch):
    _reset()
    monkeypatch.setattr(backup_api, "SSHSession", _FakeSSH)
    r = client.post(
        "/api/backup/run",
        headers=_auth(),
        json={"ip": "1.2.3.4", "ssh_password": "pw"},
    )
    assert r.status_code == 200
    assert r.json()["task_type"] == "backup-run"
    task = task_store.get(r.json()["task_id"])
    assert task is not None and task.status == TaskStatus.SUCCESS
    assert any("backup-restore.sh backup" in s for s in _FakeSSH.scripts)


def test_run_endpoint_nonzero_exit_fails(monkeypatch):
    # A non-zero wrapper exit (full disk / corrupt bundle) must mark the Task
    # FAILED, not report a green "✓ Готово".
    class FailSSH(_FakeSSH):
        async def run_script(self, script, task, check=True, timeout=None):
            _FakeSSH.scripts.append(script)
            return 1

    _reset()
    monkeypatch.setattr(backup_api, "SSHSession", FailSSH)
    r = client.post(
        "/api/backup/run",
        headers=_auth(),
        json={"ip": "1.2.3.4", "ssh_password": "pw"},
    )
    assert r.status_code == 200
    task = task_store.get(r.json()["task_id"])
    assert task is not None and task.status == TaskStatus.FAILED


def test_google_drive_requires_token_and_folder():
    # gdrive gating is symmetric with telegram/s3 — missing creds → 422.
    r = client.post(
        "/api/backup/setup",
        headers=_auth(),
        json={"ip": "1.2.3.4", "ssh_password": "pw", "upload_method": "google_drive"},
    )
    assert r.status_code == 422
    # with both → accepted (mock SSH not even needed; validation is model-level)


def test_restore_without_confirm_400():
    r = client.post(
        "/api/backup/restore",
        headers=_auth(),
        json={"ip": "1.2.3.4", "ssh_password": "pw"},
    )
    assert r.status_code == 400
    assert "подтвержд" in r.json()["detail"]


def test_restore_with_confirm_streams(monkeypatch):
    _reset()
    monkeypatch.setattr(backup_api, "SSHSession", _FakeSSH)
    r = client.post(
        "/api/backup/restore",
        headers=_auth(),
        json={"ip": "1.2.3.4", "ssh_password": "pw", "confirm": True},
    )
    assert r.status_code == 200
    assert r.json()["task_type"] == "backup-restore"
    task = task_store.get(r.json()["task_id"])
    assert task is not None and task.status == TaskStatus.SUCCESS
    assert any("restore --confirm" in s for s in _FakeSSH.scripts)


def test_status_endpoint_parses(monkeypatch):
    _reset()
    monkeypatch.setattr(backup_api, "SSHSession", _FakeSSH)
    r = client.post(
        "/api/backup/status",
        headers=_auth(),
        json={"ip": "1.2.3.4", "ssh_password": "pw"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["installed"] is True
    assert body["cronConfigured"] is True
    assert body["configured"] is True
    assert body["lastBackup"]["name"].startswith("remnawave_backup_")


def test_status_ssh_failure_502(monkeypatch):
    class BoomSSH:
        def __init__(self, *a, **k):
            pass

        async def connect(self, *a, **k):
            raise OSError("connection refused")

        async def close(self):
            pass

    monkeypatch.setattr(backup_api, "SSHSession", BoomSSH)
    r = client.post(
        "/api/backup/status",
        headers=_auth(),
        json={"ip": "1.2.3.4", "ssh_password": "pw"},
    )
    assert r.status_code == 502
    assert "Не удалось подключиться" in r.json()["detail"]


def test_status_requires_auth():
    assert (
        client.post(
            "/api/backup/status", json={"ip": "1.2.3.4", "ssh_password": "pw"}
        ).status_code
        == 401
    )


def test_invalid_ip_rejected_422():
    r = client.post(
        "/api/backup/status",
        headers=_auth(),
        json={"ip": "evil; rm", "ssh_password": "pw"},
    )
    assert r.status_code == 422


# ── (d) secrets never reach the Task log ───────────────────────


def test_setup_secrets_never_logged(monkeypatch):
    captured = {"scripts": [], "silent": [], "logs": []}

    class SilentSSH:
        def __init__(self, *a, **k):
            pass

        async def connect(self, *a, **k):
            pass

        async def get_script_output(self, script, timeout=None):
            captured["silent"].append(script)
            return "__RWCFG_WRITTEN__"

        async def run_script(self, script, task, check=True, timeout=None):
            captured["scripts"].append(script)
            return 0

        async def close(self):
            pass

    # Patch the log sink so we can inspect every line the task emitted.
    real_get = task_store.get

    monkeypatch.setattr(backup_api, "SSHSession", SilentSSH)
    r = client.post(
        "/api/backup/setup",
        headers=_auth(),
        json={
            "ip": "1.2.3.4",
            "ssh_password": "pw",
            "upload_method": "telegram",
            "bot_token": "SECRET123:TOKENVALUE",
            "chat_id": "42",
        },
    )
    assert r.status_code == 200
    task = real_get(r.json()["task_id"])
    assert task is not None
    logs = "\n".join(task.logs)
    # the token was written through the silent channel only
    assert any("SECRET123:TOKENVALUE" in s for s in captured["silent"])
    assert "SECRET123:TOKENVALUE" not in logs
    assert all("SECRET123:TOKENVALUE" not in s for s in captured["scripts"])
    # and the config.env write really is single-quoted (source-safe)
    assert re.search(r"BOT_TOKEN='SECRET123:TOKENVALUE'", "\n".join(captured["silent"]))
