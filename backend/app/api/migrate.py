"""
Marzban → Remnawave migration API (Ф7). Account-gated.

- POST /api/migrate/preview  — dry-run: connect to Marzban, return user/inbound
  counts + a "what will NOT migrate" report. Writes nothing.
- POST /api/migrate/reality  — copy Marzban Reality settings onto same-tag
  inbounds of an EXISTING Remnawave config-profile (patch, never add/remove).
- POST /api/migrate/run      — DESTRUCTIVE-ish: run the migrate binary (confirm
  required). Streamed Task.
- POST /api/migrate/legacy-secret — read Marzban's JWT secret over SSH (for
  keeping legacy subscription links working). Creds per-request, never logged.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from app.services import marzban_migrate, marzban_reality, net_guard
from app.services.remnawave_client import RemnavaveClient, RemnavaveError
from app.services.ssh_manager import SSHSession
from app.services.task_store import task_store

router = APIRouter(prefix="/api/migrate")

# The migrate image is pinned server-side — an account must NOT be able to make
# the host (DooD) pull+run an arbitrary Docker image.
_MIGRATE_IMAGE = "remnawave/migrate:latest"


def _remnawave_client(url: str, token: str) -> RemnavaveClient:
    """Build a Remnawave client after SSRF-guarding the account-supplied URL."""
    if not net_guard.is_safe_url(url):
        raise HTTPException(400, "remnawave_url не разрешён (нужен публичный http(s)).")
    return RemnavaveClient(url, token)


class MarzbanCreds(BaseModel):
    marzban_url: str = Field(..., min_length=1)
    marzban_username: str = Field(..., min_length=1)
    marzban_password: str = Field(..., min_length=1)


class RemnawaveTarget(BaseModel):
    remnawave_url: str = Field(..., min_length=1)
    remnawave_token: str = Field(..., min_length=1)


class PreviewBody(MarzbanCreds):
    pass


class RealityBody(MarzbanCreds, RemnawaveTarget):
    config_profile_uuid: str = Field(..., min_length=1)


class RunBody(MarzbanCreds, RemnawaveTarget):
    preserve_status: bool = True
    preserve_subhash: bool = True
    internal_squad_uuids: list[str] = []
    batch_size: int = Field(100, ge=1, le=10000)
    confirm: bool = False


class LegacySecretBody(BaseModel):
    ip: str = Field(..., min_length=1)
    ssh_port: int = Field(22, ge=1, le=65535)
    ssh_user: str = "root"
    ssh_password: str = ""


# ── preview ───────────────────────────────────────────────────
@router.post("/preview")
async def preview(body: PreviewBody) -> dict:
    try:
        token = await marzban_migrate.marzban_login(
            body.marzban_url, body.marzban_username, body.marzban_password
        )
        counts = await marzban_migrate.marzban_counts(body.marzban_url, token)
    except marzban_migrate.MarzbanApiError as exc:
        raise HTTPException(400, str(exc))
    return {
        "total_users": counts["total_users"],
        "inbound_tags": counts["inbound_tags"],
        "will_not_migrate": [
            "Конфигурация inbounds (создайте профили/сквады заранее)",
            "Reality-ключи (отдельный шаг «Перенести Reality»)",
            "История трафика",
        ],
    }


# ── reality ───────────────────────────────────────────────────
@router.post("/reality")
async def reality(body: RealityBody) -> dict:
    try:
        token = await marzban_migrate.marzban_login(
            body.marzban_url, body.marzban_username, body.marzban_password
        )
        core = await marzban_migrate.marzban_core_config(body.marzban_url, token)
    except marzban_migrate.MarzbanApiError as exc:
        raise HTTPException(400, str(exc))

    client = _remnawave_client(body.remnawave_url, body.remnawave_token)
    try:
        profile = await client.get_config_profile(body.config_profile_uuid)
        profile_config = profile.get("config") or {}
        patched, report = marzban_reality.build_reality_patch(core, profile_config)
        if report["matched"]:
            await client.update_config_profile(body.config_profile_uuid, patched)
    except RemnavaveError as exc:
        raise HTTPException(400, f"Remnawave: {exc.detail}")
    return {"applied": bool(report["matched"]), **report}


# ── run (streamed) ────────────────────────────────────────────
@router.post("/run")
async def run(body: RunBody, background_tasks: BackgroundTasks) -> dict:
    if not body.confirm:
        raise HTTPException(400, "Миграция пишет в прод-панель и требует confirm=true.")
    # SSRF-guard the account-supplied remnawave_url before the binary uses it.
    if not net_guard.is_safe_url(body.remnawave_url):
        raise HTTPException(400, "remnawave_url не разрешён (нужен публичный http(s)).")
    cfg = {
        "marzban_url": body.marzban_url,
        "marzban_username": body.marzban_username,
        "marzban_password": body.marzban_password,
        "remnawave_url": body.remnawave_url,
        "remnawave_token": body.remnawave_token,
        "preserve_status": body.preserve_status,
        "preserve_subhash": body.preserve_subhash,
        "internal_squad_uuids": body.internal_squad_uuids,
        "batch_size": body.batch_size,
        "image": _MIGRATE_IMAGE,  # pinned server-side (no arbitrary-image DooD run)
    }
    task = task_store.create(total_steps=1)
    background_tasks.add_task(marzban_migrate.run_migrate, task, cfg)
    return {"task_id": task.task_id, "task_type": "marzban-migrate"}


# ── legacy secret ─────────────────────────────────────────────
@router.post("/legacy-secret")
async def legacy_secret(body: LegacySecretBody) -> dict:
    ssh = SSHSession(body.ip, body.ssh_port, body.ssh_user, body.ssh_password)
    try:
        await ssh.connect()
        secret = (await ssh.get_output(marzban_reality.legacy_secret_cmd())).strip()
    except Exception as exc:
        raise HTTPException(502, f"SSH/чтение секрета не удалось: {exc}")
    finally:
        await ssh.close()
    if not secret:
        raise HTTPException(
            404, "Не удалось прочитать legacy secret_key (проверьте БД Marzban)."
        )
    return {"secret_key": secret, "env_hint": "MARZBAN_LEGACY_SECRET_KEY"}
