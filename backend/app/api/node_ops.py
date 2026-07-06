"""Per-component management of an ALREADY-DEPLOYED node.

Lets a SUCCESS node's card reinstall / reconfigure / uninstall individual
components (Node Accelerator, TrafficGuard, Remnanode, Masking, WARP, Hysteria2,
SSL, HAProxy) against the live server, using SSH creds passed per-request from
the browser's localStorage (never persisted — same rule as /api/stats/node).

The op runs as a streamed background Task (reuses the generic /ws/logs/{task_id}
WebSocket). `reinstall`/`reconfigure` re-run the pipeline's own `step_*` builders
idempotently (single source of truth); `uninstall` runs dedicated teardown
scripts (the pipeline has no uninstall path of its own).

Steps 1 «Подключение» and 2 «Обновление системы» are NOT manageable (no entry
here); the SSH-port network steps are intentionally excluded too — rolling the
port back on a live box is lockout-prone and out of scope.
"""
from typing import Literal

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from app.models.deploy import DeployRequest
from app.services.ssh_manager import SSHSession
from app.services.task_store import task_store, TaskStatus
from app.services.backend_ip import get_backend_ip
from app.services import pipeline

router = APIRouter(prefix="/api/node")

Component = Literal[
    "node_accelerator", "trafficguard", "remnanode",
    "masking", "warp", "hysteria2", "ssl", "haproxy",
]
Action = Literal["reinstall", "reconfigure", "uninstall"]

# Human labels for the op header (logged into the stream).
_COMPONENT_LABEL = {
    "node_accelerator": "Node Accelerator",
    "trafficguard": "TrafficGuard",
    "remnanode": "Remnanode",
    "masking": "Маскировочный сайт",
    "warp": "WARP Native",
    "hysteria2": "Hysteria2",
    "ssl": "SSL-сертификат",
    "haproxy": "HAProxy",
}


class NodeOpRequest(DeployRequest):
    """The full deploy payload (so we can rebuild the exact install) + which
    component and action. Inherits DeployRequest's validators (domain/email
    shell-safety etc.), so a hostile field can't reach the reinstall scripts."""
    component: Component
    action: Action


@router.post("/step")
async def node_step(req: NodeOpRequest, background_tasks: BackgroundTasks):
    task = task_store.create(total_steps=1)
    background_tasks.add_task(_run_op, req, task.task_id)
    return {"task_id": task.task_id, "task_type": "node-op"}


def _effective_port(req: NodeOpRequest) -> int:
    # A deployed node already switched to the new port (if change_ssh_port).
    return req.new_ssh_port if req.change_ssh_port else req.current_ssh_port


async def _run_op(req: NodeOpRequest, task_id: str) -> None:
    task = task_store.get(task_id)
    if not task:
        return
    label = _COMPONENT_LABEL[req.component]
    verb = {"reinstall": "Переустановка", "reconfigure": "Изменение",
            "uninstall": "Удаление"}[req.action]
    ssh = SSHSession(req.ip, _effective_port(req), req.ssh_user, req.ssh_password)
    try:
        task.set_step(1, TaskStatus.RUNNING)
        task.add_log(f"\x1b[1;36m[{verb}] {label} на {req.ip}\x1b[0m")
        task.add_log(f"Подключение к {req.ip}:{_effective_port(req)}...")
        await ssh.connect()
        task.add_log("\x1b[32mПодключено.\x1b[0m")

        if req.action == "uninstall":
            await _uninstall(ssh, task, req)
        else:  # reinstall or reconfigure (re-run install with the given params)
            await _reinstall(ssh, task, req)

        task.finish(TaskStatus.SUCCESS)
        task.add_log(f"\n\x1b[1;32m✓ {verb}: {label} — готово.\x1b[0m")
    except Exception as exc:
        task.add_log(f"\n\x1b[1;31m✗ Ошибка: {exc}\x1b[0m")
        task.finish(TaskStatus.FAILED, str(exc))
    finally:
        await ssh.close()


async def _reinstall(ssh: SSHSession, task, req: NodeOpRequest) -> None:
    """Re-run the pipeline's own step builder for the component (idempotent)."""
    c = req.component
    if c == "node_accelerator":
        await pipeline.step_node_accelerator(ssh, task, req)
    elif c == "trafficguard":
        await pipeline.step_traffic_guard(ssh, task, get_backend_ip() or "")
    elif c == "remnanode":
        token = req.remnanode_token
        if not token and req.create_in_remnawave:
            # Auto-registered nodes never stored the token client-side — re-fetch
            # the (stable) node SECRET_KEY from the panel keygen. Needs Remnawave
            # configured in this account's settings.
            token = await _fetch_node_secret_key(task)
        if not token:
            raise RuntimeError(
                "Для переустановки Remnanode нужен токен ноды: укажите его в форме "
                "или настройте Remnawave (Настройки → Remnawave) для авто-нод."
            )
        await pipeline.step_remnanode(
            ssh, task, token, req.domain,
            node_port=req.remnanode_port, xhttp_path=req.xhttp_path,
        )
    elif c == "masking":
        await pipeline.step_sni_masking(ssh, task)
    elif c == "warp":
        await pipeline.step_warp(ssh, task)
    elif c == "hysteria2":
        await pipeline.step_certbot_ssl(ssh, task, req.domain, req.email)
    elif c == "ssl":
        await pipeline.step_ssl(
            ssh, task, req.domain, req.email, req.cloudflare_api_key,
            req.ip, req.cert_provider,
        )
    elif c == "haproxy":
        await pipeline.step_haproxy_deploy(ssh, task, req)


async def _fetch_node_secret_key(task) -> str:
    """Re-fetch the node SECRET_KEY from the panel keygen (stable key) for a
    Remnanode reinstall of an auto-registered node whose token wasn't stored."""
    from app.models.settings import AppSettings
    from app.services import storage
    from app.services.remnawave_client import RemnavaveClient

    cfg = AppSettings(**storage.load_settings()).remnawave
    if not cfg.panel_url or not cfg.api_token:
        raise RuntimeError(
            "Токен ноды не сохранён, а Remnawave не настроен (Настройки → Remnawave) "
            "— переустановить Remnanode нельзя."
        )
    task.add_log("\x1b[36m[remnanode] Получаю SECRET_KEY из панели Remnawave...\x1b[0m")
    client = RemnavaveClient(cfg.panel_url, cfg.api_token)
    return await client.get_node_secret_key()


async def _uninstall(ssh: SSHSession, task, req: NodeOpRequest) -> None:
    """Dedicated teardown scripts (the pipeline has no uninstall path). All are
    idempotent — removing an absent component is a no-op, never an error."""
    c = req.component
    script = _UNINSTALL_SCRIPTS[c](req)
    await ssh.run_script(script, task, check=False, timeout=180)


# ── Uninstall script builders (idempotent, `|| true`-guarded) ────

def _u_warp(_req: NodeOpRequest) -> str:
    return """\
echo "[warp] Останавливаю и удаляю WARP..."
wg-quick down warp 2>/dev/null || true
systemctl disable wg-quick@warp 2>/dev/null || true
rm -f /etc/wireguard/warp.conf 2>/dev/null || true
echo "[warp] Удалён."
"""


def _u_trafficguard(_req: NodeOpRequest) -> str:
    # TrafficGuard installs from the /opt/TrafficGuard-auto clone (step_traffic_
    # guard). Its own iptables rules aren't reliably comment-marked, so we remove
    # the clone + run its uninstall.sh if present; leftover rules clear on reboot.
    # NOTE: na-ctguard is NOT ours — it belongs to Node Accelerator's CDN guard
    # (behind_cdn); its teardown lives in _u_node_accelerator, not here.
    return """\
echo "[trafficguard] Удаляю TrafficGuard..."
if [ -f /opt/TrafficGuard-auto/uninstall.sh ]; then
    cd /opt/TrafficGuard-auto && bash uninstall.sh </dev/null 2>/dev/null || true
fi
rm -rf /opt/TrafficGuard-auto 2>/dev/null || true
echo "[trafficguard] Удалён (оставшиеся iptables-правила очистятся при перезагрузке)."
"""


def _u_remnanode(_req: NodeOpRequest) -> str:
    return """\
echo "[remnanode] Останавливаю и удаляю контейнеры..."
if [ -d /opt/remnanode ]; then
    cd /opt/remnanode && (docker compose down -v 2>/dev/null || docker-compose down -v 2>/dev/null || true)
fi
rm -rf /opt/remnanode 2>/dev/null || true
echo "[remnanode] Удалён."
"""


def _u_masking(_req: NodeOpRequest) -> str:
    return """\
echo "[masking] Восстанавливаю дефолтную страницу..."
rm -rf /var/www/html/* 2>/dev/null || true
cat > /var/www/html/index.html <<'EOF'
<!doctype html><html><head><title>Welcome</title></head>
<body><h1>It works!</h1></body></html>
EOF
echo "[masking] Дефолтная страница восстановлена."
"""


def _u_hysteria2(_req: NodeOpRequest) -> str:
    return """\
echo "[hysteria2] Удаляю Certbot standalone SSL..."
if [ -d /opt/certbot ]; then
    cd /opt/certbot && (docker compose down 2>/dev/null || docker-compose down 2>/dev/null || true)
fi
rm -rf /opt/certbot 2>/dev/null || true
crontab -l 2>/dev/null | grep -v '/opt/certbot' | crontab - 2>/dev/null || true
echo "[hysteria2] Удалён."
"""


def _u_ssl(req: NodeOpRequest) -> str:
    d = req.domain
    return f"""\
echo "[ssl] Удаляю сертификат для {d}..."
/root/.acme.sh/acme.sh --remove -d "{d}" --ecc 2>/dev/null || true
rm -f /etc/ssl/certs/{d}.crt /etc/ssl/certs/{d}_fullchain.pem /etc/ssl/private/{d}.key 2>/dev/null || true
rm -rf /root/.acme.sh/{d}_ecc 2>/dev/null || true
echo "[ssl] Сертификат удалён."
"""


def _u_haproxy(_req: NodeOpRequest) -> str:
    return """\
echo "[haproxy] Останавливаю и удаляю HAProxy..."
systemctl stop haproxy 2>/dev/null || true
systemctl disable haproxy 2>/dev/null || true
DEBIAN_FRONTEND=noninteractive apt-get purge -y haproxy 2>/dev/null || true
rm -rf /etc/haproxy 2>/dev/null || true
echo "[haproxy] Удалён."
"""


def _u_node_accelerator(_req: NodeOpRequest) -> str:
    # Node Accelerator owns the na-ctguard CDN-guard rules (behind_cdn) + the
    # na-fw-safety timer — flush them here (by comment match), scoped to NA.
    return """\
echo "[node-accelerator] Удаляю сервисы Node Accelerator..."
for unit in na-ctguard na-fw-safety.timer na-fw-safety.service; do
    systemctl stop "$unit" 2>/dev/null || true
    systemctl disable "$unit" 2>/dev/null || true
done
# Strip only na-ctguard rules (comment match); SSH-allow/whitelist rules lack
# this marker and survive the restore, so this can only open filtering.
iptables-save 2>/dev/null | grep -v 'na-ctguard' | iptables-restore 2>/dev/null || true
rm -rf /opt/node-accelerator 2>/dev/null || true
echo "[node-accelerator] Удалён (правила ядра остаются до перезагрузки)."
"""


_UNINSTALL_SCRIPTS = {
    "warp": _u_warp,
    "trafficguard": _u_trafficguard,
    "remnanode": _u_remnanode,
    "masking": _u_masking,
    "hysteria2": _u_hysteria2,
    "ssl": _u_ssl,
    "haproxy": _u_haproxy,
    "node_accelerator": _u_node_accelerator,
}
