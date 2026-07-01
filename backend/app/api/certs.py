"""
Certificate renewal endpoint.

Sub-steps:
  1. Connect + verify prerequisites (acme.sh installed, cert exists)
  2. Renew via acme.sh DNS-01 challenge (ECC cert)
  3. Install cert to paths + auto-restart affected services

Uses STEP_LABELS defined locally (not the 8-step deployment labels).
"""
from fastapi import APIRouter, BackgroundTasks
from app.models.deploy import RenewCertsRequest
from app.services.task_store import task_store, TaskStatus
from app.services.ssh_manager import SSHSession

router = APIRouter(prefix="/api")

# Labels sent via WebSocket — must match RENEW_STEPS in StepProgress.tsx
RENEW_STEP_LABELS = [
    "Подключение к серверу",
    "Обновление сертификата",
    "Перезапуск сервисов",
]
RENEW_TOTAL = len(RENEW_STEP_LABELS)


@router.post("/certs/renew")
async def renew_certs(req: RenewCertsRequest, background_tasks: BackgroundTasks):
    task = task_store.create(total_steps=RENEW_TOTAL)
    background_tasks.add_task(_renew, req, task.task_id)
    return {"task_id": task.task_id, "task_type": "certs"}


async def _renew(req: RenewCertsRequest, task_id: str) -> None:
    task = task_store.get(task_id)
    if not task:
        return

    root_domain = ".".join(req.domain.split(".")[-2:])
    ssh = SSHSession(req.ip, req.ssh_port, req.ssh_user, req.ssh_password)

    try:
        # ── Step 1: Connect + verify ──────────────────────────
        task.set_step(1, TaskStatus.RUNNING)
        _log_step(task, 1, RENEW_STEP_LABELS[0])

        task.add_log(f"Connecting to {req.ip}:{req.ssh_port} as {req.ssh_user}...")
        await ssh.connect()

        os_info = await ssh.get_output(
            "cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"'"
        )
        task.add_log(f"\x1b[32mConnected. OS: {os_info or 'unknown'}\x1b[0m")

        await _verify_prerequisites(ssh, task, root_domain)

        # ── Step 2: Renew certificate ─────────────────────────
        task.set_step(2, TaskStatus.RUNNING)
        _log_step(task, 2, RENEW_STEP_LABELS[1])

        await _do_renew(ssh, task, root_domain, req.cf_api_key)

        # ── Step 3: Restart services ──────────────────────────
        task.set_step(3, TaskStatus.RUNNING)
        _log_step(task, 3, RENEW_STEP_LABELS[2])

        await _restart_services(ssh, task)

        task.finish(TaskStatus.SUCCESS)
        task.add_log("\n\x1b[1;32m✓ Сертификаты обновлены успешно!\x1b[0m")

    except Exception as exc:
        task.add_log(f"\n\x1b[1;31m✗ Ошибка: {exc}\x1b[0m")
        task.finish(TaskStatus.FAILED, str(exc))
    finally:
        await ssh.close()


# ── helpers ───────────────────────────────────────────────────

def _log_step(task, index: int, label: str) -> None:
    task.add_log(f"\n\x1b[36m{'─' * 56}\x1b[0m")
    task.add_log(f"\x1b[1;36m[{index}/{RENEW_TOTAL}] {label}\x1b[0m")
    task.add_log(f"\x1b[36m{'─' * 56}\x1b[0m")


async def _verify_prerequisites(ssh: SSHSession, task, root_domain: str) -> None:
    """Check acme.sh is installed and a cert exists for the domain."""
    acme = await ssh.get_output("[ -f /root/.acme.sh/acme.sh ] && echo YES || echo NO")
    if acme != "YES":
        raise RuntimeError(
            "acme.sh не установлен на сервере. "
            "Сначала выполните полный деплой через вкладку «Деплой ноды»."
        )

    cert_list = await ssh.get_output(
        f"/root/.acme.sh/acme.sh --list 2>/dev/null | grep -F '{root_domain}' | head -3"
    )
    if not cert_list:
        raise RuntimeError(
            f"Сертификат для {root_domain} не найден в acme.sh. "
            "Возможно, домен указан неверно или деплой ещё не выполнялся."
        )

    task.add_log(f"\x1b[32m[verify] Сертификат найден:\x1b[0m")
    for line in cert_list.splitlines():
        task.add_log(f"  {line}")


async def _do_renew(
    ssh: SSHSession,
    task,
    root_domain: str,
    cf_api_key: str | None,
) -> None:
    """Renew cert via acme.sh. Optionally override CF_Token."""
    cf_export = f'export CF_Token="{cf_api_key}"' if cf_api_key else "# using stored CF_Token"

    renew_script = f"""\
{cf_export}

# --renew respects the 30-day renewal window; only re-issues when needed.
# We call --force to always update on manual trigger.
if /root/.acme.sh/acme.sh --renew -d "{root_domain}" --ecc --force 2>&1; then
    echo "[acme] Renewal completed."
else
    echo "[acme] --force renewal failed; cert may still be valid."
fi

# Re-install to the paths the services expect
mkdir -p /etc/ssl/certs /etc/ssl/private

/root/.acme.sh/acme.sh --install-cert -d "{root_domain}" --ecc \\
    --cert-file      /etc/ssl/certs/{root_domain}.crt \\
    --key-file       /etc/ssl/private/{root_domain}.key \\
    --fullchain-file /etc/ssl/certs/{root_domain}_fullchain.pem 2>&1

chmod 600 /etc/ssl/private/{root_domain}.key 2>/dev/null || true

echo ""
echo "[acme] Installed cert details:"
openssl x509 -in /etc/ssl/certs/{root_domain}_fullchain.pem \\
    -noout -subject -dates 2>/dev/null || true
"""
    await ssh.run_script(renew_script, task, timeout=300)


async def _restart_services(ssh: SSHSession, task) -> None:
    """Detect which VPN/proxy services are running and reload/restart them."""

    # Services that use TLS certs and need a restart after renewal
    candidates = [
        "nginx",
        "hysteria-server",   # our manual-install unit name
        "hysteria2",         # vitabled script unit name
        "remnawave-node",    # remnanode
        "xray",
        "sing-box",
        "v2ray",
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
