"""
Orchestrator for the node-installer MCP container (Ф3).

Mirrors `xray_checker.py`'s Docker-out-of-Docker lifecycle: the backend runs in a
container and manages the sibling `node-installer-mcp` container via the host
daemon, attaching it to the shared `node-assistant-net` so external clients reach
it and the MCP reaches our backend by container name.

The MCP container is configured from the ACTIVE account's settings at start time:
Remnawave creds (`RemnavaveConfig`), a freshly-issued node-assistant JWT
(`accounts.issue_token`), and the account's `MCP_AUTH_TOKEN` (stored Fernet-
encrypted in `McpConfig.auth_token_enc`; the plaintext is generated on first
enable and returned only to the authenticated owner).

⚠️ Single shared container: like xray-checker it's one process, so it carries ONE
account's creds — the account that last enabled it. Fine for a single-operator
deployment; documented, not a silent surprise.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets as _secrets
import tempfile
from typing import Any, Optional

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.config import settings
from app.models.settings import AppSettings, McpConfig
from app.services import accounts, api_tokens, storage

log = logging.getLogger("mcp")

CONTAINER_NAME = "node-installer-mcp"
_BACKEND_URL = "http://node-installer-backend:8000"  # backend on node-assistant-net
_CONTAINER_HTTP_PORT = 3100  # fixed inside the container; host port is configurable
# GLOBAL marker of which account currently owns the shared single container, so
# another account's /status reports "foreign" instead of a false "running/reachable".
_OWNER_FILE = accounts.DATA_DIR / "mcp_owner.json"


class McpError(Exception):
    pass


# ── Fernet vault for the MCP auth token ───────────────────────
def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.encryption_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def _decrypt(token_enc: str) -> Optional[str]:
    if not token_enc:
        return None
    try:
        return _fernet().decrypt(token_enc.encode()).decode()
    except InvalidToken:
        # Wrong key / corrupted ciphertext: caller regenerates a token, which
        # silently invalidates any already-configured external client — log it.
        log.warning("mcp.token_decrypt_failed (rekeyed or corrupt vault entry)")
        return None


# ── shared-container ownership marker (global) ────────────────
def _set_owner(account_id: str, port: int) -> None:
    try:
        _OWNER_FILE.write_text(
            json.dumps({"account_id": account_id, "port": port}), encoding="utf-8"
        )
    except OSError:
        pass


def _get_owner() -> Optional[dict]:
    try:
        return json.loads(_OWNER_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ── settings helpers ──────────────────────────────────────────
def _cfg(account_id: Optional[str] = None) -> McpConfig:
    return AppSettings(**storage.load_settings(account_id)).mcp


def encrypt_new_token() -> str:
    """Generate a fresh auth token and return ONLY its ciphertext (the caller
    persists it; the plaintext is re-derived on read via read_auth_token)."""
    return _encrypt(_secrets.token_urlsafe(32))


def read_auth_token(account_id: Optional[str] = None) -> Optional[str]:
    """Decrypt the existing token WITHOUT generating one (side-effect-free — safe
    for a GET)."""
    return _decrypt(_cfg(account_id).auth_token_enc)


def ensure_auth_token(account_id: Optional[str] = None) -> str:
    """Return the account's MCP auth token (plaintext), generating + persisting an
    encrypted one on first use."""
    data = storage.load_settings(account_id)
    cfg = AppSettings(**data).mcp
    plain = _decrypt(cfg.auth_token_enc)
    if plain:
        return plain
    plain = _secrets.token_urlsafe(32)
    data["mcp"] = {**cfg.model_dump(), "auth_token_enc": _encrypt(plain)}
    storage.save_settings(data, account_id)
    return plain


def _network() -> str:
    return os.getenv("XRAY_CHECKER_NETWORK", "").strip()


def endpoint(cfg: Optional[McpConfig] = None) -> str:
    """User-facing HTTP endpoint for the MCP (host-published)."""
    cfg = cfg or _cfg()
    return f"http://<server-ip>:{cfg.http_port}/mcp"


# ── Docker lifecycle (mirrors xray_checker) ───────────────────
_NO_DOCKER = "\x00docker-not-found"


async def _docker(*args: str, timeout: int = 60) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except (FileNotFoundError, NotImplementedError, OSError):
        # OSError also covers PermissionError on the docker socket / resource
        # exhaustion — degrade to the friendly "no-docker" path, don't 500.
        return 127, _NO_DOCKER
    try:
        out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "docker command timed out"
    return proc.returncode or 0, out_b.decode(errors="replace")


def _require_docker(rc: int, out: str) -> None:
    if rc == 127 and out == _NO_DOCKER:
        raise McpError("Docker недоступен на хосте — MCP-контейнер не запущен.")


async def container_state() -> str:
    """'running' / 'stopped' / 'absent' / 'no-docker'."""
    rc, out = await _docker(
        "inspect", "-f", "{{.State.Status}}", CONTAINER_NAME, timeout=10
    )
    if rc == 127 and out == _NO_DOCKER:
        return "no-docker"
    if rc != 0:
        # A non-zero rc means either the container is genuinely absent OR the
        # daemon is unreachable/hung. Disambiguate with a `docker version` probe
        # (mirrors xray_checker) so a hung daemon isn't reported as "absent".
        rc2, out2 = await _docker("version", "-f", "{{.Server.Version}}", timeout=10)
        if rc2 != 0:
            return "no-docker"
        return "absent"
    st = out.strip()
    return "running" if st == "running" else "stopped"


async def start(account_id: Optional[str] = None) -> None:
    """(Re)create + start the MCP container from the account's settings."""
    aid = account_id or accounts.current_account.get()
    cfg = _cfg(aid)
    rw = AppSettings(**storage.load_settings(aid)).remnawave
    if not rw.panel_url or not rw.api_token:
        raise McpError(
            "Remnawave не настроен для аккаунта — MCP требует panel_url + api_token."
        )

    # Argument-injection guard: cfg.image is a positional docker arg; a value
    # starting with '-' would be parsed as an option. (Not user-settable today,
    # but defensive — the API never writes `image`.)
    if not cfg.image or cfg.image.startswith("-"):
        raise McpError("Некорректное имя образа MCP.")

    rc0, out0 = await _docker("version", "-f", "{{.Server.Version}}", timeout=10)
    _require_docker(rc0, out0)

    token = ensure_auth_token(aid)
    # The container authenticates to our backend with a managed, revocable
    # readonly API token (rotated on every start) instead of a raw session JWT.
    # Fall back to a session JWT if token issuance fails for any reason.
    try:
        na_jwt = api_tokens.mint_managed("mcp-container", readonly=True, account_id=aid)
    except Exception:
        na_jwt = accounts.issue_token(aid or "")

    await _docker("rm", "-f", CONTAINER_NAME, timeout=30)

    # Secrets go through a 0600 --env-file (read by the docker CLI, sent over the
    # socket) instead of `-e KEY=VALUE` argv — keeps them out of `ps`/`/proc/
    # cmdline`. (They still appear in `docker inspect`; that's inherent to Docker
    # env and only avoidable via secret mounts.) Non-secret vars stay inline.
    fd, env_path = tempfile.mkstemp(prefix="mcp-env-", suffix=".env")
    try:
        os.chmod(env_path, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(
                f"REMNAWAVE_BASE_URL={rw.panel_url}\n"
                f"REMNAWAVE_API_TOKEN={rw.api_token}\n"
                f"NODE_ASSISTANT_TOKEN={na_jwt}\n"
                f"MCP_AUTH_TOKEN={token}\n"
            )
        run_args = [
            "run",
            "-d",
            "--name",
            CONTAINER_NAME,
            "--restart",
            "unless-stopped",
        ]
        net = _network()
        if net:
            run_args += ["--network", net]
        run_args += [
            "--env-file",
            env_path,
            "-p",
            f"{cfg.http_port}:{_CONTAINER_HTTP_PORT}",
            "-e",
            f"REMNAWAVE_READONLY={'true' if cfg.readonly else 'false'}",
            "-e",
            f"NODE_ASSISTANT_BASE_URL={_BACKEND_URL}",
            "-e",
            f"MCP_HTTP_PORT={_CONTAINER_HTTP_PORT}",
            cfg.image,
        ]
        rc, out = await _docker(*run_args, timeout=120)
    finally:
        try:
            os.unlink(env_path)
        except OSError:
            pass
    _require_docker(rc, out)
    if rc != 0:
        raise McpError(f"Не удалось запустить MCP-контейнер: {out.strip()[:400]}")
    _set_owner(aid or "", cfg.http_port)  # this account now owns the shared container


async def stop(account_id: Optional[str] = None) -> None:
    aid = account_id or accounts.current_account.get()
    owner = _get_owner()
    # Only actually stop the shared container if we own it (don't let account B's
    # "disable" tear down account A's running MCP).
    if owner and owner.get("account_id") not in (None, aid):
        return
    await _docker("stop", CONTAINER_NAME, timeout=30)
    if owner and owner.get("account_id") == aid:
        try:
            _OWNER_FILE.unlink()
        except OSError:
            pass


async def logs(tail: int = 200) -> str:
    rc, out = await _docker("logs", "--tail", str(tail), CONTAINER_NAME, timeout=20)
    if rc != 0:
        return out.strip() or "(логи недоступны — контейнер не запущен)"
    return out


async def reachable(cfg: Optional[McpConfig] = None) -> bool:
    """True if the container's /health answers (by name on the shared net, else
    the published host port)."""
    cfg = cfg or _cfg()
    base = (
        f"http://{CONTAINER_NAME}:{_CONTAINER_HTTP_PORT}"
        if _network()
        else f"http://127.0.0.1:{cfg.http_port}"
    )
    try:
        async with httpx.AsyncClient(timeout=4.0) as c:
            r = await c.get(f"{base}/health")
            return r.status_code == 200
    except Exception:
        return False


async def status(account_id: Optional[str] = None) -> dict[str, Any]:
    aid = account_id or accounts.current_account.get()
    cfg = _cfg(aid)
    state = await container_state()
    owner = _get_owner()
    foreign = (
        state == "running"
        and owner is not None
        and owner.get("account_id") not in (None, aid)
    )
    return {
        "enabled": cfg.enabled,
        "readonly": cfg.readonly,
        # An honest state for a non-owner: the container runs, but with ANOTHER
        # account's creds/token — this account's clients get 403.
        "container": "foreign" if foreign else state,
        "reachable": (await reachable(cfg))
        if state == "running" and not foreign
        else False,
        "http_port": cfg.http_port,
    }
