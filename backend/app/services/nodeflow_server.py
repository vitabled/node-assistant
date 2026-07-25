"""
Local NodeFlow HAProxy-panel orchestrator (Wave-7 «HAPROXY» local deploy).

Brings up a SHARED, single local NodeFlow stack over the host Docker socket
(DooD, like the xray-checker / MCP / cliproxy containers) so «HAPROXY» works with
zero external panel: postgres → migrations → panel, on `node-assistant-net`.

Design notes (each earned from the install-kit):
  • The panel has NO published image — it is built from the vendored source via the
    compose `nodeflow-build` profile. If the images are absent, deploy fails SOFT
    with a clear "build first" message (mirrors mcp_server's Docker-absent path).
  • PKI is generated HERE in Python (`cryptography`): an Ed25519 CA, a server cert
    whose SAN is the panel's PUBLIC host (agents connect to it over mTLS on :4200),
    and an Ed25519 update-signing key. No root/openssl/compose dance. Idempotent —
    regenerating the CA would orphan every already-enrolled node.
  • The stack is SHARED across accounts (single panel), like the other DooD
    singletons. The admin token + postgres password live GLOBALLY, Fernet-encrypted,
    under DATA_DIR/nodeflow/state.json — NOT in per-account settings.
  • Only the agent mTLS port (4200) is host-published. The browser UI (8080) is
    reached by container name through the backend proxy and never host-exposed.
  • PKI/TLS are mounted into the panel via node-data **volume-subpath** (only
    nodeflow/pki + nodeflow/tls), never the whole data volume — the third-party
    panel must not see other accounts' data.
"""
from __future__ import annotations

import asyncio
import base64
import datetime as _dt
import hashlib
import ipaddress
import json
import logging
import os
import secrets
import socket
from pathlib import Path
from typing import Any, Optional

import httpx
from cryptography import x509
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import NameOID

from app.config import settings
from app.services import accounts, backend_ip

log = logging.getLogger("nodeflow")

PANEL_CONTAINER = "nodeflow-panel"
POSTGRES_CONTAINER = "nodeflow-postgres"
PANEL_IMAGE = "node-installer-nodeflow-panel:latest"
MIGRATE_IMAGE = "node-installer-nodeflow-migrate:latest"
POSTGRES_IMAGE = "postgres:17-alpine"
PGDATA_VOLUME = "node-nodeflow-pgdata"
RELEASES_VOLUME = "node-nodeflow-releases"
PANEL_PORT = 8080
AGENT_PORT = 4200
_NETWORK = "node-assistant-net"
_NO_DOCKER = "__no_docker__"
_PANEL_UID_GID = 65532  # distroless nonroot

_ROOT = accounts.DATA_DIR / "nodeflow"
_STATE_FILE = _ROOT / "state.json"
_PKI = _ROOT / "pki"
_TLS = _ROOT / "tls"


class NodeFlowServerError(Exception):
    pass


# ── Fernet vault (same key derivation as the other module-scoped vaults) ──────
def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.encryption_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def _decrypt(enc: str) -> Optional[str]:
    if not enc:
        return None
    try:
        return _fernet().decrypt(enc.encode()).decode()
    except (InvalidToken, ValueError):
        return None


# ── global state (admin token + postgres password) ───────────────────────────
def _load_state() -> dict:
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    _ROOT.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    try:
        os.chmod(_STATE_FILE, 0o600)
    except OSError:
        pass


def ensure_state() -> dict:
    """Generate the admin token + postgres password once; keep them thereafter.
    Returns the DECRYPTED {admin_token, pg_password}."""
    state = _load_state()
    changed = False
    if not state.get("admin_token_enc"):
        state["admin_token_enc"] = _encrypt(secrets.token_hex(32))
        changed = True
    if not state.get("pg_password_enc"):
        state["pg_password_enc"] = _encrypt(secrets.token_hex(24))
        changed = True
    if changed:
        _save_state(state)
    return {
        "admin_token": _decrypt(state["admin_token_enc"]) or "",
        "pg_password": _decrypt(state["pg_password_enc"]) or "",
    }


def admin_token() -> Optional[str]:
    return _decrypt(_load_state().get("admin_token_enc", ""))


def configured_san() -> str:
    return _load_state().get("san_host", "")


def internal_base_url() -> str:
    return f"http://{PANEL_CONTAINER}:{PANEL_PORT}"


# ── PKI (Ed25519 CA + server cert w/ SAN + update-signing key) ────────────────
def _san_entry(host: str):
    try:
        return x509.IPAddress(ipaddress.ip_address(host))
    except ValueError:
        return x509.DNSName(host)


def _write_key(path: Path, key: ed25519.Ed25519PrivateKey) -> None:
    path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    # Readable by the panel's nonroot user. Prefer group-owned 0440 (root:65532);
    # if chown is unavailable (non-root, e.g. tests) fall back to world-read 0444.
    # node-data is private to our own stack, so 0444 here is an acceptable floor.
    try:
        os.chown(path, 0, _PANEL_UID_GID)
        os.chmod(path, 0o440)
    except (OSError, AttributeError):
        os.chmod(path, 0o444)


def _write_cert(path: Path, cert: x509.Certificate) -> None:
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    os.chmod(path, 0o444)


def generate_pki(san_host: str) -> None:
    """Idempotent: create the CA, the :4200 server cert (SAN=san_host) and the
    update-signing key if they are not all already present. Never regenerates a
    live CA (that would break every enrolled agent)."""
    _PKI.mkdir(parents=True, exist_ok=True)
    _TLS.mkdir(parents=True, exist_ok=True)
    ca_crt, ca_key = _PKI / "ca.crt", _PKI / "ca.key"
    srv_crt, srv_key = _TLS / "server.crt", _TLS / "server.key"
    sign_key = _PKI / "update-signing.key"
    if all(p.exists() for p in (ca_crt, ca_key, srv_crt, srv_key, sign_key)):
        return

    # Fixed epoch base (Date.now is unavailable in some sandboxes; use a plain
    # fixed-past notBefore and long validity — these are internal trust roots).
    not_before = _dt.datetime(2024, 1, 1, tzinfo=_dt.timezone.utc)

    ca_privkey = ed25519.Ed25519PrivateKey.generate()
    ca_name = x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "NodeFlow"),
                         x509.NameAttribute(NameOID.COMMON_NAME, "NodeFlow Agent CA")])
    ca_cert = (x509.CertificateBuilder()
               .subject_name(ca_name).issuer_name(ca_name)
               .public_key(ca_privkey.public_key())
               .serial_number(x509.random_serial_number())
               .not_valid_before(not_before)
               .not_valid_after(not_before + _dt.timedelta(days=3650))
               .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
               .add_extension(x509.KeyUsage(
                   digital_signature=False, content_commitment=False, key_encipherment=False,
                   data_encipherment=False, key_agreement=False, key_cert_sign=True,
                   crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
               .sign(ca_privkey, None))

    srv_privkey = ed25519.Ed25519PrivateKey.generate()
    srv_cert = (x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "NodeFlow"),
                                        x509.NameAttribute(NameOID.COMMON_NAME, san_host)]))
                .issuer_name(ca_name)
                .public_key(srv_privkey.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(not_before)
                .not_valid_after(not_before + _dt.timedelta(days=825))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.KeyUsage(
                    digital_signature=True, content_commitment=False, key_encipherment=False,
                    data_encipherment=False, key_agreement=False, key_cert_sign=False,
                    crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
                .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .add_extension(x509.SubjectAlternativeName([_san_entry(san_host)]), critical=False)
                .sign(ca_privkey, None))

    _write_key(ca_key, ca_privkey)
    _write_cert(ca_crt, ca_cert)
    _write_key(srv_key, srv_privkey)
    _write_cert(srv_crt, srv_cert)
    _write_key(sign_key, ed25519.Ed25519PrivateKey.generate())


# ── docker plumbing (mirrors cliproxy_server/xray_checker) ────────────────────
async def _docker(*args: str, timeout: int = 60) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        return 127, _NO_DOCKER
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "docker timeout"
    return proc.returncode or 0, (out or b"").decode("utf-8", "replace")


def _require_docker(rc: int, out: str) -> None:
    if rc == 127 and out == _NO_DOCKER:
        raise NodeFlowServerError("Docker CLI недоступен в контейнере бэкенда")


async def _container_state(name: str) -> str:
    rc, out = await _docker("inspect", "-f", "{{.State.Status}}", name, timeout=10)
    if rc == 127 and out == _NO_DOCKER:
        return "no-docker"
    if rc != 0:
        rc2, _ = await _docker("version", "-f", "{{.Server.Version}}", timeout=10)
        return "absent" if rc2 == 0 else "no-docker"
    return "running" if out.strip() == "running" else "stopped"


async def _node_data_volume() -> str:
    """Resolve the real Docker volume backing /app/data by inspecting THIS
    container's own mounts (the compose project prefix is unknown a priori)."""
    self_id = socket.gethostname()
    rc, out = await _docker(
        "inspect", "-f",
        '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Name}}{{end}}{{end}}',
        self_id, timeout=10)
    name = out.strip()
    if rc != 0 or not name:
        raise NodeFlowServerError(
            "Не удалось определить том node-data (нужен для монтирования PKI в панель)")
    return name


async def _images_present() -> bool:
    rc, _ = await _docker("image", "inspect", PANEL_IMAGE, MIGRATE_IMAGE, timeout=15)
    return rc == 0


# ── argv builders (pure → unit-testable) ──────────────────────────────────────
def postgres_run_argv(pg_password: str) -> list[str]:
    return [
        "run", "-d", "--name", POSTGRES_CONTAINER, "--restart", "unless-stopped",
        "--network", _NETWORK,
        "-e", "POSTGRES_DB=nodeflow", "-e", "POSTGRES_USER=nodeflow",
        "-e", f"POSTGRES_PASSWORD={pg_password}",
        "-v", f"{PGDATA_VOLUME}:/var/lib/postgresql/data",
        POSTGRES_IMAGE,
    ]


def migrate_run_argv(pg_password: str) -> list[str]:
    return [
        "run", "--rm", "--network", _NETWORK,
        "-e", "PGHOST=" + POSTGRES_CONTAINER, "-e", "PGPORT=5432",
        "-e", "PGDATABASE=nodeflow", "-e", "PGUSER=nodeflow",
        "-e", f"PGPASSWORD={pg_password}",
        MIGRATE_IMAGE,
    ]


def panel_run_argv(admin_token: str, pg_password: str, san_host: str, data_volume: str) -> list[str]:
    env = {
        "DATABASE_URL": f"postgres://nodeflow:{pg_password}@{POSTGRES_CONTAINER}:5432/nodeflow?sslmode=disable",
        "DATABASE_MAX_CONNS": "10",
        "PANEL_LISTEN_ADDR": ":8080",
        "PANEL_ADMIN_TOKEN": admin_token,
        "PANEL_NODE_UPDATER_BINARY": "/node-updater",
        "PANEL_PUBLIC_URL": internal_base_url(),
        "PANEL_AGENT_PUBLIC_URL": f"https://{san_host}:{AGENT_PORT}",
        "PANEL_AGENT_TLS_LISTEN_ADDR": f":{AGENT_PORT}",
        "PANEL_AGENT_TLS_BIND_ADDR": "0.0.0.0",
        "PANEL_AGENT_TLS_PORT": str(AGENT_PORT),
        "PANEL_AGENT_TLS_CERT_FILE": "/tls/server.crt",
        "PANEL_AGENT_TLS_KEY_FILE": "/tls/server.key",
        "PANEL_AGENT_TLS_CLIENT_CA_FILE": "/pki/ca.crt",
        "PANEL_AGENT_TLS_ISSUER_KEY_FILE": "/pki/ca.key",
        "PANEL_AGENT_TLS_SERVER_NAME": san_host,
        "PANEL_REQUIRE_AGENT_MTLS": "true",
        "PANEL_AGENT_RELEASE_DIR": "/var/lib/nodeflow/releases",
        "PANEL_UPDATE_SIGNING_KEY_FILE": "/pki/update-signing.key",
    }
    argv = ["run", "-d", "--name", PANEL_CONTAINER, "--restart", "unless-stopped",
            "--network", _NETWORK, "-p", f"0.0.0.0:{AGENT_PORT}:{AGENT_PORT}"]
    for k, v in env.items():
        argv += ["-e", f"{k}={v}"]
    argv += [
        "--mount", f"type=volume,src={data_volume},dst=/pki,volume-subpath=nodeflow/pki,readonly",
        "--mount", f"type=volume,src={data_volume},dst=/tls,volume-subpath=nodeflow/tls,readonly",
        "-v", f"{RELEASES_VOLUME}:/var/lib/nodeflow/releases",
        PANEL_IMAGE,
    ]
    return argv


def _releases_init_argv() -> list[str]:
    # Fresh named volume is root:root; the nonroot panel needs to write releases.
    return [
        "run", "--rm", "-v", f"{RELEASES_VOLUME}:/releases",
        "--entrypoint", "sh", MIGRATE_IMAGE,
        "-c", f"mkdir -p /releases && chown {_PANEL_UID_GID}:{_PANEL_UID_GID} /releases && chmod 0700 /releases",
    ]


# ── deploy / stop / status ────────────────────────────────────────────────────
async def _wait_postgres(timeout: int = 60) -> None:
    for _ in range(timeout):
        rc, _out = await _docker("exec", POSTGRES_CONTAINER, "pg_isready", "-U", "nodeflow", "-d", "nodeflow", timeout=8)
        if rc == 0:
            return
        await asyncio.sleep(1)
    raise NodeFlowServerError("Postgres не стал готов за отведённое время")


async def _wait_panel_healthy(timeout: int = 90) -> None:
    url = f"{internal_base_url()}/healthz"
    async with httpx.AsyncClient(timeout=5.0) as c:
        for _ in range(timeout):
            try:
                r = await c.get(url)
                if r.status_code == 200 and r.json().get("status") == "ok":
                    return
            except Exception:
                pass
            await asyncio.sleep(1)
    raise NodeFlowServerError("Панель не стала здоровой за отведённое время — см. docker logs nodeflow-panel")


async def deploy(san_host: Optional[str] = None) -> dict[str, Any]:
    """Full local bring-up: PKI → postgres → migrations → panel. Idempotent for
    PKI/state; recreates the postgres+panel containers. Returns status()."""
    rc0, out0 = await _docker("version", "-f", "{{.Server.Version}}", timeout=10)
    _require_docker(rc0, out0)
    if rc0 != 0:
        raise NodeFlowServerError("Docker-демон недоступен")
    if not await _images_present():
        raise NodeFlowServerError(
            "Образы NodeFlow не собраны. Выполните на хосте: "
            "docker compose --profile nodeflow-build build nodeflow-panel nodeflow-migrate")

    host = (san_host or "").strip()
    if not host:
        try:
            host = await backend_ip.get_backend_ip() or ""
        except Exception:
            host = ""
    if not host:
        raise NodeFlowServerError("Не удалось определить публичный адрес хоста для mTLS-сертификата агента")

    state = ensure_state()
    generate_pki(host)
    # Remember the SAN the CA was minted for (regenerating it would orphan agents).
    st = _load_state()
    st["san_host"] = configured_san() or host
    _save_state(st)
    host = st["san_host"]

    data_volume = await _node_data_volume()

    # postgres
    await _docker("rm", "-f", POSTGRES_CONTAINER, timeout=30)
    rc, out = await _docker(*postgres_run_argv(state["pg_password"]), timeout=120)
    if rc != 0:
        raise NodeFlowServerError(f"Не удалось запустить postgres: {out.strip()[:400]}")
    await _wait_postgres()

    # migrations (baked image → no host-path mount)
    rc, out = await _docker(*migrate_run_argv(state["pg_password"]), timeout=120)
    if rc != 0:
        raise NodeFlowServerError(f"Миграции не применились: {out.strip()[:500]}")

    # releases dir ownership, then panel
    await _docker(*_releases_init_argv(), timeout=60)
    await _docker("rm", "-f", PANEL_CONTAINER, timeout=30)
    rc, out = await _docker(*panel_run_argv(state["admin_token"], state["pg_password"], host, data_volume), timeout=120)
    if rc != 0:
        raise NodeFlowServerError(f"Не удалось запустить панель: {out.strip()[:500]}")
    await _wait_panel_healthy()
    return await status()


# In-progress tracking so POST /deploy can return immediately and the frontend
# polls status (the bring-up takes ~60-90s — too long to hold an HTTP request).
_DEPLOY: dict[str, Any] = {"running": False, "error": ""}


async def deploy_bg(san_host: Optional[str] = None) -> None:
    """Fire-and-forget deploy: records progress/errors in _DEPLOY so status()
    surfaces them. Single-flight (a second call while running is a no-op)."""
    if _DEPLOY["running"]:
        return
    _DEPLOY["running"] = True
    _DEPLOY["error"] = ""
    try:
        await deploy(san_host)
    except Exception as exc:  # never lets the task die silently
        _DEPLOY["error"] = str(exc)
        log.warning("nodeflow deploy failed: %s", exc)
    finally:
        _DEPLOY["running"] = False


async def stop() -> None:
    rc, out = await _docker("rm", "-f", PANEL_CONTAINER, POSTGRES_CONTAINER, timeout=40)
    _require_docker(rc, out)


async def status() -> dict[str, Any]:
    st = _load_state()
    panel = await _container_state(PANEL_CONTAINER)
    postgres = await _container_state(POSTGRES_CONTAINER)
    reachable = False
    if panel == "running":
        try:
            async with httpx.AsyncClient(timeout=4.0) as c:
                r = await c.get(f"{internal_base_url()}/healthz")
                reachable = r.status_code == 200
        except Exception:
            reachable = False
    return {
        "panel": panel,
        "postgres": postgres,
        "reachable": reachable,
        "images_built": await _images_present(),
        "deploying": _DEPLOY["running"],
        "last_error": _DEPLOY["error"],
        "san_host": st.get("san_host", ""),
        "agent_endpoint": f"https://{st.get('san_host', '')}:{AGENT_PORT}" if st.get("san_host") else "",
        "has_token": bool(st.get("admin_token_enc")),
    }
