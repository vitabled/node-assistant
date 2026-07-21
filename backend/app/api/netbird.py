"""Wave-4 Plan F (E8) — /api/netbird: self-hosted Netbird mesh.

Deploy the control plane on a box, store a service-user PAT (encrypted), mint
setup-keys via the management API, and join nodes as agents (without hijacking
the default route → SSH stays alive). SSH creds are per-request/transient; the
PAT lives Fernet-encrypted in the per-account registry and is never returned.
"""
from __future__ import annotations

import re
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.services import accounts, netbird
from app.services.net_guard import is_safe_url
from app.services.replace_domain import is_fqdn
from app.services.ssh_manager import SSHSession
from app.services.task_store import task_store, TaskStatus

router = APIRouter(prefix="/api/netbird")


def _ipv4(v: str) -> str:
    if not re.fullmatch(r"^(\d{1,3}\.){3}\d{1,3}$", v) or any(int(p) > 255 for p in v.split(".")):
        raise ValueError("Invalid IPv4 address")
    return v


class ControlPlaneDeployRequest(BaseModel):
    ip: str
    ssh_user: str = "root"
    ssh_password: str = Field(..., min_length=1)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    domain: str
    email: str = ""

    @field_validator("ip")
    @classmethod
    def _v_ip(cls, v: str) -> str:
        return _ipv4(v)

    @field_validator("domain")
    @classmethod
    def _v_dom(cls, v: str) -> str:
        if not is_fqdn(v):
            raise ValueError("Invalid domain (public FQDN required)")
        return v

    @field_validator("email")
    @classmethod
    def _v_email(cls, v: str) -> str:
        if v and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
            raise ValueError("Invalid email")
        return v


class PatRequest(BaseModel):
    pat: str = Field(..., min_length=8)


class SetupKeyRequest(BaseModel):
    name: str = "node-assistant"

    @field_validator("name")
    @classmethod
    def _v_name(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9 ._-]{1,60}", v):
            raise ValueError("Invalid setup-key name")
        return v


class AgentJoinRequest(BaseModel):
    ip: str
    ssh_user: str = "root"
    ssh_password: str = Field(..., min_length=1)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    setup_key: str = Field(..., min_length=8)
    management_url: str = ""   # falls back to the registered control plane

    @field_validator("ip")
    @classmethod
    def _v_ip(cls, v: str) -> str:
        return _ipv4(v)

    @field_validator("setup_key")
    @classmethod
    def _v_key(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9._-]{8,200}", v):
            raise ValueError("Invalid setup key")
        return v

    @field_validator("management_url")
    @classmethod
    def _v_mgmt(cls, v: str) -> str:
        if v and not re.fullmatch(r"^https://[A-Za-z0-9._:-]+(/.*)?$", v):
            raise ValueError("management_url must be an https URL")
        return v


@router.get("/control-plane")
async def get_cp() -> dict:
    return netbird.public_control_plane()


@router.delete("/control-plane")
async def delete_cp() -> dict:
    netbird.clear_control_plane()
    return {"ok": True}


@router.post("/control-plane/deploy")
async def deploy_cp(req: ControlPlaneDeployRequest, bg: BackgroundTasks) -> dict:
    account_id = accounts.current_account.get()
    task = task_store.create(total_steps=1)
    bg.add_task(_run_cp_deploy, req, task.task_id, account_id)
    return {"task_id": task.task_id, "task_type": "netbird"}


async def _run_cp_deploy(
    req: ControlPlaneDeployRequest, task_id: str, account_id: Optional[str]
) -> None:
    task = task_store.get(task_id)
    if not task:
        return
    ssh = SSHSession(req.ip, req.ssh_port, req.ssh_user, req.ssh_password)
    try:
        task.set_step(1, TaskStatus.RUNNING)
        task.add_log(f"\x1b[1;36mРазвёртывание Netbird control plane на {req.ip} ({req.domain})\x1b[0m")
        await ssh.connect()
        await ssh.run_script(
            netbird.control_plane_deploy_script(req.domain, req.email), task, timeout=600
        )
        netbird.set_control_plane(req.domain, account_id=account_id)
        task.finish(TaskStatus.SUCCESS)
        task.add_log(
            f"\n\x1b[1;32m✓ Netbird запущен. Dashboard: https://{req.domain} — "
            f"войдите, создайте service-user PAT и сохраните его в панели.\x1b[0m"
        )
    except Exception as exc:
        task.add_log(f"\n\x1b[1;31m✗ Ошибка: {exc}\x1b[0m")
        task.finish(TaskStatus.FAILED, str(exc))
    finally:
        await ssh.close()


@router.put("/pat")
async def put_pat(req: PatRequest) -> dict:
    if not netbird.get_control_plane():
        raise HTTPException(400, "Сначала разверните control plane.")
    netbird.set_pat(req.pat)
    return {"ok": True}


@router.post("/setup-key")
async def create_setup_key(req: SetupKeyRequest) -> dict:
    cp = netbird.get_control_plane()
    if not cp:
        raise HTTPException(400, "Control plane не развёрнут.")
    pat = netbird.get_pat()
    if not pat:
        raise HTTPException(400, "PAT не сохранён — создайте его в Dashboard и сохраните в панели.")
    mgmt = cp.get("management_url", "")
    if not is_safe_url(mgmt):
        raise HTTPException(400, "management_url недоступен/небезопасен.")
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as cli:
            r = await cli.post(
                f"{mgmt}/api/setup-keys",
                headers={"Authorization": f"Token {pat}", "Content-Type": "application/json"},
                json=netbird.setup_key_payload(req.name),
            )
    except Exception as exc:
        raise HTTPException(502, f"Netbird API недоступен: {str(exc)[:150]}")
    if r.status_code >= 300:
        raise HTTPException(502, f"Netbird API вернул {r.status_code} (проверьте PAT).")
    key = (r.json() or {}).get("key")
    if not key:
        raise HTTPException(502, "Netbird API не вернул setup-key.")
    return {"key": key}


@router.post("/agent/join")
async def join_agent(req: AgentJoinRequest, bg: BackgroundTasks) -> dict:
    mgmt = req.management_url or netbird.get_control_plane().get("management_url", "")
    if not mgmt:
        raise HTTPException(400, "Не задан management_url и control plane не развёрнут.")
    task = task_store.create(total_steps=1)
    bg.add_task(_run_agent_join, req, mgmt, task.task_id)
    return {"task_id": task.task_id, "task_type": "netbird"}


async def _run_agent_join(req: AgentJoinRequest, mgmt: str, task_id: str) -> None:
    task = task_store.get(task_id)
    if not task:
        return
    ssh = SSHSession(req.ip, req.ssh_port, req.ssh_user, req.ssh_password)
    try:
        task.set_step(1, TaskStatus.RUNNING)
        task.add_log(f"\x1b[1;36mПодключение ноды {req.ip} в mesh ({mgmt})\x1b[0m")
        await ssh.connect()
        out = await ssh.get_script_output(
            netbird.agent_install_script(mgmt, req.setup_key), timeout=180
        )
        if "__NB_AGENT_OK__" not in out:
            raise RuntimeError("Агент не поднялся — проверьте setup-key/management-url.")
        peer_ip = netbird.parse_peer_ip(out)
        task.finish(TaskStatus.SUCCESS)
        task.add_log(
            f"\n\x1b[1;32m✓ Нода в mesh. Оверлейный IP: {peer_ip or 'неизвестно'} "
            f"(SSH сохранён — дефолт-роут не перехвачен).\x1b[0m"
        )
    except Exception as exc:
        task.add_log(f"\n\x1b[1;31m✗ Ошибка: {exc}\x1b[0m")
        task.finish(TaskStatus.FAILED, str(exc))
    finally:
        await ssh.close()
