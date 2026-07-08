"""Ф9 (wave1) — Remnawave backup / restore routes — /api/backup.

  POST /api/backup/setup    — install the wrapper + write config.env + (optional)
                              host cron. Streamed 3-step Task. Upload secrets live
                              in the body → written to config.env on the TARGET
                              server (chmod 600), NEVER persisted here, NEVER logged.
  POST /api/backup/run      — run a backup right now (streamed Task).
  POST /api/backup/restore  — DESTRUCTIVE restore. `confirm` required in the body
                              (missing → 400); with confirm → streamed Task that
                              clears the DB volume.
  POST /api/backup/status   — read-only probe (installed / cron / config / recent
                              backups). Synchronous. SSH failure → 502.

SSH creds are per-request (from panel_jobs_<id> in the browser) and never stored,
same rule as /api/panel/*. Under `require_account` (wired globally in main.py).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from app.services import backup_service
from app.services.backup_service import _shell_safe
from app.services.ssh_manager import SSHSession
from app.services.task_store import TaskStatus, task_store

router = APIRouter(prefix="/api/backup")

_IPV4_RE = r"^(\d{1,3}\.){3}\d{1,3}$"


def _valid_ipv4(v: str) -> bool:
    import re as _re

    return bool(_re.fullmatch(_IPV4_RE, v)) and all(int(p) <= 255 for p in v.split("."))


# ──────────────────────────────────────────────────────────────
# Request models (creds per-request; never persisted)
# ──────────────────────────────────────────────────────────────


class BackupCreds(BaseModel):
    ip: str
    ssh_user: str = "root"
    ssh_password: str = Field(..., min_length=1)
    ssh_port: int = Field(default=22, ge=1, le=65535)

    @field_validator("ip")
    @classmethod
    def _validate_ip(cls, v: str) -> str:
        if not _valid_ipv4(v):
            raise ValueError("Invalid IPv4 address")
        return v


class BackupSetupRequest(BackupCreds):
    upload_method: Literal["telegram", "s3", "google_drive", "local"] = "local"
    # Telegram
    bot_token: str = ""
    chat_id: str = ""
    # S3
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = ""
    s3_endpoint: str = ""
    s3_region: str = ""
    # Google Drive
    gd_token: str = ""
    gd_folder_id: str = ""
    # Schedule / retention. cron_times empty → no auto-backup cron is installed.
    cron_times: str = ""
    retain_days: int = Field(default=7, ge=1, le=365)
    # Only "docker" is implemented — the panel is always a docker-compose stack.
    db_connection_type: Literal["docker"] = "docker"

    @field_validator(
        "bot_token",
        "chat_id",
        "s3_access_key",
        "s3_secret_key",
        "s3_bucket",
        "s3_endpoint",
        "s3_region",
        "gd_token",
        "gd_folder_id",
    )
    @classmethod
    def _safe_secret(cls, v: str) -> str:
        # These land in a `source`d config.env — reject shell-breakout chars
        # (`$`/backtick/quote/backslash/newline). Injection-neutral chars like
        # `;` are kept (they're single-quoted in the file). Mirrors
        # backup_service._shell_safe.
        if v and not _shell_safe(v):
            raise ValueError("Значение содержит недопустимые символы")
        return v

    @field_validator("cron_times")
    @classmethod
    def _valid_cron(cls, v: str) -> str:
        import re as _re

        v = (v or "").strip()
        if v and not _re.fullmatch(r"^[0-9*,/ \t-]+$", v):
            raise ValueError("Недопустимое расписание cron")
        return v

    @model_validator(mode="after")
    def _require_method_fields(self) -> "BackupSetupRequest":
        if self.upload_method == "telegram" and not (self.bot_token and self.chat_id):
            raise ValueError("BOT_TOKEN и CHAT_ID обязательны для метода telegram")
        if self.upload_method == "s3" and not (
            self.s3_access_key and self.s3_secret_key and self.s3_bucket
        ):
            raise ValueError(
                "S3_ACCESS_KEY, S3_SECRET_KEY и S3_BUCKET обязательны для метода s3"
            )
        if self.upload_method == "google_drive" and not (
            self.gd_token and self.gd_folder_id
        ):
            raise ValueError(
                "GD_TOKEN и GD_FOLDER_ID обязательны для метода google_drive"
            )
        return self


class BackupRestoreRequest(BackupCreds):
    confirm: bool = False


def _cfg(req: BackupSetupRequest) -> dict:
    return {
        "upload_method": req.upload_method,
        "bot_token": req.bot_token,
        "chat_id": req.chat_id,
        "s3_access_key": req.s3_access_key,
        "s3_secret_key": req.s3_secret_key,
        "s3_bucket": req.s3_bucket,
        "s3_endpoint": req.s3_endpoint,
        "s3_region": req.s3_region,
        "gd_token": req.gd_token,
        "gd_folder_id": req.gd_folder_id,
        "cron_times": req.cron_times,
        "retain_days": req.retain_days,
        "db_connection_type": req.db_connection_type,
    }


# ──────────────────────────────────────────────────────────────
# POST /api/backup/setup — install + config + cron (streamed Task)
# ──────────────────────────────────────────────────────────────

_SETUP_STEPS = ["Установка", "Настройка config.env", "Расписание (cron)"]


@router.post("/setup")
async def backup_setup(
    req: BackupSetupRequest, background_tasks: BackgroundTasks
) -> dict:
    task = task_store.create(total_steps=len(_SETUP_STEPS))
    background_tasks.add_task(_run_setup, req, task.task_id)
    return {"task_id": task.task_id, "task_type": "backup-setup"}


async def _run_setup(req: BackupSetupRequest, task_id: str) -> None:
    task = task_store.get(task_id)
    if not task:
        return
    ssh = SSHSession(req.ip, req.ssh_port, req.ssh_user, req.ssh_password)
    try:
        task.set_step(1, TaskStatus.RUNNING)
        task.add_log(f"\x1b[1;36m[Резервное копирование] Настройка на {req.ip}\x1b[0m")
        await ssh.connect()
        task.add_log("\x1b[32mПодключено.\x1b[0m")
        await ssh.run_script(backup_service.install_script(), task, timeout=180)

        # Step 2 — config.env through the SILENT channel (secrets never logged).
        task.set_step(2, TaskStatus.RUNNING)
        out = await ssh.get_script_output(backup_service.config_env_script(_cfg(req)))
        if "__RWCFG_WRITTEN__" not in (out or ""):
            raise RuntimeError("Не удалось записать config.env")
        task.add_log(
            "\x1b[32m[backup] config.env записан (секреты не выводятся).\x1b[0m"
        )

        # Step 3 — host cron (only when a schedule was given).
        task.set_step(3, TaskStatus.RUNNING)
        if req.cron_times.strip():
            await ssh.run_script(
                backup_service.setup_cron_script(req.cron_times), task, timeout=60
            )
        else:
            task.add_log(
                "\x1b[90m[cron] Расписание не задано — авто-бэкап не настроен.\x1b[0m"
            )

        task.finish(TaskStatus.SUCCESS)
        task.add_log("\n\x1b[1;32m✓ Резервное копирование настроено.\x1b[0m")
    except Exception as exc:
        task.add_log(f"\n\x1b[1;31m✗ Ошибка: {exc}\x1b[0m")
        task.finish(TaskStatus.FAILED, str(exc))
    finally:
        await ssh.close()


# ──────────────────────────────────────────────────────────────
# POST /api/backup/run  &  /restore — one-shot streamed Tasks
# ──────────────────────────────────────────────────────────────


@router.post("/run")
async def backup_run(req: BackupCreds, background_tasks: BackgroundTasks) -> dict:
    task = task_store.create(total_steps=1)
    background_tasks.add_task(_run_op, req, task.task_id, "run")
    return {"task_id": task.task_id, "task_type": "backup-run"}


@router.post("/restore")
async def backup_restore(
    req: BackupRestoreRequest, background_tasks: BackgroundTasks
) -> dict:
    if not req.confirm:
        raise HTTPException(
            status_code=400,
            detail="Восстановление ДЕСТРУКТИВНО и требует подтверждения (confirm=true).",
        )
    task = task_store.create(total_steps=1)
    background_tasks.add_task(_run_op, req, task.task_id, "restore")
    return {"task_id": task.task_id, "task_type": "backup-restore"}


async def _run_op(req: BackupCreds, task_id: str, kind: str) -> None:
    task = task_store.get(task_id)
    if not task:
        return
    ssh = SSHSession(req.ip, req.ssh_port, req.ssh_user, req.ssh_password)
    try:
        task.set_step(1, TaskStatus.RUNNING)
        await ssh.connect()
        task.add_log("\x1b[32mПодключено.\x1b[0m")
        if kind == "restore":
            task.add_log(
                "\x1b[1;31m[ВНИМАНИЕ] Восстановление ДЕСТРУКТИВНО — том "
                "remnawave-db-data будет очищен.\x1b[0m"
            )
            script = backup_service.restore_script(True)
        else:
            task.add_log("\x1b[36m[backup] Ручной запуск бэкапа...\x1b[0m")
            script = backup_service.run_backup_script()
        # check=False keeps the wrapper's own error output streaming, but we still
        # honour its exit code — a failed backup/restore must be FAILED, not a
        # green "✓ Готово" over a full disk or a corrupt bundle.
        rc = await ssh.run_script(script, task, check=False, timeout=1800)
        if rc != 0:
            raise RuntimeError(
                "Операция завершилась с ошибкой (см. лог выше)"
                if kind == "run"
                else "Восстановление завершилось с ошибкой (см. лог выше)"
            )
        task.finish(TaskStatus.SUCCESS)
        task.add_log("\n\x1b[1;32m✓ Готово.\x1b[0m")
    except Exception as exc:
        task.add_log(f"\n\x1b[1;31m✗ Ошибка: {exc}\x1b[0m")
        task.finish(TaskStatus.FAILED, str(exc))
    finally:
        await ssh.close()


# ──────────────────────────────────────────────────────────────
# POST /api/backup/status — read-only probe (synchronous)
# ──────────────────────────────────────────────────────────────


def _parse_status(out: str) -> dict:
    installed = cron = configured = False
    backups: list[dict] = []
    in_list = False
    for raw in (out or "").splitlines():
        ln = raw.strip()
        if ln == "RWBK_INSTALLED=yes":
            installed = True
        elif ln == "RWBK_CRON=yes":
            cron = True
        elif ln == "RWBK_CONFIG=yes":
            configured = True
        elif ln == "RWBK_BACKUPS_START":
            in_list = True
        elif ln == "RWBK_BACKUPS_END":
            in_list = False
        elif in_list and "|" in ln:
            name, _, rest = ln.partition("|")
            size, _, mtime = rest.partition("|")
            try:
                backups.append(
                    {"name": name, "size": int(size or 0), "mtime": int(mtime or 0)}
                )
            except ValueError:
                continue
    return {
        "installed": installed,
        "cronConfigured": cron,
        "configured": configured,
        "backups": backups,
        "lastBackup": backups[0] if backups else None,
    }


@router.post("/status")
async def backup_status(req: BackupCreds) -> dict:
    ssh = SSHSession(req.ip, req.ssh_port, req.ssh_user, req.ssh_password)
    try:
        try:
            await ssh.connect()
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Не удалось подключиться к серверу {req.ip}:{req.ssh_port}: {exc}",
            )
        out = await ssh.get_script_output(backup_service.status_script())
        return _parse_status(out)
    finally:
        await ssh.close()
