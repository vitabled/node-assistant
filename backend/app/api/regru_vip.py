"""Deploy and verify VLESS + WebSocket behind Reg.ru VIP."""
from __future__ import annotations

import base64
import ipaddress
import json
import re

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import Field, field_validator

from app.models.ssh_creds import SshCreds
from app.services import ssh_auth
from app.services.ssh_manager import SSHSession
from app.services.task_store import TaskStatus, task_store

router = APIRouter(prefix="/api/regru-vip")

_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_PATH_RE = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]+$")


class RegruVipDeployRequest(SshCreds):
    ssh_port: int = Field(default=22, ge=1, le=65535)
    your_site: str
    ws_path: str = "/api/v3/media/ws"
    xray_port: int = Field(default=12080, ge=1, le=65535)
    xray_host: str

    @field_validator("ip")
    @classmethod
    def _ipv4(cls, value: str) -> str:
        try:
            parsed = ipaddress.ip_address(value.strip())
        except ValueError as exc:
            raise ValueError("IP ноды должен быть корректным IPv4-адресом") from exc
        if parsed.version != 4:
            raise ValueError("Для Reg.ru VIP сейчас поддерживается IPv4 ноды")
        return str(parsed)

    @field_validator("your_site", "xray_host")
    @classmethod
    def _domain(cls, value: str) -> str:
        value = value.strip().lower().rstrip(".")
        if not _DOMAIN_RE.fullmatch(value):
            raise ValueError("Нужен корректный домен, например vip.example.com")
        return value

    @field_validator("ws_path")
    @classmethod
    def _ws_path(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith("/"):
            value = "/" + value
        value = value.rstrip("/")
        if not value or value == "/" or not _PATH_RE.fullmatch(value) or "//" in value:
            raise ValueError("Некорректный WebSocket-путь")
        return value


class RegruVipVerifyRequest(RegruVipDeployRequest):
    pass


def build_xray_inbound(req: RegruVipDeployRequest) -> dict:
    return {
        "tag": "regru-vip-ws",
        "listen": "127.0.0.1",
        "port": req.xray_port,
        "protocol": "vless",
        "settings": {"clients": [], "decryption": "none"},
        "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]},
        "streamSettings": {
            "network": "ws",
            "security": "none",
            "wsSettings": {"path": req.ws_path, "host": req.xray_host},
        },
    }


def _proxy_block(req: RegruVipDeployRequest) -> str:
    return f"""        proxy_pass http://xray_ws_vip;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host {req.xray_host};
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;"""


def build_nginx_config(req: RegruVipDeployRequest) -> str:
    proxy = _proxy_block(req)
    return f"""upstream xray_ws_vip {{
    server 127.0.0.1:{req.xray_port};
    keepalive 16;
}}
server {{
    listen 80;
    listen [::]:80;
    server_name {req.your_site} {req.ip};
    large_client_header_buffers 8 64k;
    client_header_buffer_size 64k;

    location = {req.ws_path} {{
{proxy}
    }}
    location {req.ws_path}/ {{
{proxy}
    }}
    location / {{ return 404; }}
}}
"""


def build_htaccess(req: RegruVipDeployRequest) -> str:
    path = req.ws_path.lstrip("/")
    return f"""DirectorySlash Off
Options -Indexes
RewriteEngine On
<IfModule mod_headers.c>
  RewriteCond %{{HTTP:Sec-WebSocket-Key}} .+
  RewriteRule ^{path}/ - [E=DOWS:1]
  RequestHeader set Upgrade websocket env=DOWS
  RequestHeader set Connection "Upgrade" env=DOWS
</IfModule>
RewriteCond %{{HTTP:Sec-WebSocket-Key}} .+
RewriteRule ^{path}/(.*)$ ws://{req.ip}/{path}/$1 [P,L,QSA,NE]
"""


def _b64(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


def build_deploy_script(req: RegruVipDeployRequest) -> str:
    inbound = _b64(json.dumps(build_xray_inbound(req), separators=(",", ":")))
    nginx = _b64(build_nginx_config(req))
    jq_filter = _b64(
        '.inbounds=((.inbounds//[])|map(select(.tag!="regru-vip-ws")))+[$inbound[0]]'
    )
    return f"""set -euo pipefail
command -v docker >/dev/null || {{ echo 'Docker не найден'; exit 1; }}
docker inspect remnanode >/dev/null 2>&1 || {{ echo 'Контейнер remnanode не найден'; exit 1; }}

echo '[1/3] Добавляю inbound regru-vip-ws в конфиг Xray...'
XRAY_CONFIG=$(docker exec remnanode sh -c '
  pid=$(pgrep -o xray 2>/dev/null || true)
  if [ -n "$pid" ]; then
    set -- $(tr "\\000" "\\n" < /proc/$pid/cmdline)
    prev=""
    for arg in "$@"; do
      if [ "$prev" = "-config" ] || [ "$prev" = "-c" ]; then echo "$arg"; exit 0; fi
      case "$arg" in -config=*) echo "${{arg#-config=}}"; exit 0;; esac
      prev="$arg"
    done
  fi
  for f in /etc/xray/config.json /usr/local/etc/xray/config.json /var/lib/remnawave/config.json /opt/remnanode/config.json; do
    [ -f "$f" ] && {{ echo "$f"; exit 0; }}
  done
' | tail -1)
[ -n "$XRAY_CONFIG" ] || {{ echo 'Не удалось найти активный config.json Xray'; exit 1; }}
echo "Активный Xray config: $XRAY_CONFIG"
printf '%s' '{inbound}' | base64 -d > /tmp/regru-vip-inbound.json
docker cp /tmp/regru-vip-inbound.json remnanode:/tmp/regru-vip-inbound.json
rm -f /tmp/regru-vip-inbound.json
docker exec remnanode sh -c '
  if ! command -v jq >/dev/null; then
    command -v apk >/dev/null && apk add --no-cache jq >/dev/null
  fi
  command -v jq >/dev/null || {{ echo "jq отсутствует в remnanode"; exit 1; }}
  cfg="$1"
  cp "$cfg" "$cfg.regru-vip.bak"
  filter=$(printf "%s" "{jq_filter}" | base64 -d)
  jq --slurpfile inbound /tmp/regru-vip-inbound.json "$filter" "$cfg" > "$cfg.regru-vip.tmp"
  xray run -test -config "$cfg.regru-vip.tmp"
  mv "$cfg.regru-vip.tmp" "$cfg"
  rm -f /tmp/regru-vip-inbound.json
' sh "$XRAY_CONFIG"
docker restart remnanode >/dev/null
echo 'Xray inbound применён.'

echo '[2/3] Устанавливаю nginx-конфиг origin...'
printf '%s' '{nginx}' | base64 -d > /tmp/regru-vip.conf
if command -v nginx >/dev/null 2>&1; then
  install -m 0644 /tmp/regru-vip.conf /etc/nginx/conf.d/regru-vip.conf
  nginx -t
  nginx -s reload
elif docker inspect remnawave-nginx >/dev/null 2>&1; then
  docker cp /tmp/regru-vip.conf remnawave-nginx:/etc/nginx/conf.d/regru-vip.conf
  docker exec remnawave-nginx nginx -t
  docker exec remnawave-nginx nginx -s reload
else
  echo 'Не найден nginx ни на хосте, ни в контейнере remnawave-nginx'
  exit 1
fi
rm -f /tmp/regru-vip.conf
echo 'Nginx конфиг применён.'

echo '[3/3] Готово. Установите сгенерированный .htaccess на сайте Reg.ru VIP.'
"""


async def _run_deploy(req: RegruVipDeployRequest, task_id: str) -> None:
    task = task_store.get(task_id)
    if task is None:
        return
    ssh: SSHSession | None = None
    try:
        task.set_step(1, TaskStatus.RUNNING)
        task.add_log(f"Подключение к {req.ip}:{req.ssh_port}...")
        ssh = SSHSession(req.ip, req.ssh_port, req.ssh_user, **await ssh_auth.resolve(req))
        await ssh.connect()
        task.set_step(2, TaskStatus.RUNNING)
        await ssh.run_script(build_deploy_script(req), task, timeout=180)
        task.set_step(3, TaskStatus.RUNNING)
        task.add_log("\x1b[1;32m✓ Reg.ru VIP origin развёрнут.\x1b[0m")
        task.finish(TaskStatus.SUCCESS)
    except Exception as exc:
        task.add_log(f"\x1b[1;31m✗ Ошибка: {exc}\x1b[0m")
        task.finish(TaskStatus.FAILED, str(exc))
    finally:
        if ssh is not None:
            await ssh.close()


@router.post("/deploy")
async def deploy(req: RegruVipDeployRequest, background_tasks: BackgroundTasks):
    task = task_store.create(total_steps=3)
    background_tasks.add_task(_run_deploy, req, task.task_id)
    return {
        "task_id": task.task_id,
        "task_type": "regru-vip",
        "htaccess": build_htaccess(req),
    }


def _curl_probe(url: str, host_header: str = "") -> str:
    host = f" -H 'Host: {host_header}'" if host_header else ""
    return (
        "curl -k -sS --http1.1 --max-time 12 -o /dev/null -w '%{http_code}'"
        f"{host} -H 'Connection: Upgrade' -H 'Upgrade: websocket'"
        " -H 'Sec-WebSocket-Version: 13'"
        " -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ=='"
        f" '{url}' || true"
    )


@router.post("/verify")
async def verify(req: RegruVipVerifyRequest):
    ssh = SSHSession(req.ip, req.ssh_port, req.ssh_user, **await ssh_auth.resolve(req))
    try:
        await ssh.connect(timeout=15)
        origin = await ssh.get_output(
            _curl_probe(f"http://{req.ip}{req.ws_path}/", req.your_site)
        )
        vip = await ssh.get_output(_curl_probe(f"https://{req.your_site}{req.ws_path}/"))
    except Exception as exc:
        raise HTTPException(502, f"Проверка по SSH не выполнена: {exc}") from exc
    finally:
        await ssh.close()
    return {
        "ok": origin == "101" and vip == "101",
        "origin": {"status": int(origin) if origin.isdigit() else 0, "ok": origin == "101"},
        "vip": {"status": int(vip) if vip.isdigit() else 0, "ok": vip == "101"},
    }
