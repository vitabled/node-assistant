"""
Remote xray-core version management on an already-deployed node.

The remnanode image (remnawave/node) bakes a specific xray-core binary into
/usr/local/bin/xray at BUILD TIME (docker/Dockerfile's `xray` build stage runs
XTLS's install-xray.sh with a fixed XRAY_CORE_VERSION ARG) — there is no
runtime env-var or docker image tag that selects the xray-core version, so
changing it after deploy means downloading the requested release straight
into the running container and restarting the xray process (not the whole
container: s6-overlay supervises xray as its own service inside remnanode).

Streamed as a background Task, same pattern as node_ops.py's /step endpoint,
so the card can show live progress via the existing /ws/logs/{task_id}.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, BackgroundTasks
from pydantic import field_validator

from app.models.ssh_creds import SshCreds
from app.services import ssh_auth
from app.services.ssh_manager import SSHSession
from app.services.task_store import task_store, TaskStatus

router = APIRouter(prefix="/api/node")

# Accepts "latest", "v25.9.11", or "25.9.11" — same shape XTLS/Xray-core tags use.
_VERSION_RE = re.compile(r"^(latest|v?\d+\.\d+\.\d+)$")


class XrayVersionRequest(SshCreds):
    ssh_port: int = 22
    version: str = "latest"

    @field_validator("version")
    @classmethod
    def _safe_version(cls, v: str) -> str:
        v = (v or "").strip()
        if not _VERSION_RE.match(v):
            raise ValueError("Версия должна быть вида 'latest' или 'v25.9.11'")
        return v


def _xray_update_script(version: str) -> str:
    # Reuses remnawave's own install-xray.sh (same script the Dockerfile build
    # stage runs) so the on-node update follows the identical download/verify
    # logic upstream uses — no separate re-implementation to drift out of sync.
    tag = version if version == "latest" else (version if version.startswith("v") else f"v{version}")
    return f"""\
set -e
echo "[xray-version] Текущая версия:"
docker exec remnanode xray version 2>/dev/null | head -1 || echo "(не удалось определить)"

echo "[xray-version] Скачиваю Xray-core {tag} внутри контейнера remnanode..."
docker exec remnanode sh -c '
    apk add --no-cache curl >/dev/null 2>&1 || true
    curl -fsSL https://raw.githubusercontent.com/remnawave/scripts/main/scripts/install-xray.sh \\
        | sh -s -- "{tag}" XTLS
'

echo "[xray-version] Перезапускаю процесс xray внутри remnanode (s6 supervises it)..."
docker exec remnanode sh -c '
    if command -v s6-svc >/dev/null 2>&1; then
        s6-svc -r /run/service/xray 2>/dev/null || s6-svc -r /var/run/s6/services/xray 2>/dev/null || true
    fi
' || true

# s6 service paths vary by image build; a container restart is the reliable
# fallback if the in-place service restart above didn't take.
sleep 2
NEW_V=$(docker exec remnanode xray version 2>/dev/null | head -1 || echo "")
if [ -z "$NEW_V" ]; then
    echo "[xray-version] Процесс не поднялся после restart сервиса — перезапускаю контейнер remnanode..."
    docker restart remnanode
    sleep 3
    NEW_V=$(docker exec remnanode xray version 2>/dev/null | head -1 || echo "(не удалось определить)")
fi
echo "[xray-version] Новая версия: $NEW_V"
"""


async def _run_xray_update(req: XrayVersionRequest, task_id: str) -> None:
    task = task_store.get(task_id)
    if not task:
        return
    ssh: SSHSession | None = None
    try:
        task.set_step(1, TaskStatus.RUNNING)
        task.add_log(f"\x1b[1;36m[Xray-core] Обновление версии на {req.ip} → {req.version}\x1b[0m")
        task.add_log(f"Подключение к {req.ip}:{req.ssh_port}...")
        ssh = SSHSession(req.ip, req.ssh_port, req.ssh_user, **await ssh_auth.resolve(req))
        await ssh.connect()
        task.add_log("\x1b[32mПодключено.\x1b[0m")

        running = await ssh.get_output(
            "docker ps --filter 'name=remnanode' --filter 'status=running' "
            "--format '{{.Names}}' 2>/dev/null | head -1"
        )
        if "remnanode" not in (running or ""):
            raise RuntimeError("Контейнер remnanode не запущен — обновление xray невозможно.")

        await ssh.run_script(_xray_update_script(req.version), task, timeout=180)

        task.finish(TaskStatus.SUCCESS)
        task.add_log(f"\n\x1b[1;32m✓ Xray-core обновлён на {req.ip}.\x1b[0m")
    except Exception as exc:
        task.add_log(f"\n\x1b[1;31m✗ Ошибка: {exc}\x1b[0m")
        task.finish(TaskStatus.FAILED, str(exc))
    finally:
        if ssh is not None:
            await ssh.close()


@router.post("/xray-version")
async def update_xray_version(req: XrayVersionRequest, background_tasks: BackgroundTasks):
    """Kick off a streamed xray-core version update on a live node.
    Returns {task_id} — subscribe to /ws/logs/{task_id} for progress."""
    task = task_store.create(total_steps=1)
    background_tasks.add_task(_run_xray_update, req, task.task_id)
    return {"task_id": task.task_id, "task_type": "xray-version"}
