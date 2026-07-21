"""Wave-4 Plan D (E6) — /api/certwarden: centralised ACME (Certwarden).

Deploy the Certwarden server on a chosen box, and install our pull-and-restart
client on a node. SSH creds are per-request/transient. The node's download
API-keys go through the SILENT SSH channel (never logged) and are charset-guarded
before interpolation.
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.services import accounts, certwarden
from app.services.replace_domain import is_fqdn
from app.services.ssh_manager import SSHSession
from app.services.task_store import task_store, TaskStatus

router = APIRouter(prefix="/api/certwarden")

_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_URL_RE = re.compile(r"^https?://[A-Za-z0-9._:-]+(/.*)?$")


def _ipv4(v: str) -> str:
    if not re.fullmatch(r"^(\d{1,3}\.){3}\d{1,3}$", v) or any(int(p) > 255 for p in v.split(".")):
        raise ValueError("Invalid IPv4 address")
    return v


class CertwardenServerDeployRequest(BaseModel):
    ip: str
    ssh_user: str = "root"
    ssh_password: str = Field(..., min_length=1)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    placement: str = "dedicated"          # panel | dedicated (registry label)
    server_url: str                       # base URL operators/nodes will use
    domain: str = ""                      # server FQDN (optional, for registry)

    @field_validator("ip")
    @classmethod
    def _v_ip(cls, v: str) -> str:
        return _ipv4(v)

    @field_validator("placement")
    @classmethod
    def _v_pl(cls, v: str) -> str:
        if v not in ("panel", "dedicated"):
            raise ValueError("placement must be panel|dedicated")
        return v

    @field_validator("server_url")
    @classmethod
    def _v_url(cls, v: str) -> str:
        if not _URL_RE.fullmatch(v):
            raise ValueError("server_url must be a http(s) URL")
        return v

    @field_validator("domain")
    @classmethod
    def _v_dom(cls, v: str) -> str:
        if v and not is_fqdn(v):
            raise ValueError("Invalid domain")
        return v


class CertwardenClientInstallRequest(BaseModel):
    ip: str
    ssh_user: str = "root"
    ssh_password: str = Field(..., min_length=1)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    server_url: str
    domain: str
    cert_name: str
    key_name: str
    cert_apikey: str
    key_apikey: str
    restart_containers: list[str] = Field(default_factory=list)

    @field_validator("ip")
    @classmethod
    def _v_ip(cls, v: str) -> str:
        return _ipv4(v)

    @field_validator("server_url")
    @classmethod
    def _v_url(cls, v: str) -> str:
        if not _URL_RE.fullmatch(v):
            raise ValueError("server_url must be a http(s) URL")
        return v

    @field_validator("domain")
    @classmethod
    def _v_dom(cls, v: str) -> str:
        if not is_fqdn(v):
            raise ValueError("Invalid domain")
        return v

    @field_validator("cert_name", "key_name", "cert_apikey", "key_apikey")
    @classmethod
    def _v_name(cls, v: str) -> str:
        if not _NAME_RE.fullmatch(v):
            raise ValueError("Invalid name/key (charset [A-Za-z0-9._-])")
        return v

    @field_validator("restart_containers")
    @classmethod
    def _v_containers(cls, v: list[str]) -> list[str]:
        for c in v:
            if not _NAME_RE.fullmatch(c):
                raise ValueError(f"Invalid container name: {c!r}")
        return v


@router.get("/server")
async def get_cw_server() -> dict:
    return certwarden.get_server()


@router.delete("/server")
async def delete_cw_server() -> dict:
    certwarden.clear_server()
    return {"ok": True}


@router.post("/server/deploy")
async def deploy_cw_server(req: CertwardenServerDeployRequest, bg: BackgroundTasks) -> dict:
    account_id = accounts.current_account.get()
    task = task_store.create(total_steps=1)
    bg.add_task(_run_server_deploy, req, task.task_id, account_id)
    return {"task_id": task.task_id, "task_type": "certwarden"}


async def _run_server_deploy(
    req: CertwardenServerDeployRequest, task_id: str, account_id: Optional[str]
) -> None:
    task = task_store.get(task_id)
    if not task:
        return
    ssh = SSHSession(req.ip, req.ssh_port, req.ssh_user, req.ssh_password)
    try:
        task.set_step(1, TaskStatus.RUNNING)
        task.add_log(f"\x1b[1;36mРазвёртывание Certwarden-сервера на {req.ip} ({req.placement})\x1b[0m")
        await ssh.connect()
        await ssh.run_script(certwarden.server_deploy_script(), task, timeout=420)
        certwarden.set_server(req.placement, req.server_url, req.domain, account_id=account_id)
        task.finish(TaskStatus.SUCCESS)
        task.add_log(f"\n\x1b[1;32m✓ Certwarden-сервер запущен. UI: {req.server_url}\x1b[0m")
    except Exception as exc:
        task.add_log(f"\n\x1b[1;31m✗ Ошибка: {exc}\x1b[0m")
        task.finish(TaskStatus.FAILED, str(exc))
    finally:
        await ssh.close()


@router.post("/client/install")
async def install_cw_client(req: CertwardenClientInstallRequest, bg: BackgroundTasks) -> dict:
    task = task_store.create(total_steps=1)
    bg.add_task(_run_client_install, req, task.task_id)
    return {"task_id": task.task_id, "task_type": "certwarden"}


async def _run_client_install(req: CertwardenClientInstallRequest, task_id: str) -> None:
    task = task_store.get(task_id)
    if not task:
        return
    ssh = SSHSession(req.ip, req.ssh_port, req.ssh_user, req.ssh_password)
    try:
        task.set_step(1, TaskStatus.RUNNING)
        task.add_log(f"\x1b[1;36mУстановка Certwarden-клиента на {req.ip} для {req.domain}\x1b[0m")
        task.add_log("Подключение...")
        await ssh.connect()
        task.add_log("\x1b[32mПодключено. Тяну сертификат (ключи не логируются)...\x1b[0m")
        # SILENT channel — the API keys must never hit the task log.
        out = await ssh.get_script_output(
            certwarden.client_install_script(
                req.server_url, req.domain, req.cert_name, req.key_name,
                req.cert_apikey, req.key_apikey, req.restart_containers or None,
            ),
            timeout=120,
        )
        if "__CW_CLIENT_OK__" not in out:
            raise RuntimeError("Клиент не смог получить сертификат — проверьте URL/имена/ключи.")
        task.finish(TaskStatus.SUCCESS)
        task.add_log(f"\n\x1b[1;32m✓ Certwarden-клиент установлен, сертификат для {req.domain} получен, cron настроен.\x1b[0m")
    except Exception as exc:
        task.add_log(f"\n\x1b[1;31m✗ Ошибка: {exc}\x1b[0m")
        task.finish(TaskStatus.FAILED, str(exc))
    finally:
        await ssh.close()
