"""SSL management — deploy (issue + install) a cert onto a live node.

The «Управление SSL» section (Ф10). Unlike the old renew-only flow, this ISSUES
a fresh per-FQDN cert with the chosen provider (cloudflare DNS-01 / letsencrypt
HTTP-01 / zerossl EAB), reusing the deploy pipeline's `build_ssl_script` so both
paths share one source of truth. If the node already has a valid cert for the
domain (openssl probe), it reports that and skips — unless `force` is set.

Sub-steps (streamed via the generic /ws/logs task, own labels — not the 13-step
deploy numbering): 1 connect + probe, 2 issue+install, 3 restart services.
"""
import base64
import io
import re
import zipfile

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, field_validator

from app.models.deploy import DeployCertRequest
from app.services.task_store import task_store, TaskStatus
from app.services.ssh_manager import SSHSession
from app.services import pipeline
from app.services.cloudflare import upsert_a_record

router = APIRouter(prefix="/api")

# FQDN allowlist — `domain` is interpolated into a remote file path, so restrict
# it to hostname chars (no shell/path metacharacters) before use.
_DOMAIN_RE = re.compile(
    r"^[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)*\.[A-Za-z]{2,}$"
)
# Selectable cert files → (remote path template, download filename template).
_CERT_FILES = {
    "fullchain": ("/etc/ssl/certs/{d}_fullchain.pem", "{d}_fullchain.pem"),
    "key":       ("/etc/ssl/private/{d}.key",          "{d}.key"),
}

# Labels sent via WebSocket — must match RENEW_STEPS in StepProgress.tsx
DEPLOY_STEP_LABELS = [
    "Подключение к серверу",
    "Выпуск и установка сертификата",
    "Перезапуск сервисов",
]
DEPLOY_TOTAL = len(DEPLOY_STEP_LABELS)


@router.post("/certs/deploy")
async def deploy_cert(req: DeployCertRequest, background_tasks: BackgroundTasks):
    task = task_store.create(total_steps=DEPLOY_TOTAL)
    background_tasks.add_task(_deploy, req, task.task_id)
    return {"task_id": task.task_id, "task_type": "certs"}


async def _deploy(req: DeployCertRequest, task_id: str) -> None:
    task = task_store.get(task_id)
    if not task:
        return
    ssh = SSHSession(req.ip, req.ssh_port, req.ssh_user, req.ssh_password)
    try:
        # ── Step 1: connect + probe existing cert ─────────────
        task.set_step(1, TaskStatus.RUNNING)
        _log_step(task, 1, DEPLOY_STEP_LABELS[0])
        task.add_log(f"Подключение к {req.ip}:{req.ssh_port} как {req.ssh_user}...")
        await ssh.connect()
        os_info = await ssh.get_output(
            "cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"'"
        )
        task.add_log(f"\x1b[32mПодключено. ОС: {os_info or 'unknown'}\x1b[0m")

        existing = await _probe_cert(ssh, req.domain)
        if existing and not req.force:
            task.add_log(
                f"\x1b[1;33m[SSL] Сертификат для {req.domain} уже установлен "
                f"(истекает: {existing}). Передеплой пропущен — включите "
                f"«Принудительно», чтобы переустановить.\x1b[0m"
            )
            task.set_step(DEPLOY_TOTAL, TaskStatus.RUNNING)
            task.finish(TaskStatus.SUCCESS)
            return
        if existing:
            task.add_log(f"\x1b[33m[SSL] Сертификат уже есть (истекает {existing}) — принудительный передеплой.\x1b[0m")

        # ── Step 2: issue + install via the chosen provider ────
        task.set_step(2, TaskStatus.RUNNING)
        _log_step(task, 2, DEPLOY_STEP_LABELS[1])
        if pipeline.ssl_needs_cf_dns(req.cert_provider):
            task.add_log(f"[CF] Обновляю A-запись {req.domain} → {req.ip}...")
            await upsert_a_record(req.cf_api_key or "", req.domain, req.ip)
            task.add_log(f"\x1b[32m[CF] A-запись обновлена.\x1b[0m")
        else:
            task.add_log(
                f"\x1b[33m[SSL] Провайдер '{req.cert_provider}' использует HTTP-01 (порт 80). "
                f"Убедитесь, что {req.domain} уже указывает на {req.ip}.\x1b[0m"
            )
        script = pipeline.build_ssl_script(
            req.domain, req.email, req.cf_api_key or "", req.cert_provider
        )
        await ssh.run_script(script, task, timeout=360)

        # ── Step 3: restart services that use the cert ─────────
        task.set_step(3, TaskStatus.RUNNING)
        _log_step(task, 3, DEPLOY_STEP_LABELS[2])
        await _restart_services(ssh, task)

        task.finish(TaskStatus.SUCCESS)
        task.add_log("\n\x1b[1;32m✓ Сертификат задеплоен успешно!\x1b[0m")

    except Exception as exc:
        task.add_log(f"\n\x1b[1;31m✗ Ошибка: {exc}\x1b[0m")
        task.finish(TaskStatus.FAILED, str(exc))
    finally:
        await ssh.close()


# ── helpers ───────────────────────────────────────────────────

def _log_step(task, index: int, label: str) -> None:
    task.add_log(f"\n\x1b[36m{'─' * 56}\x1b[0m")
    task.add_log(f"\x1b[1;36m[{index}/{DEPLOY_TOTAL}] {label}\x1b[0m")
    task.add_log(f"\x1b[36m{'─' * 56}\x1b[0m")


async def _probe_cert(ssh: SSHSession, domain: str) -> str | None:
    """Return the installed cert's notAfter string for `domain`, or None if no
    valid cert is present (same path step_ssl installs to). Never raises."""
    script = (
        f'CERT="/etc/ssl/certs/{domain}_fullchain.pem"; '
        'if [ -s "$CERT" ]; then '
        'openssl x509 -enddate -noout -in "$CERT" 2>/dev/null | cut -d= -f2; '
        'fi'
    )
    try:
        out = (await ssh.get_output(script)).strip()
        return out or None
    except Exception:
        return None


async def _restart_services(ssh: SSHSession, task) -> None:
    """Detect running VPN/proxy services that use TLS certs and restart them."""
    candidates = [
        "nginx", "hysteria-server", "hysteria2", "remnawave-node",
        "xray", "sing-box", "v2ray",
    ]
    restart_script = ""
    for svc in candidates:
        restart_script += f"""\
if systemctl is-enabled {svc} &>/dev/null 2>&1 || systemctl is-active {svc} &>/dev/null 2>&1; then
    echo -n "  {svc}: "
    systemctl restart {svc} 2>&1 && echo "restarted" || echo "WARN: restart failed"
fi
"""
    full_script = f"""\
echo "Detecting running services..."
{restart_script}
echo ""
echo "Service summary:"
systemctl list-units --type=service --state=running \\
    | grep -E "nginx|hysteria|remna|xray|sing-box|v2ray" || echo "(none matched)"
"""
    await ssh.run_script(full_script, task, timeout=60)


# ── cert download (Ф8) ────────────────────────────────────────

class DownloadCertRequest(BaseModel):
    """Read a node's installed cert files over SSH and stream them back. SSH creds
    are per-request/transient (never persisted). `files` ⊆ {fullchain, key}."""
    ip: str
    ssh_user: str = "root"
    ssh_password: str
    ssh_port: int = 22
    domain: str
    files: list[str] = []

    @field_validator("domain")
    @classmethod
    def _validate_domain(cls, v: str) -> str:
        # fullmatch (not match): `$` alone would accept a trailing newline.
        if not _DOMAIN_RE.fullmatch(v):
            raise ValueError("Invalid domain (hostname expected)")
        return v


async def _read_remote_file(ssh: SSHSession, path: str) -> bytes | None:
    """base64-read a remote file SILENTLY (get_output logs nothing — the private
    key must never hit a task log). Returns None if the file is absent/empty."""
    # `path` is safe: domain is FQDN-validated, the rest is a fixed literal.
    # Cap the read at 8 MiB (certs are KB) so a stray huge file at the cert path
    # can't OOM the backend buffering all stdout.
    script = f'F="{path}"; if [ -s "$F" ]; then echo __OK__; head -c 8388608 "$F" | base64; else echo __MISSING__; fi'
    out = await ssh.get_output(script)
    if "__OK__" not in out:
        return None
    b64 = out.split("__OK__", 1)[1].strip()
    try:
        return base64.b64decode(b64)
    except Exception:
        return None


@router.post("/certs/download")
async def download_cert(req: DownloadCertRequest):
    sel = [f for f in req.files if f in _CERT_FILES]
    if not sel:
        raise HTTPException(422, "Не выбраны файлы для скачивания")

    ssh = SSHSession(req.ip, req.ssh_port, req.ssh_user, req.ssh_password)
    collected: list[tuple[str, bytes]] = []
    try:
        await ssh.connect()
        for f in sel:
            path_tpl, name_tpl = _CERT_FILES[f]
            data = await _read_remote_file(ssh, path_tpl.format(d=req.domain))
            name = name_tpl.format(d=req.domain)
            if data is None:
                raise HTTPException(404, f"Сертификат не найден на ноде: {name}")
            collected.append((name, data))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Не удалось прочитать сертификаты по SSH: {str(exc)[:200]}")
    finally:
        await ssh.close()

    # Single file → return it directly; multiple → zip them.
    if len(collected) == 1:
        name, data = collected[0]
        return Response(
            content=data, media_type="application/x-pem-file",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in collected:
            zf.writestr(name, data)
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{req.domain}-certs.zip"'},
    )
