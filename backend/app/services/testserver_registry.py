"""Per-account registry of iperf3 test servers (Ф1, wave1).

Records live in `accounts/<id>/testservers.json`:
  {id: 12hex, name, ip, iperf_port (5201), created_at}

SSH credentials used to *deploy* a test server are transient (per-request,
never persisted) — only ip/port are stored. Pattern mirrors
`checker_registry.py`.
"""

from __future__ import annotations

import ipaddress
import shlex
import time
import uuid
from typing import Optional

from app.services import storage
from app.services.test_tools import iperf_server_script, test_tools_install_script


def list_servers(account_id: Optional[str] = None) -> list[dict]:
    return storage.load_testservers(account_id)


def get_server(server_id: str, account_id: Optional[str] = None) -> Optional[dict]:
    return next(
        (s for s in storage.load_testservers(account_id) if s["id"] == server_id), None
    )


def add_server(
    name: str, ip: str, iperf_port: int = 5201, account_id: Optional[str] = None
) -> dict:
    """Register a test server. Raises ValueError on a bad IP/port or an ip+port
    duplicate."""
    ip = ip.strip()
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        raise ValueError("Некорректный IP-адрес")
    port = int(iperf_port)
    if not 1 <= port <= 65535:
        raise ValueError("Некорректный порт iperf3")
    existing = storage.load_testservers(account_id)
    if any(s["ip"] == ip and s["iperf_port"] == port for s in existing):
        raise ValueError("Тест-сервер с таким IP и портом уже добавлен")
    srv = {
        "id": uuid.uuid4().hex[:12],
        "name": name.strip() or ip,
        "ip": ip,
        "iperf_port": port,
        "created_at": int(time.time()),
    }
    existing.append(srv)
    storage.save_testservers(existing, account_id)
    return srv


def remove_server(server_id: str, account_id: Optional[str] = None) -> bool:
    existing = storage.load_testservers(account_id)
    kept = [s for s in existing if s["id"] != server_id]
    if len(kept) == len(existing):
        return False
    storage.save_testservers(kept, account_id)
    return True


def deploy_script(iperf_port: int, allow_ips: list[str]) -> str:
    """Full test-server provisioning: shared test-tools installer + the iperf3
    systemd service + UFW allow rules for the iperf port, restricted to
    `allow_ips` (backend/nodes). Invalid entries are dropped BEFORE generation
    (ipaddress) and valid ones are shlex-quoted anyway (defence-in-depth).
    UFW is never force-enabled — rules apply only if ufw is already present."""
    port = int(iperf_port)
    valid: list[str] = []
    for ip in allow_ips:
        try:
            ip = str(ipaddress.ip_address(str(ip).strip()))
        except ValueError:
            continue
        if ip not in valid:
            valid.append(ip)
    if valid:
        rules = "\n    ".join(
            f"ufw allow from {shlex.quote(ip)} to any port {port} proto tcp "
            f"comment 'iperf3-test' 2>/dev/null || true"
            for ip in valid
        )
        ufw_block = f"""\
if command -v ufw >/dev/null 2>&1; then
    {rules}
    ufw status 2>/dev/null | grep 'iperf3-test' || true
fi
echo "[ufw] Доступ к порту {port} разрешён для {len(valid)} IP."
"""
    else:
        ufw_block = 'echo "[ufw] allow-список пуст — правила не добавлены."\n'
    return (
        test_tools_install_script()
        + "\n"
        + iperf_server_script(port)
        + "\n"
        + ufw_block
    )
