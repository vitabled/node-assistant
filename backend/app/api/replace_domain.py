"""Wave-4 Plan E (E7) — /api/replace-domain: change a node's or panel's domain.

Streamed Tasks (own labels via the generic /ws/logs stream). Re-issues the cert
for the new FQDN (reusing pipeline.build_ssl_script), rewrites the on-box config
(scoped sed), and restarts the compose stack. SSH creds are per-request and
never persisted.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel, Field, field_validator

from app.services import pipeline, replace_domain
from app.services.cloudflare import upsert_a_record
from app.services.ssh_manager import SSHSession
from app.services.task_store import task_store, TaskStatus

router = APIRouter(prefix="/api/replace-domain")

_PROVIDERS = ("cloudflare", "letsencrypt", "zerossl")


def _fqdn_field(v: str) -> str:
    if not replace_domain.is_fqdn(v):
        raise ValueError("Invalid domain (hostname expected)")
    return v


class ReplaceDomainNodeRequest(BaseModel):
    """Change a remnanode's domain. `old_domain` optional (auto-detected on box)."""

    ip: str
    ssh_user: str = "root"
    ssh_password: str = Field(..., min_length=1)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    old_domain: str = ""
    new_domain: str
    cert_provider: str = "cloudflare"
    email: str = ""
    cf_api_key: Optional[str] = None

    @field_validator("new_domain")
    @classmethod
    def _v_new(cls, v: str) -> str:
        return _fqdn_field(v)

    @field_validator("old_domain")
    @classmethod
    def _v_old(cls, v: str) -> str:
        if v and not replace_domain.is_fqdn(v):
            raise ValueError("Invalid old domain")
        return v

    @field_validator("cert_provider")
    @classmethod
    def _v_provider(cls, v: str) -> str:
        if v not in _PROVIDERS:
            raise ValueError(f"cert_provider must be one of {_PROVIDERS}")
        return v


class ReplaceDomainPanelRequest(BaseModel):
    """Change a panel's front-end and/or subscription domain. At least one of
    new_panel_domain / new_sub_domain must be set. `reverse_proxy=caddy` → cert is
    auto-issued by Caddy (we skip acme); `nginx` → we re-issue via build_ssl_script."""

    ip: str
    ssh_user: str = "root"
    ssh_password: str = Field(..., min_length=1)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    reverse_proxy: str = "caddy"
    old_panel_domain: str = ""
    new_panel_domain: str = ""
    old_sub_domain: str = ""
    new_sub_domain: str = ""
    cert_provider: str = "cloudflare"
    email: str = ""
    cf_api_key: Optional[str] = None

    @field_validator("new_panel_domain", "new_sub_domain", "old_panel_domain", "old_sub_domain")
    @classmethod
    def _v_opt(cls, v: str) -> str:
        if v and not replace_domain.is_fqdn(v):
            raise ValueError("Invalid domain")
        return v

    @field_validator("reverse_proxy")
    @classmethod
    def _v_rp(cls, v: str) -> str:
        if v not in ("caddy", "nginx"):
            raise ValueError("reverse_proxy must be caddy or nginx")
        return v


# ── Node ──────────────────────────────────────────────────────────
NODE_STEPS = ["Подключение", "Выпуск сертификата", "Смена домена и рестарт"]


@router.post("/node")
async def replace_node_domain(req: ReplaceDomainNodeRequest, bg: BackgroundTasks) -> dict:
    task = task_store.create(total_steps=len(NODE_STEPS))
    bg.add_task(_run_node, req, task.task_id)
    return {"task_id": task.task_id, "task_type": "replace-domain"}


async def _run_node(req: ReplaceDomainNodeRequest, task_id: str) -> None:
    task = task_store.get(task_id)
    if not task:
        return
    ssh = SSHSession(req.ip, req.ssh_port, req.ssh_user, req.ssh_password)
    try:
        task.set_step(1, TaskStatus.RUNNING)
        task.add_log(f"\x1b[1;36m[1/3] Подключение к {req.ip}:{req.ssh_port}\x1b[0m")
        await ssh.connect()
        task.add_log("\x1b[32mПодключено.\x1b[0m")

        task.set_step(2, TaskStatus.RUNNING)
        task.add_log(f"\x1b[1;36m[2/3] Выпуск сертификата для {req.new_domain}\x1b[0m")
        if pipeline.ssl_needs_cf_dns(req.cert_provider):
            task.add_log(f"[CF] A-запись {req.new_domain} → {req.ip}...")
            await upsert_a_record(req.cf_api_key or "", req.new_domain, req.ip)
        else:
            task.add_log(
                f"\x1b[33m[SSL] HTTP-01: убедитесь, что {req.new_domain} уже указывает на {req.ip}.\x1b[0m"
            )
        await ssh.run_script(
            pipeline.build_ssl_script(req.new_domain, req.email, req.cf_api_key or "", req.cert_provider),
            task, timeout=360,
        )

        task.set_step(3, TaskStatus.RUNNING)
        task.add_log(f"\x1b[1;36m[3/3] Смена домена в конфигах ноды и рестарт\x1b[0m")
        await ssh.run_script(
            replace_domain.node_replace_script(req.old_domain, req.new_domain),
            task, timeout=180,
        )

        task.finish(TaskStatus.SUCCESS)
        task.add_log(f"\n\x1b[1;32m✓ Домен ноды сменён на {req.new_domain}.\x1b[0m")
    except Exception as exc:
        task.add_log(f"\n\x1b[1;31m✗ Ошибка: {exc}\x1b[0m")
        task.finish(TaskStatus.FAILED, str(exc))
    finally:
        await ssh.close()


# ── Panel ─────────────────────────────────────────────────────────
PANEL_STEPS = ["Подключение", "Сертификаты", "Смена домена и рестарт"]


@router.post("/panel")
async def replace_panel_domain(req: ReplaceDomainPanelRequest, bg: BackgroundTasks) -> dict:
    task = task_store.create(total_steps=len(PANEL_STEPS))
    bg.add_task(_run_panel, req, task.task_id)
    return {"task_id": task.task_id, "task_type": "replace-domain"}


async def _run_panel(req: ReplaceDomainPanelRequest, task_id: str) -> None:
    task = task_store.get(task_id)
    if not task:
        return
    if not (req.new_panel_domain or req.new_sub_domain):
        task.add_log("\x1b[1;31m✗ Не указан ни новый домен панели, ни подписки.\x1b[0m")
        task.finish(TaskStatus.FAILED, "no new domain")
        return
    ssh = SSHSession(req.ip, req.ssh_port, req.ssh_user, req.ssh_password)
    try:
        task.set_step(1, TaskStatus.RUNNING)
        task.add_log(f"\x1b[1;36m[1/3] Подключение к {req.ip}:{req.ssh_port}\x1b[0m")
        await ssh.connect()
        task.add_log("\x1b[32mПодключено.\x1b[0m")

        task.set_step(2, TaskStatus.RUNNING)
        task.add_log("\x1b[1;36m[2/3] Сертификаты\x1b[0m")
        if req.reverse_proxy == "caddy":
            task.add_log("\x1b[33m[SSL] Caddy выпустит сертификат автоматически при рестарте.\x1b[0m")
        else:
            for dom in (req.new_panel_domain, req.new_sub_domain):
                if not dom:
                    continue
                if pipeline.ssl_needs_cf_dns(req.cert_provider):
                    task.add_log(f"[CF] A-запись {dom} → {req.ip}...")
                    await upsert_a_record(req.cf_api_key or "", dom, req.ip)
                await ssh.run_script(
                    pipeline.build_ssl_script(dom, req.email, req.cf_api_key or "", req.cert_provider),
                    task, timeout=360,
                )

        task.set_step(3, TaskStatus.RUNNING)
        task.add_log("\x1b[1;36m[3/3] Смена домена в .env/compose/reverse-proxy и рестарт\x1b[0m")
        await ssh.run_script(
            replace_domain.panel_replace_script(
                req.old_panel_domain, req.new_panel_domain,
                req.old_sub_domain, req.new_sub_domain,
            ),
            task, timeout=300,
        )

        task.finish(TaskStatus.SUCCESS)
        task.add_log("\n\x1b[1;32m✓ Домен панели обновлён.\x1b[0m")
    except Exception as exc:
        task.add_log(f"\n\x1b[1;31m✗ Ошибка: {exc}\x1b[0m")
        task.finish(TaskStatus.FAILED, str(exc))
    finally:
        await ssh.close()
