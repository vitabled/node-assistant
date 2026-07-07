"""Test-server registry routes (Ф1, wave1) — /api/testservers.

  GET    /api/testservers            — the account's registered test servers
  POST   /api/testservers            — register an ALREADY provisioned server by IP
  DELETE /api/testservers/{id}       — drop a server from the registry
  POST   /api/testservers/deploy     — SSH-provision a server (streamed Task),
                                       then register it. SSH creds are transient
                                       (per-request, never persisted).

The deploy allowlist (UFW access to the iperf3 port) = the caller's node IPs +
the backend's own external IP (added automatically).
"""

from __future__ import annotations

import ipaddress

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.services import accounts, testserver_registry
from app.services.backend_ip import get_backend_ip
from app.services.ssh_manager import SSHSession
from app.services.task_store import TaskStatus, task_store

router = APIRouter(prefix="/api/testservers")

DEPLOY_STEP_LABELS = [
    "Подключение к серверу",
    "Установка тест-инструментов и iperf3-сервера",
    "Регистрация тест-сервера",
]


def _validate_ip(v: str) -> str:
    v = v.strip()
    try:
        ipaddress.ip_address(v)
    except ValueError:
        raise ValueError("Некорректный IP-адрес")
    return v


class ServerCreate(BaseModel):
    name: str = ""
    ip: str
    iperf_port: int = Field(5201, ge=1, le=65535)

    _ip = field_validator("ip")(_validate_ip)


class ServerDeploy(BaseModel):
    name: str = ""
    ip: str
    ssh_user: str = "root"
    ssh_password: str
    ssh_port: int = Field(22, ge=1, le=65535)
    iperf_port: int = Field(5201, ge=1, le=65535)
    allow_ips: list[str] = []

    _ip = field_validator("ip")(_validate_ip)


@router.get("")
async def list_servers() -> dict:
    aid = accounts.current_account.get() or ""
    return {"servers": testserver_registry.list_servers(aid)}


@router.post("")
async def add_server(body: ServerCreate) -> dict:
    aid = accounts.current_account.get() or ""
    try:
        return testserver_registry.add_server(body.name, body.ip, body.iperf_port, aid)
    except ValueError as exc:
        # ip/port are pydantic-validated above → a ValueError here is a duplicate
        raise HTTPException(409, str(exc))


@router.delete("/{server_id}")
async def delete_server(server_id: str) -> dict:
    aid = accounts.current_account.get() or ""
    if not testserver_registry.remove_server(server_id, aid):
        raise HTTPException(404, "Тест-сервер не найден")
    return {"ok": True}


@router.post("/deploy")
async def deploy_server(body: ServerDeploy, background_tasks: BackgroundTasks) -> dict:
    """SSH-provision a test server (streamed Task) and register it on success."""
    aid = accounts.current_account.get() or ""
    task = task_store.create(total_steps=len(DEPLOY_STEP_LABELS))
    background_tasks.add_task(_deploy, body, task.task_id, aid)
    return {"task_id": task.task_id, "task_type": "testserver"}


async def _deploy(req: ServerDeploy, task_id: str, account_id: str) -> None:
    task = task_store.get(task_id)
    if not task:
        return
    ssh = SSHSession(req.ip, req.ssh_port, req.ssh_user, req.ssh_password)
    try:
        # ── Step 1: connect ────────────────────────────────────
        task.set_step(1, TaskStatus.RUNNING)
        _log_step(task, 1)
        task.add_log(f"Подключение к {req.ip}:{req.ssh_port} как {req.ssh_user}...")
        await ssh.connect()
        task.add_log("\x1b[32mПодключено.\x1b[0m")

        # ── Step 2: install tools + iperf3 service + UFW allow ─
        task.set_step(2, TaskStatus.RUNNING)
        _log_step(task, 2)
        allow = list(req.allow_ips)
        backend = await get_backend_ip()
        if backend:
            allow.append(backend)
        script = testserver_registry.deploy_script(req.iperf_port, allow)
        await ssh.run_script(script, task, timeout=600)

        # ── Step 3: register in the account's registry ─────────
        task.set_step(3, TaskStatus.RUNNING)
        _log_step(task, 3)
        try:
            srv = testserver_registry.add_server(
                req.name, req.ip, req.iperf_port, account_id
            )
            task.add_log(
                f"\x1b[32mТест-сервер зарегистрирован: {srv['name']} "
                f"({srv['ip']}:{srv['iperf_port']})\x1b[0m"
            )
        except ValueError as exc:
            # already registered (redeploy) — fine, the box is provisioned
            task.add_log(f"\x1b[33m[warn] {exc} — запись уже в реестре.\x1b[0m")

        task.finish(TaskStatus.SUCCESS)
        task.add_log("\n\x1b[1;32m✓ Тест-сервер развёрнут успешно!\x1b[0m")
    except Exception as exc:
        task.add_log(f"\n\x1b[1;31m✗ Ошибка: {exc}\x1b[0m")
        task.finish(TaskStatus.FAILED, str(exc))
    finally:
        await ssh.close()


def _log_step(task, index: int) -> None:
    task.add_log(f"\n\x1b[36m{'─' * 56}\x1b[0m")
    task.add_log(
        f"\x1b[1;36m[{index}/{len(DEPLOY_STEP_LABELS)}] {DEPLOY_STEP_LABELS[index - 1]}\x1b[0m"
    )
    task.add_log(f"\x1b[36m{'─' * 56}\x1b[0m")
