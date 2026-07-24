"""Self-hosted CLIProxyAPI gateway — OAuth instead of API keys (Wave-7 Plan F).

CLIProxyAPI can reach a provider two ways: an API key, or an OAuth account — the
same login a human uses in Claude/Codex/Grok/Kimi. It keeps the refresh token,
renews it, and round-robins across a pool. This module exists for the second way.

Contract distilled from the operator's `ai-router` project, which runs this in
production (`docs/cliproxyapi-integration.md`, read against CLIProxyAPI HEAD
5afc0f1d). Facts worth not re-learning:

  • image `eceasy/cli-proxy-api` lives on DOCKER HUB, not ghcr — and is pinned;
  • bind-mounting the config FILE makes Docker pre-create it as a DIRECTORY, so
    the whole dir is mounted instead and `--config /conf/config.yaml` is passed;
  • the image has NO curl/wget, so a healthcheck must not use them;
  • `MANAGEMENT_PASSWORD` (env) also force-enables remote management, which a
    sibling container needs — without it the answer is 403 before the key is
    even checked.

⚠️ THE dangerous failure mode: an EMPTY `api-keys` list makes the proxy OPEN —
no auth provider is registered and every request passes. The config is therefore
seeded into the volume BEFORE the container is ever started; there is no window
in which it runs unconfigured.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import secrets
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings
from app.models.settings import AppSettings
from app.services import accounts, storage

log = logging.getLogger("cliproxy")

CONTAINER_NAME = "node-installer-cliproxy"
VOLUME_NAME = "node-cliproxy-conf"
DEFAULT_IMAGE = "eceasy/cli-proxy-api:v7.2.50"
PORT = 8317
_NETWORK = "node-assistant-net"
_NO_DOCKER = "__no_docker__"

# GLOBAL owner marker of the shared single container (mirrors mcp_server). It
# MUST NOT live in per-account settings: a second account that never touched the
# gateway would read its own empty owner, see owner_is_me=True, and be free to
# reconfigure the shared container — pulling in its own creds and exposing the
# first account's OAuth logins in the shared volume. (Wave-7 review, cliproxy:237.)
_OWNER_FILE = accounts.DATA_DIR / "cliproxy_owner.json"


def _set_owner(account_id: str) -> None:
    try:
        _OWNER_FILE.write_text(json.dumps({"account_id": account_id}), encoding="utf-8")
    except OSError:
        pass


def _get_owner() -> str:
    try:
        return json.loads(_OWNER_FILE.read_text(encoding="utf-8")).get("account_id") or ""
    except (OSError, ValueError):
        return ""


def _owner_is(account_id: str) -> bool:
    """True when the gateway is unowned (nobody started it) or this account owns
    it. An unowned gateway may be claimed by whoever enables it first."""
    owner = _get_owner()
    return (not owner) or owner == account_id


class CliProxyError(Exception):
    pass


# ── Fernet vault (same key derivation as mcp_server) ──────────
def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.encryption_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(enc: str) -> Optional[str]:
    if not enc:
        return None
    try:
        return _fernet().decrypt(enc.encode()).decode()
    except InvalidToken:
        return None


def _cfg(account_id: Optional[str] = None):
    return AppSettings(**storage.load_settings(account_id)).ai


# ── config rendering (pure) ───────────────────────────────────

def render_config(master_key: str) -> str:
    """The YAML seeded into the volume before first start.

    `master_key` is quoted rather than interpolated bare: a key containing `:`
    or `#` would otherwise produce YAML that parses into something else."""
    if not master_key:
        raise CliProxyError("Пустой мастер-ключ — прокси стал бы открытым")
    quoted = '"' + master_key.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return (
        'host: ""\n'
        f"port: {PORT}\n"
        "remote-management:\n"
        "  allow-remote: true\n"
        # Left empty on purpose: a non-empty secret-key is bcrypt-hashed and
        # WRITTEN BACK into this file at startup. MANAGEMENT_PASSWORD (env) does
        # the same job without mutating the volume.
        '  secret-key: ""\n'
        'auth-dir: "/conf/auths"\n'
        "api-keys:\n"
        f"  - {quoted}\n"
        "usage-statistics-enabled: false\n"
        "logging-to-file: false\n"
        "debug: false\n"
        "request-retry: 3\n"
    )


def seed_config_argv(master_key: str) -> list[str]:
    """`docker run` argv that writes config.yaml into the volume and exits.

    The YAML travels on STDIN, never in argv — otherwise the master key would sit
    in `docker inspect` and in the daemon's logs."""
    render_config(master_key)  # validate before building a command
    return [
        "run", "--rm", "-i", "-v", f"{VOLUME_NAME}:/conf",
        "busybox:stable", "sh", "-c", "cat > /conf/config.yaml",
    ]


def run_argv(image: str, management_password: str) -> list[str]:
    if not image or image.startswith("-"):
        raise CliProxyError("Некорректное имя образа")
    return [
        "run", "-d", "--name", CONTAINER_NAME, "--restart", "unless-stopped",
        "--network", _NETWORK,
        "-e", f"MANAGEMENT_PASSWORD={management_password}",
        "-v", f"{VOLUME_NAME}:/conf",
        # No -p: the gateway is reachable only from our network. Its own
        # management.html would need the management key, which never goes to a
        # browser anyway.
        image,
        "./CLIProxyAPI", "--config", "/conf/config.yaml",
    ]


# ── docker plumbing (mirrors xray_checker/mcp_server) ─────────

async def _docker(*args: str, stdin: str = "", timeout: int = 60) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", *args,
            stdin=asyncio.subprocess.PIPE if stdin else None,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        return 127, _NO_DOCKER
    try:
        out, _ = await asyncio.wait_for(
            proc.communicate(stdin.encode() if stdin else None), timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "docker timeout"
    return proc.returncode or 0, (out or b"").decode("utf-8", "replace")


def _require_docker(rc: int, out: str) -> None:
    if rc == 127 and out == _NO_DOCKER:
        raise CliProxyError("Docker CLI недоступен в контейнере бэкенда")


async def container_state() -> str:
    rc, out = await _docker("inspect", "-f", "{{.State.Status}}", CONTAINER_NAME, timeout=10)
    if rc == 127 and out == _NO_DOCKER:
        return "no-docker"
    if rc != 0:
        rc2, _ = await _docker("version", "-f", "{{.Server.Version}}", timeout=10)
        return "absent" if rc2 == 0 else "no-docker"
    return "running" if out.strip() == "running" else "stopped"


def ensure_keys(account_id: Optional[str] = None) -> tuple[str, str]:
    """Return (master_key, management_password), generating and storing them on
    first use. Both live Fernet-encrypted, like the MCP auth token."""
    raw = storage.load_settings(account_id)
    s = AppSettings(**raw)
    cfg = s.ai
    master = decrypt(cfg.cliproxy_master_key_enc) or ""
    mgmt = decrypt(cfg.cliproxy_mgmt_key_enc) or ""
    changed = False
    if not master:
        master = secrets.token_urlsafe(32)
        cfg.cliproxy_master_key_enc = encrypt(master)
        changed = True
    if not mgmt:
        mgmt = secrets.token_urlsafe(32)
        cfg.cliproxy_mgmt_key_enc = encrypt(mgmt)
        changed = True
    if changed:
        raw["ai"] = cfg.model_dump()
        storage.save_settings(raw, account_id)
    return master, mgmt


def internal_base_url() -> str:
    return f"http://{CONTAINER_NAME}:{PORT}"


async def start(account_id: Optional[str] = None) -> None:
    """Seed the config, then start. Order is the whole point — see the module
    docstring on the open-proxy failure mode."""
    aid = account_id or accounts.current_account.get()
    master, mgmt = ensure_keys(aid)
    cfg = _cfg(aid)
    image = cfg.cliproxy_image or DEFAULT_IMAGE

    rc0, out0 = await _docker("version", "-f", "{{.Server.Version}}", timeout=10)
    _require_docker(rc0, out0)

    rc, out = await _docker(*seed_config_argv(master), stdin=render_config(master))
    if rc != 0:
        raise CliProxyError(f"Не удалось записать конфиг шлюза: {out.strip()[:300]}")

    await _docker("rm", "-f", CONTAINER_NAME, timeout=30)
    rc, out = await _docker(*run_argv(image, mgmt), timeout=120)
    if rc != 0:
        raise CliProxyError(f"Не удалось запустить шлюз: {out.strip()[:400]}")

    _set_owner(aid or "")  # GLOBAL — this account now owns the shared container


async def stop(account_id: Optional[str] = None) -> None:
    """Tear down the container. Guarded on ownership so a non-owner's «disable»
    cannot kill the owner's running gateway (Wave-7 review, cliproxy:237)."""
    aid = account_id or accounts.current_account.get()
    if not _owner_is(aid or ""):
        raise CliProxyError("Шлюз настроен другим аккаунтом")
    rc, out = await _docker("rm", "-f", CONTAINER_NAME, timeout=30)
    _require_docker(rc, out)
    _set_owner("")  # released — the next enabler may claim it


async def status(account_id: Optional[str] = None) -> dict[str, Any]:
    aid = account_id or accounts.current_account.get()
    cfg = _cfg(aid)
    state = await container_state()
    return {
        "enabled": cfg.cliproxy_enabled,
        "image": cfg.cliproxy_image or DEFAULT_IMAGE,
        "container": state,
        # Read the GLOBAL owner, not per-account settings.
        "owner_is_me": _owner_is(aid or ""),
        "base_url": internal_base_url(),
        "has_keys": bool(cfg.cliproxy_master_key_enc),
    }
