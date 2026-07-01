"""
Orchestrator for the headless `kutovoys/xray-checker` container.

node-assistant manages xray-checker as a Docker container (chosen strategy):
  - start/stop/restart with `--restart unless-stopped` so Docker auto-restarts it
    on crash (the "daemon lifecycle" requirement),
  - update = `docker pull <image>` then recreate; on pull/run failure the old
    container is left running,
  - the checker's JSON REST API (`/api/v1/*`) is proxied through our own routes.

xray-checker facts (verified against the repo, v based on api-1-style /api/v1):
  Env: SUBSCRIPTION_URL (required), PROXY_CHECK_INTERVAL (300), PROXY_CHECK_METHOD
       (ip), METRICS_PORT (2112).
  JSON API (wrapped in {success, data, error}):
    GET /api/v1/status   -> {total, online, offline, avgLatencyMs}
    GET /api/v1/proxies  -> [{stableId, name, groupName, online, latencyMs, lastCheck, ...}]
    GET /api/v1/system/info -> {version, uptime, uptimeSec, instance}
    GET /config/{stableId}  -> live probe for one proxy (used to force a "deep check")
  Prometheus: /metrics (xray_proxy_status, xray_proxy_latency_ms) — we prefer the JSON API.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Optional

import httpx

from app.services import storage
from app.models.settings import AppSettings, XrayCheckerConfig

CONTAINER_NAME = "xray-checker"


def _network() -> str:
    """Shared docker network for DooD (set by docker-compose). When present, the
    backend runs in a container and reaches the checker by container name."""
    return os.getenv("XRAY_CHECKER_NETWORK", "").strip()


class CheckerError(Exception):
    pass


def _cfg() -> XrayCheckerConfig:
    return AppSettings(**storage.load_settings()).xray_checker


def _base_url(cfg: Optional[XrayCheckerConfig] = None) -> str:
    cfg = cfg or _cfg()
    if _network():
        # DooD: the backend is a container on the shared network — reach the
        # checker by its container name on the checker's internal port (2112).
        return f"http://{CONTAINER_NAME}:2112"
    # Bare-metal/host mode: scrape via the published host port.
    return f"http://127.0.0.1:{cfg.metrics_port}"


# ── Docker process lifecycle ──────────────────────────────────

# Sentinel returned by _docker when the `docker` binary is not on PATH.
_NO_DOCKER = "\x00docker-not-found"


async def _docker(*args: str, timeout: int = 60) -> tuple[int, str]:
    """Run a `docker` CLI command; return (exit_code, combined_output).

    Returns (127, _NO_DOCKER) if the `docker` executable isn't found on PATH
    (or asyncio subprocesses aren't supported on this event loop) — callers
    turn that into a friendly "Docker недоступен" message instead of a raw errno.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except (FileNotFoundError, NotImplementedError, OSError):
        return 127, _NO_DOCKER
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise CheckerError(f"docker {' '.join(args)} timed out after {timeout}s")
    return proc.returncode or 0, (out or b"").decode(errors="replace")


def _require_docker(rc: int, out: str) -> None:
    """Raise a clear CheckerError if `docker` isn't available on the host."""
    if rc == 127 and out == _NO_DOCKER:
        raise CheckerError(
            "Docker не найден на хосте node-assistant. Установите Docker (или "
            "добавьте бинарник в PATH процесса бэкенда), чтобы запускать xray-checker."
        )


async def container_state() -> str:
    """Return 'running', 'exited', 'missing', or 'no-docker'."""
    rc, out = await _docker(
        "inspect", "-f", "{{.State.Status}}", CONTAINER_NAME, timeout=15
    )
    if rc == 127 and out == _NO_DOCKER:
        return "no-docker"
    if rc != 0:
        # Distinguish "no such container" from "docker not available".
        if "No such object" in out or "no such" in out.lower():
            return "missing"
        rc2, out2 = await _docker("version", "-f", "{{.Server.Version}}", timeout=10)
        return "no-docker" if rc2 != 0 else "missing"
    return (out.strip() or "unknown")


async def start(cfg: Optional[XrayCheckerConfig] = None) -> None:
    """(Re)create and start the checker container from the current settings."""
    cfg = cfg or _cfg()
    if not cfg.subscription_url.strip():
        raise CheckerError("SUBSCRIPTION_URL не задан — укажите ссылку подписки в Настройках.")

    # Fail fast with a clear message if Docker isn't on the host.
    rc0, out0 = await _docker("version", "-f", "{{.Server.Version}}", timeout=10)
    _require_docker(rc0, out0)

    # Remove any previous container (ignore errors if absent), then run fresh.
    await _docker("rm", "-f", CONTAINER_NAME, timeout=30)

    run_args = [
        "run", "-d",
        "--name", CONTAINER_NAME,
        "--restart", "unless-stopped",
    ]
    net = _network()
    if net:
        # Join the backend's docker network so we can reach it by name (DooD).
        run_args += ["--network", net]
    run_args += [
        # Publish to the host too (optional: lets an admin open the status page).
        "-p", f"{cfg.metrics_port}:2112",
        "-e", f"SUBSCRIPTION_URL={cfg.subscription_url}",
        "-e", f"PROXY_CHECK_INTERVAL={cfg.check_interval}",
        "-e", f"PROXY_CHECK_METHOD={cfg.check_method}",
        "-e", "METRICS_PORT=2112",
        cfg.image,
    ]
    rc, out = await _docker(*run_args, timeout=120)
    _require_docker(rc, out)
    if rc != 0:
        raise CheckerError(f"Не удалось запустить контейнер: {out.strip()[:400]}")


async def stop() -> None:
    await _docker("stop", CONTAINER_NAME, timeout=30)


async def restart() -> None:
    rc, out = await _docker("restart", CONTAINER_NAME, timeout=60)
    if rc != 0:
        # Container may not exist yet — create it.
        await start()


async def get_logs(tail: int = 200) -> str:
    rc, out = await _docker("logs", "--tail", str(tail), CONTAINER_NAME, timeout=20)
    if rc != 0:
        return out.strip() or "(логи недоступны — контейнер не запущен)"
    return out


async def update() -> dict[str, Any]:
    """Auto-update pipeline: pull the new image, then recreate the container.

    Docker-image strategy (chosen): `docker pull` fetches the new build; on
    success we recreate the container on the new image. If the pull fails, the
    running container is untouched — the panel keeps working on the old image.
    """
    cfg = _cfg()
    rc, out = await _docker("pull", cfg.image, timeout=300)
    _require_docker(rc, out)
    if rc != 0:
        raise CheckerError(f"docker pull провалился — старый чекер сохранён. {out.strip()[:400]}")
    pulled = out.strip().splitlines()[-1] if out.strip() else ""
    # Recreate on the freshly pulled image.
    await start(cfg)
    return {"ok": True, "pull": pulled}


# ── HTTP bridge to the checker's JSON API ─────────────────────

async def _get_json(path: str, cfg: Optional[XrayCheckerConfig] = None,
                    timeout: float = 8.0) -> Any:
    url = f"{_base_url(cfg)}{path}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url)
    resp.raise_for_status()
    data = resp.json()
    # The checker wraps payloads in { success, data, error }.
    if isinstance(data, dict) and "data" in data and set(data.keys()) <= {"success", "data", "error"}:
        return data["data"]
    return data


async def fetch_status() -> dict[str, Any]:
    """GET /api/v1/status -> {total, online, offline, avgLatencyMs}."""
    return await _get_json("/api/v1/status")


async def fetch_proxies() -> list[dict[str, Any]]:
    """GET /api/v1/proxies -> list of proxy dicts."""
    data = await _get_json("/api/v1/proxies")
    return data if isinstance(data, list) else data.get("proxies", []) if isinstance(data, dict) else []


async def fetch_system_info() -> dict[str, Any]:
    try:
        return await _get_json("/api/v1/system/info")
    except Exception:
        return {}


async def trigger_deep_check() -> dict[str, Any]:
    """Force an immediate live probe of every proxy.

    Hitting `/config/{stableId}` makes the checker perform an on-demand probe of
    that proxy (the same endpoint uptime monitors use). We fire them concurrently,
    then the caller re-scrapes /api/v1/proxies for fresh results.
    """
    proxies = await fetch_proxies()
    base = _base_url()

    async def _probe(client: httpx.AsyncClient, stable_id: str) -> None:
        try:
            await client.get(f"{base}/config/{stable_id}", timeout=15.0)
        except Exception:
            pass

    async with httpx.AsyncClient() as client:
        await asyncio.gather(*[
            _probe(client, p["stableId"]) for p in proxies if p.get("stableId")
        ])
    return {"triggered": len(proxies)}
