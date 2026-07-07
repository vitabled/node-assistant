"""Per-account registry of xray-checker instances.

The shared local checker (managed as a Docker container by `xray_checker.py`) is
a virtual built-in instance `id="local"`, always present and not stored. Extra
REMOTE instances — a `kutovoys/xray-checker` running on another server, reached
over HTTP — are stored per-account in `accounts/<id>/checkers.json` and polled
read-only. The single shared metrics DB discriminates rows by `checker_id`.

Instance shape: {id, name, kind:"local"|"remote", base_url, enabled, created_at}.
SSH credentials used to *deploy* a remote checker are transient (per-request,
never persisted) — only the resulting base_url is stored.
"""
from __future__ import annotations

import shlex
import time
import uuid
from typing import Optional

import httpx

from app.services import net_guard, storage
from app.services.metrics_store import LOCAL_CHECKER_ID


def _local_instance() -> dict:
    return {
        "id": LOCAL_CHECKER_ID,
        "name": "Локальный чекер",
        "kind": "local",
        "base_url": "",
        "enabled": True,
        "created_at": 0,
    }


def _normalize_url(base_url: str) -> str:
    url = base_url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ValueError("base_url должен начинаться с http:// или https://")
    return url


def list_instances(account_id: Optional[str] = None) -> list[dict]:
    """Built-in local instance first, then this account's stored remote instances."""
    return [_local_instance()] + storage.load_checkers(account_id)


def get_instance(instance_id: str, account_id: Optional[str] = None) -> Optional[dict]:
    if instance_id == LOCAL_CHECKER_ID:
        return _local_instance()
    return next(
        (c for c in storage.load_checkers(account_id) if c["id"] == instance_id), None
    )


def add_instance(name: str, base_url: str, account_id: Optional[str] = None) -> dict:
    """Register a remote checker by URL. Raises ValueError on bad/duplicate/unsafe URL."""
    url = _normalize_url(base_url)
    net_guard.assert_safe_url(url)  # SSRF: reject non-public/internal hosts
    existing = storage.load_checkers(account_id)
    if any(c["base_url"] == url for c in existing):
        raise ValueError("Инстанс с таким URL уже добавлен")
    inst = {
        "id": uuid.uuid4().hex[:12],
        "name": name.strip() or url,
        "kind": "remote",
        "base_url": url,
        "enabled": True,
        "created_at": int(time.time()),
    }
    existing.append(inst)
    storage.save_checkers(existing, account_id)
    return inst


def update_instance(
    instance_id: str,
    *,
    enabled: Optional[bool] = None,
    name: Optional[str] = None,
    account_id: Optional[str] = None,
) -> Optional[dict]:
    existing = storage.load_checkers(account_id)
    for c in existing:
        if c["id"] == instance_id:
            if enabled is not None:
                c["enabled"] = bool(enabled)
            if name is not None and name.strip():
                c["name"] = name.strip()
            storage.save_checkers(existing, account_id)
            return c
    return None


def delete_instance(instance_id: str, account_id: Optional[str] = None) -> bool:
    """Drop a remote instance from the registry. Its historical samples are LEFT
    in the metrics DB (just hidden from selectors, since the id is gone)."""
    existing = storage.load_checkers(account_id)
    kept = [c for c in existing if c["id"] != instance_id]
    if len(kept) == len(existing):
        return False
    storage.save_checkers(kept, account_id)
    return True


async def test_connection(base_url: str) -> dict:
    """Probe {base_url}/api/v1/status. {ok:True} or {ok:False, error:...}."""
    try:
        root = _normalize_url(base_url)
        net_guard.assert_safe_url(root)  # SSRF: reject non-public/internal hosts
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:
            resp = await client.get(root + "/api/v1/status")
        resp.raise_for_status()
        return {"ok": True}
    except Exception as e:  # network/HTTP failure — report, don't raise
        return {"ok": False, "error": str(e)[:200]}


def remote_deploy_script(subscription_url: str, image: str, host_port: int) -> str:
    """Bash to (re)deploy a kutovoys/xray-checker container on a remote server.
    Published on host_port so node-assistant can reach it at http://<ip>:<port>.
    subscription_url is the account's own sub (a remote box can't reach the
    internal aggregator)."""
    # shlex.quote both interpolated values (defence-in-depth: a stray quote in the
    # account's own subscription_url/image must not break out of the shell command).
    return f"""set -e
docker rm -f xray-checker >/dev/null 2>&1 || true
docker run -d --name xray-checker --restart unless-stopped \\
  -p {int(host_port)}:2112 \\
  -e SUBSCRIPTION_URL={shlex.quote(subscription_url)} \\
  -e METRICS_PORT=2112 \\
  {shlex.quote(image)}
echo "xray-checker deployed on :{int(host_port)}"
"""
