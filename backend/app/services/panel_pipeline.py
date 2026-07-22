"""Ф4 (wave1) — Remnawave panel / subscription-page install pipeline.

Modelled on `pipeline.run_pipeline`: an SSHSession + a streamed Task, each step
`set_step` + a readable log block; any exception → `task.finish(FAILED)` +
re-raise. The pure config builders (`_env_file` / `_compose_yml` / `_caddyfile`
/ `_nginx_conf` / `_subpage_env` / `_subpage_compose`) are separated out so they
can be unit-tested without SSH or network.

Secrets (JWT / PG password / metrics pass / webhook secret) are generated here and
written to the target server's `/opt/remnawave/.env`; they are NEVER logged — the
.env block is pushed through the SILENT channel (`SSHSession.get_script_output`,
which pipes to `bash -s` over stdin and returns only stdout, so the heredoc values
never appear in a task log).
"""

from __future__ import annotations

import asyncio
import base64
import secrets
from typing import Optional

from app.models.panel_deploy import PanelDeployRequest
from app.services.cloudflare import upsert_a_record
from app.services.ssh_manager import SSHSession
from app.services.task_store import Task, TaskStatus
from app.services.pipeline import (
    _APT_WAIT,
    _apt_install,
    build_ssl_script,
    ssl_needs_cf_dns,
)


# Fixed step labels (defined HERE, not in task_store — the panel pipeline is a
# separate flow and must not collide with the deploy STEP_LABELS).
PANEL_STEP_LABELS = [
    "Подключение к серверу",  # 1
    "Установка Docker",  # 2
    "Тест-инструменты",  # 3
    "Генерация секретов и .env",  # 4
    "docker-compose панели",  # 5
    "Reverse-proxy и SSL",  # 6
    "Запуск панели",  # 7
    "Установка страницы подписок",  # 8
]
PANEL_TOTAL = len(PANEL_STEP_LABELS)

# Ports the containers listen on (loopback only → reverse-proxy required).
_PANEL_APP_PORT = 3000
_PANEL_METRICS_PORT = 3001
_SUBPAGE_PORT = 3010


# ──────────────────────────────────────────────────────────────
# Step helpers
# ──────────────────────────────────────────────────────────────


def _begin(task: Task, index: int, label: Optional[str] = None) -> None:
    task.set_step(index, TaskStatus.RUNNING)
    label = label or PANEL_STEP_LABELS[index - 1]
    task.add_log(f"\n\x1b[36m{'─' * 56}\x1b[0m")
    task.add_log(f"\x1b[1;36m[{index}/{PANEL_TOTAL}] {label}\x1b[0m")
    task.add_log(f"\x1b[36m{'─' * 56}\x1b[0m")


def _skip(task: Task, index: int, reason: str, label: Optional[str] = None) -> None:
    _begin(task, index, label)
    task.add_log(f"\x1b[90m[skip] {reason}\x1b[0m")


# ──────────────────────────────────────────────────────────────
# Pure config builders (unit-tested — no SSH / no network)
# ──────────────────────────────────────────────────────────────


def _subpage_bundled(req: PanelDeployRequest) -> bool:
    """True when the subscription page shares the panel's server (so it can reach
    the backend by container name over the shared docker network)."""
    return req.target == "both" and req.sub_server is None


def _env_file(req: PanelDeployRequest) -> str:
    """Render the panel `/opt/remnawave/.env` with freshly-generated secrets.
    `extra_env` is applied ON TOP (can override any base key)."""
    pg_pw = secrets.token_hex(24)  # == openssl rand -hex 24
    base: dict[str, str] = {
        "POSTGRES_USER": "postgres",
        "POSTGRES_PASSWORD": pg_pw,
        "POSTGRES_DB": "postgres",
        "REDIS_HOST": "remnawave-redis",
        "REDIS_PORT": "6379",
        "JWT_AUTH_SECRET": secrets.token_hex(64),  # == openssl rand -hex 64
        "JWT_API_TOKENS_SECRET": secrets.token_hex(64),
        "JWT_AUTH_LIFETIME": "48",
        "APP_PORT": str(_PANEL_APP_PORT),
        "METRICS_PORT": str(_PANEL_METRICS_PORT),
        "METRICS_USER": "admin",
        "METRICS_PASS": secrets.token_hex(16),
        "PANEL_DOMAIN": req.panel_domain or req.sub_domain,
        "FRONT_END_DOMAIN": req.panel_domain or req.sub_domain,
        "SUB_PUBLIC_DOMAIN": req.sub_domain or req.panel_domain,
        "IS_TELEGRAM_NOTIFICATIONS_ENABLED": "false",
    }
    if req.enable_webhooks:
        base["WEBHOOK_ENABLED"] = "true"
        base["WEBHOOK_URL"] = req.webhook_url
        base["WEBHOOK_SECRET_HEADER"] = secrets.token_hex(32)  # HMAC secret, ≥32
    for key, val in (req.extra_env or {}).items():
        base[key] = val
    # DATABASE_URL derived AFTER extra_env so a POSTGRES_USER/DB override stays in
    # sync (POSTGRES_PASSWORD/DATABASE_URL themselves are override-protected).
    base["DATABASE_URL"] = (
        f"postgresql://{base['POSTGRES_USER']}:{base['POSTGRES_PASSWORD']}"
        f"@remnawave-db:5432/{base['POSTGRES_DB']}"
    )
    return "\n".join(f"{k}={v}" for k, v in base.items()) + "\n"


# Panel compose — static YAML (no per-request substitution; the app reads secrets
# from .env, ${VAR} interpolated by compose from the same-dir .env). Plain string
# (not an f-string) so the literal ${} / $${} survive untouched. The network is
# given an explicit name so the subscription-page compose can join it as external.
_PANEL_COMPOSE = """\
services:
  remnawave-backend:
    image: remnawave/backend:2
    container_name: remnawave-backend
    hostname: remnawave-backend
    restart: always
    env_file:
      - .env
    ports:
      - '127.0.0.1:3000:3000'
      - '127.0.0.1:3001:3001'
    depends_on:
      remnawave-db:
        condition: service_healthy
      remnawave-redis:
        condition: service_healthy
    networks:
      - remnawave-network

  remnawave-db:
    image: postgres:18.4
    container_name: remnawave-db
    hostname: remnawave-db
    restart: always
    environment:
      - POSTGRES_USER=${POSTGRES_USER}
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
      - POSTGRES_DB=${POSTGRES_DB}
      - TZ=UTC
    volumes:
      - remnawave-db-data:/var/lib/postgresql/data
    healthcheck:
      test: ['CMD-SHELL', 'pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}']
      interval: 3s
      timeout: 10s
      retries: 10
    networks:
      - remnawave-network

  remnawave-redis:
    image: valkey/valkey:9-alpine
    container_name: remnawave-redis
    hostname: remnawave-redis
    restart: always
    volumes:
      - remnawave-redis-data:/data
    healthcheck:
      test: ['CMD', 'valkey-cli', 'ping']
      interval: 3s
      timeout: 10s
      retries: 10
    networks:
      - remnawave-network

volumes:
  remnawave-db-data:
  remnawave-redis-data:

networks:
  remnawave-network:
    driver: bridge
    name: remnawave-network
"""


def _compose_yml(req: PanelDeployRequest) -> str:
    """Panel docker-compose.yml (backend:2 + postgres:18.4 + valkey). Static —
    postgres TZ stays UTC (never touched)."""
    return _PANEL_COMPOSE


def _proxy_targets(req: PanelDeployRequest, box: str) -> list[tuple[str, int]]:
    """(domain, upstream_port) pairs a given box must reverse-proxy.

    box="panel": panel_domain→3000, plus sub_domain→3010 when the subpage is
    bundled on this same box. box="sub": sub_domain→3010 (separate-server case)."""
    targets: list[tuple[str, int]] = []
    if box == "panel":
        if req.target in ("panel", "both") and req.panel_domain:
            targets.append((req.panel_domain, _PANEL_APP_PORT))
        if (
            req.target in ("subpage", "both")
            and req.sub_domain
            and req.sub_server is None
        ):
            targets.append((req.sub_domain, _SUBPAGE_PORT))
    else:  # "sub"
        if req.sub_domain:
            targets.append((req.sub_domain, _SUBPAGE_PORT))
    return targets


def _render_caddy(targets: list[tuple[str, int]], email: str) -> str:
    if not targets:
        return ""
    parts: list[str] = []
    if email:
        parts.append("{\n    email " + email + "\n}\n")
    for domain, port in targets:
        parts.append(f"{domain} {{\n    reverse_proxy 127.0.0.1:{port}\n}}\n")
    return "\n".join(parts)


def _caddyfile(req: PanelDeployRequest) -> str:
    """Caddyfile for the PANEL box (panel + bundled subpage). Caddy auto-provisions
    TLS, so no acme.sh step is needed."""
    return _render_caddy(_proxy_targets(req, "panel"), req.email)


# nginx site template — native $-vars survive (plain string; only __DOMAIN__ /
# __PORT__ placeholders are replaced). Certs come from build_ssl_script's paths.
_NGINX_SITE = """\
server {
    listen 80;
    listen [::]:80;
    server_name __DOMAIN__;
    location / { return 301 https://$host$request_uri; }
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name __DOMAIN__;

    ssl_certificate     /etc/ssl/certs/__DOMAIN___fullchain.pem;
    ssl_certificate_key /etc/ssl/private/__DOMAIN__.key;

    location / {
        proxy_pass http://127.0.0.1:__PORT__;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
"""


def _render_nginx(targets: list[tuple[str, int]]) -> str:
    blocks = [
        _NGINX_SITE.replace("__DOMAIN__", domain).replace("__PORT__", str(port))
        for domain, port in targets
    ]
    return "\n".join(blocks)


def _nginx_conf(req: PanelDeployRequest) -> str:
    """nginx reverse-proxy config for the PANEL box (certs issued via acme.sh)."""
    return _render_nginx(_proxy_targets(req, "panel"))


def _subpage_env(req: PanelDeployRequest) -> str:
    """Subscription-page `.env`. REMNAWAVE_PANEL_URL points at the bundled backend
    by container name (same box) or the panel's public URL (separate box). The API
    token is REQUIRED (validated on the model): проверено на образе 7.2.6 — с
    пустым токеном контейнер падает с кодом 1 ещё на старте Nest."""
    if _subpage_bundled(req):
        panel_url = "http://remnawave-backend:3000"
    elif req.panel_domain:
        panel_url = f"https://{req.panel_domain}"
    else:
        panel_url = ""  # subpage-only vs. an external panel — set via Variables
    pairs = {
        "APP_PORT": str(_SUBPAGE_PORT),
        "REMNAWAVE_PANEL_URL": panel_url,
        "REMNAWAVE_API_TOKEN": req.subpage_api_token.strip(),
        "CUSTOM_SUB_PREFIX": "",
        "TRUST_PROXY": "true",
    }
    return "\n".join(f"{k}={v}" for k, v in pairs.items()) + "\n"


def _subpage_compose(req: PanelDeployRequest) -> str:
    """Subscription-page docker-compose.yml. Mounts the custom index.html when
    provided; joins the panel network (external) when bundled, else its own."""
    vol = (
        "    volumes:\n      - ./index.html:/opt/app/frontend/index.html\n"
        if req.subpage_html.strip()
        else ""
    )
    if _subpage_bundled(req):
        net_ref = "    networks:\n      - remnawave-network\n"
        net_def = "networks:\n  remnawave-network:\n    external: true\n    name: remnawave-network\n"
    else:
        net_ref = "    networks:\n      - subpage-network\n"
        net_def = "networks:\n  subpage-network:\n    driver: bridge\n"
    return (
        "services:\n"
        "  remnawave-subscription-page:\n"
        f"    image: {req.subpage_image}\n"
        "    container_name: remnawave-subscription-page\n"
        "    hostname: remnawave-subscription-page\n"
        "    restart: always\n"
        "    env_file:\n      - .env\n"
        "    ports:\n      - '127.0.0.1:3010:3010'\n" + vol + net_ref + "\n" + net_def
    )


# ──────────────────────────────────────────────────────────────
# Bash script builders
# ──────────────────────────────────────────────────────────────


def _docker_install_script() -> str:
    return f"""\
{_APT_WAIT}
if ! command -v docker >/dev/null 2>&1; then
    echo "[docker] Установка Docker Engine..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker 2>/dev/null || true
    systemctl start docker 2>/dev/null || true
fi
docker --version
docker compose version 2>/dev/null || docker-compose --version 2>/dev/null || true
echo "[docker] Готово."
"""


def _write_env_script(env_text: str) -> str:
    """Idempotent, SILENT .env write. Existing .env is preserved (re-deploys must
    NOT regenerate secrets — the DB volume holds the old password). Echoes a
    sentinel (the ONLY stdout) so the caller can confirm without seeing values."""
    return (
        "mkdir -p /opt/remnawave\n"
        "if [ -f /opt/remnawave/.env ]; then\n"
        "  echo __ENV_EXISTS__\n"
        "else\n"
        # umask 0077 in a subshell → the file is created 0600 from the start (no
        # world-readable window between `cat >` and a later chmod).
        "  ( umask 077; cat > /opt/remnawave/.env <<'ENVEOF'\n" + env_text + "ENVEOF\n"
        "  )\n"
        "  echo __ENV_WRITTEN__\n"
        "fi\n"
    )


def _write_file_script(path: str, content: str, marker: str) -> str:
    """Write `content` to `path` via a quoted heredoc (no shell expansion)."""
    directory = path.rsplit("/", 1)[0]
    return (
        f"mkdir -p {directory}\n"
        f"cat > {path} <<'{marker}'\n" + content + f"{marker}\n"
        f'echo "[write] {path}"\n'
    )


def _write_html_script(path: str, html: str) -> str:
    """Write arbitrary HTML safely via base64 (the raw content can't collide with
    a heredoc terminator)."""
    b64 = base64.b64encode(html.encode("utf-8")).decode("ascii")
    directory = path.rsplit("/", 1)[0]
    return (
        f"mkdir -p {directory}\n"
        f"cat > {path}.b64 <<'B64EOF'\n" + b64 + "\nB64EOF\n"
        f"base64 -d {path}.b64 > {path} && rm -f {path}.b64\n"
        f'echo "[write] {path} ({len(html)} bytes)"\n'
    )


def _caddy_install_script() -> str:
    return f"""\
{_APT_WAIT}
if ! command -v caddy >/dev/null 2>&1; then
    echo "[caddy] Установка Caddy..."
    {_apt_install("debian-keyring", "debian-archive-keyring", "apt-transport-https", "curl", "gnupg")}
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \\
        | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \\
        > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -y
    {_apt_install("caddy")}
fi
caddy version
"""


# ──────────────────────────────────────────────────────────────
# Reverse-proxy setup (per box) — caddy (auto-SSL) or nginx (acme.sh)
# ──────────────────────────────────────────────────────────────


async def _setup_reverse_proxy(
    ssh: SSHSession, task: Task, req: PanelDeployRequest, targets: list[tuple[str, int]]
) -> None:
    if not targets:
        task.add_log("\x1b[90m[proxy] Нет доменов для проксирования — пропуск.\x1b[0m")
        return

    if req.reverse_proxy == "caddy":
        await ssh.run_script(_caddy_install_script(), task, timeout=300)
        caddyfile = _render_caddy(targets, req.email)
        write = _write_file_script("/etc/caddy/Caddyfile", caddyfile, "CADDY_EOF")
        write += (
            "caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile\n"
            "systemctl reload caddy 2>/dev/null || systemctl restart caddy\n"
            'echo "[caddy] Reverse-proxy настроен (авто-SSL)."\n'
        )
        await ssh.run_script(write, task, timeout=180)
        return

    # nginx branch — issue a per-FQDN cert (acme.sh) then reverse-proxy.
    await ssh.run_script(
        f"{_APT_WAIT}\n{_apt_install('nginx')}\nsystemctl enable nginx 2>/dev/null || true\n",
        task,
        check=False,
        timeout=180,
    )
    for domain, _ in targets:
        if ssl_needs_cf_dns(req.cert_provider):
            # Point the A record at THIS box (ssh.host), not always the panel IP —
            # a separate subscription-page server has its own address.
            box_ip = ssh.host
            task.add_log(f"[CF] A-запись {domain} → {box_ip}...")
            await upsert_a_record(req.cf_api_key or "", domain, box_ip)
        else:
            task.add_log(
                f"\x1b[33m[SSL] Провайдер '{req.cert_provider}' использует HTTP-01 (порт 80). "
                f"Убедитесь, что {domain} уже указывает на сервер.\x1b[0m"
            )
        await ssh.run_script(
            build_ssl_script(
                domain, req.email, req.cf_api_key or "", req.cert_provider
            ),
            task,
            timeout=360,
        )
    nginx_conf = _render_nginx(targets)
    write = _write_file_script(
        "/etc/nginx/conf.d/remnawave-panel.conf", nginx_conf, "NGINX_EOF"
    )
    write += (
        "nginx -t\n"
        "systemctl reload nginx 2>/dev/null || systemctl restart nginx\n"
        'echo "[nginx] Reverse-proxy настроен."\n'
    )
    await ssh.run_script(write, task, timeout=120)


# ──────────────────────────────────────────────────────────────
# Panel + subpage install actions
# ──────────────────────────────────────────────────────────────


async def _install_panel(ssh: SSHSession, task: Task, req: PanelDeployRequest) -> None:
    # Step 4 — secrets + .env (SILENT; secrets never logged)
    _begin(task, 4)
    out = await ssh.get_script_output(_write_env_script(_env_file(req)))
    if "__ENV_WRITTEN__" in out:
        task.add_log(
            "\x1b[32m[env] /opt/remnawave/.env создан (секреты сгенерированы, не выводятся).\x1b[0m"
        )
    elif "__ENV_EXISTS__" in out:
        task.add_log(
            "\x1b[33m[env] /opt/remnawave/.env уже существует — секреты сохранены.\x1b[0m"
        )
    else:
        raise RuntimeError("Не удалось записать /opt/remnawave/.env")

    # Step 5 — docker-compose.yml
    _begin(task, 5)
    await ssh.run_script(
        _write_file_script(
            "/opt/remnawave/docker-compose.yml", _compose_yml(req), "COMPOSE_EOF"
        ),
        task,
    )

    # Step 6 — reverse-proxy + SSL (panel box: panel + bundled subpage domains)
    _begin(task, 6)
    await _setup_reverse_proxy(ssh, task, req, _proxy_targets(req, "panel"))

    # Step 7 — up + verify
    _begin(task, 7)
    up_script = """\
cd /opt/remnawave
docker compose up -d 2>&1 || docker-compose up -d 2>&1
echo "[panel] Ожидание запуска backend..."
for i in $(seq 1 20); do
    if docker ps --filter name=remnawave-backend --filter status=running \\
        --format '{{.Names}}' 2>/dev/null | grep -q remnawave-backend; then
        break
    fi
    sleep 3
done
docker compose ps 2>/dev/null || docker-compose ps 2>/dev/null || true
curl -fsS -o /dev/null http://127.0.0.1:3000 2>/dev/null \\
    && echo "[panel] backend отвечает на 127.0.0.1:3000" \\
    || echo "[panel] backend ещё поднимается (первый старт выполняет миграции)"
"""
    await ssh.run_script(up_script, task, timeout=300)
    running = await ssh.get_output(
        "docker ps --filter name=remnawave-backend --filter status=running "
        "--format '{{.Names}}' 2>/dev/null | head -1"
    )
    if "remnawave-backend" not in (running or ""):
        raise RuntimeError(
            "Контейнер remnawave-backend не запущен после docker compose up. "
            "Проверьте: docker ps -a && docker logs remnawave-backend"
        )
    task.add_log("\x1b[32m[panel] Панель Remnawave запущена.\x1b[0m")


async def _install_subpage(
    ssh: SSHSession, task: Task, req: PanelDeployRequest, *, separate: bool
) -> None:
    """Deploy the subscription-page container on `ssh`'s box. When `separate` the
    box also gets its own reverse-proxy for sub_domain (bundled subpages are
    already covered by the panel box's proxy config)."""
    await ssh.run_script(
        _write_file_script(
            "/opt/remnawave-subpage/docker-compose.yml",
            _subpage_compose(req),
            "SUBCOMPOSE_EOF",
        ),
        task,
    )
    await ssh.run_script(
        _write_file_script(
            "/opt/remnawave-subpage/.env", _subpage_env(req), "SUBENV_EOF"
        ),
        task,
    )
    if req.subpage_html.strip():
        await ssh.run_script(
            _write_html_script("/opt/remnawave-subpage/index.html", req.subpage_html),
            task,
        )

    if separate:
        await _setup_reverse_proxy(ssh, task, req, _proxy_targets(req, "sub"))

    if not req.panel_domain and not _subpage_bundled(req):
        task.add_log(
            "\x1b[33m[subpage] REMNAWAVE_PANEL_URL не задан (нет domain панели). "
            "Задайте его в разделе «Переменные» после создания API-токена в Dashboard → API Tokens.\x1b[0m"
        )
    else:
        task.add_log(
            "\x1b[33m[subpage] Задайте REMNAWAVE_API_TOKEN (Dashboard → API Tokens) "
            "через раздел «Переменные».\x1b[0m"
        )

    await ssh.run_script(
        "cd /opt/remnawave-subpage\n"
        "docker compose up -d 2>&1 || docker-compose up -d 2>&1\n"
        "for i in $(seq 1 10); do\n"
        "    if docker ps --filter name=remnawave-subscription-page --filter status=running \\\n"
        "        --format '{{.Names}}' 2>/dev/null | grep -q remnawave-subscription-page; then break; fi\n"
        "    sleep 2\n"
        "done\n"
        "docker ps --filter name=remnawave-subscription-page "
        "--format 'table {{.Names}}\\t{{.Status}}' 2>/dev/null || true\n",
        task,
        timeout=300,
    )
    running = await ssh.get_output(
        "docker ps --filter name=remnawave-subscription-page --filter status=running "
        "--format '{{.Names}}' 2>/dev/null | head -1"
    )
    if "remnawave-subscription-page" not in (running or ""):
        raise RuntimeError(
            "Контейнер remnawave-subscription-page не запущен после docker compose up. "
            "Проверьте: docker ps -a && docker logs remnawave-subscription-page"
        )
    task.add_log("\x1b[32m[subpage] Страница подписок запущена.\x1b[0m")


# ──────────────────────────────────────────────────────────────
# Shared prep steps (docker + test tools) — run on whichever box we connect to
# ──────────────────────────────────────────────────────────────


async def _install_docker(ssh: SSHSession, task: Task) -> None:
    await ssh.run_script(_docker_install_script(), task, timeout=300)


async def _install_test_tools(
    ssh: SSHSession, task: Task, req: PanelDeployRequest
) -> None:
    if not req.install_test_tools:
        task.add_log(
            "\x1b[90m[test-tools] Пропущено по настройке (install_test_tools=false).\x1b[0m"
        )
        return
    from app.services.test_tools import test_tools_install_script

    try:
        await ssh.run_script(
            test_tools_install_script(), task, check=False, timeout=300
        )
        task.add_log(
            "\x1b[32m[test-tools] Инструменты тестирования установлены.\x1b[0m"
        )
    except Exception as exc:
        task.add_log(
            f"\x1b[33m[ПРЕДУПРЕЖДЕНИЕ] Установка тест-инструментов не удалась: {exc} — "
            f"деплой продолжается (инструменты опциональны).\x1b[0m"
        )


# ──────────────────────────────────────────────────────────────
# Main runner
# ──────────────────────────────────────────────────────────────


async def run_panel_pipeline(req: PanelDeployRequest, task: Task) -> None:
    """Install the Remnawave panel and/or subscription page. Mirrors
    run_pipeline: any exception → FAILED + re-raise."""
    want_panel = req.target in ("panel", "both")
    want_sub = req.target in ("subpage", "both")

    primary_ssh: Optional[SSHSession] = None
    sub_ssh: Optional[SSHSession] = None
    try:
        # ── Step 1: connect to the primary box (panel box, or subpage box in
        #    subpage-only mode) ──
        _begin(task, 1)
        if want_panel:
            conn_ip, u, pw, port = req.ip, req.ssh_user, req.ssh_password, req.ssh_port
        else:
            s = req.sub_server
            conn_ip, u, pw, port = (
                (s.ip, s.ssh_user, s.ssh_password, s.ssh_port)
                if s
                else (req.ip, req.ssh_user, req.ssh_password, req.ssh_port)
            )
        task.add_log(f"Подключение к {conn_ip}:{port} как {u}...")
        primary_ssh = SSHSession(conn_ip, port, u, pw)
        await primary_ssh.connect()
        os_info = await primary_ssh.get_output(
            "cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"'"
        )
        task.add_log(f"\x1b[32mПодключено. ОС: {os_info or 'unknown'}\x1b[0m")

        # ── Step 2: Docker ──
        _begin(task, 2)
        await _install_docker(primary_ssh, task)

        # ── Step 3: test tools (optional, non-fatal) ──
        _begin(task, 3)
        await _install_test_tools(primary_ssh, task, req)

        # ── Steps 4–7: panel ──
        if want_panel:
            await _install_panel(primary_ssh, task, req)
        else:
            for idx in range(4, 8):
                _skip(task, idx, "Панель не выбрана (target=subpage).")

        # ── Step 8: subscription page ──
        if want_sub:
            _begin(task, 8)
            if req.sub_server is not None and want_panel:
                # Separate box: open a second session + install docker there.
                s = req.sub_server
                task.add_log(
                    f"\x1b[36m[subpage] Отдельный сервер {s.ip}:{s.ssh_port}...\x1b[0m"
                )
                sub_ssh = SSHSession(s.ip, s.ssh_port, s.ssh_user, s.ssh_password)
                await sub_ssh.connect()
                await _install_docker(sub_ssh, task)
                await _install_test_tools(sub_ssh, task, req)
                await _install_subpage(sub_ssh, task, req, separate=True)
            else:
                # Bundled (same box) OR subpage-only (primary IS the subpage box).
                separate = not _subpage_bundled(req)
                await _install_subpage(primary_ssh, task, req, separate=separate)
        else:
            _skip(task, 8, "Страница подписок не выбрана (target=panel).")

        task.finish(TaskStatus.SUCCESS)
        task.add_log("\n\x1b[1;32m✓ Установка Remnawave завершена успешно!\x1b[0m")

    except asyncio.CancelledError:
        task.add_log(
            "\n\x1b[1;33m[СИСТЕМА] Установка принудительно остановлена. Соединение закрыто.\x1b[0m"
        )
        task.finish(TaskStatus.FAILED, "Остановлено пользователем")
        raise
    except Exception as exc:
        task.add_log(f"\n\x1b[1;31m✗ Ошибка: {exc}\x1b[0m")
        task.finish(TaskStatus.FAILED, str(exc))
        raise
    finally:
        for sess in (primary_ssh, sub_ssh):
            if sess:
                try:
                    await sess.close()
                except Exception:
                    pass
