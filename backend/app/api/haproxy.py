"""
HAPROXY (NodeFlow) config + generic proxy API (Wave-7). Account-gated.

Two modes (HaproxyConfig.mode):
- `local` (default) — a SHARED local NodeFlow stack auto-deployed over the host
  Docker socket (services/nodeflow_server.py). The proxy targets the internal
  container URL with the GLOBAL admin token; SSRF guard is exempt for that URL.
- `remote` — an EXISTING panel registered per-account (URL + Fernet-encrypted token).

Endpoints:
- GET  /api/haproxy/config → {enabled, mode, base_url, has_token, configured, local}
- POST /api/haproxy/config → set enabled/mode + (remote) base_url + admin token
- POST /api/haproxy/deploy → deploy/redeploy the LOCAL stack (background), set local+enabled
- POST /api/haproxy/stop   → tear down the LOCAL stack
- GET  /api/haproxy/local/status → local stack container states + reachability
- POST /api/haproxy/test   → health + authenticated probe of the active panel
- ANY  /api/haproxy/proxy/{path} → forward to NodeFlow /api/v1/{path} with the admin bearer
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.models.settings import AppSettings
from app.services import nodeflow_client, nodeflow_server, storage
from app.services.net_guard import is_safe_url

router = APIRouter(prefix="/api/haproxy")

_STRIP_RESP_HEADERS = {
    "content-encoding", "transfer-encoding", "connection", "keep-alive",
    "content-length", "server", "date",
}
_STRIP_REQ_HEADERS = {
    "host", "authorization", "cookie", "content-length", "connection",
    "accept-encoding", "origin", "referer",
}


class HaproxyConfigBody(BaseModel):
    enabled: bool = False
    mode: str = Field("local", pattern="^(local|remote)$")
    base_url: str = ""  # remote only
    admin_token: str = Field("", description="remote only; blank keeps the stored one")


class HaproxyDeployBody(BaseModel):
    # Optional override for the agent-mTLS SAN host (default: the host's public IP).
    san_host: str = ""


def _load() -> AppSettings:
    return AppSettings(**storage.load_settings())


def _configured(cfg) -> bool:
    if cfg.mode == "local":
        return bool(nodeflow_server.admin_token())
    return bool(cfg.base_url and cfg.admin_token_enc)


def _public(cfg) -> dict:
    return {
        "enabled": cfg.enabled,
        "mode": cfg.mode,
        "base_url": cfg.base_url,
        "has_token": bool(cfg.admin_token_enc) if cfg.mode == "remote" else bool(nodeflow_server.admin_token()),
        "configured": _configured(cfg),
    }


def _client_or_400(cfg) -> nodeflow_client.NodeFlowClient:
    if cfg.mode == "local":
        token = nodeflow_server.admin_token() or ""
        if not token:
            raise HTTPException(409, "Локальная панель NodeFlow ещё не развёрнута — нажмите «Развернуть»")
        return nodeflow_client.NodeFlowClient(
            nodeflow_server.internal_base_url(), token, allow_internal=True)
    # remote
    if not cfg.base_url:
        raise HTTPException(400, "NodeFlow не настроен: укажите URL панели")
    token = nodeflow_client.decrypt(cfg.admin_token_enc) or ""
    if not token:
        raise HTTPException(400, "NodeFlow не настроен: укажите admin-токен панели")
    return nodeflow_client.NodeFlowClient(cfg.base_url, token)


@router.get("/config")
async def get_config() -> dict:
    cfg = _load().haproxy
    result = _public(cfg)
    if cfg.mode == "local":
        result["local"] = await nodeflow_server.status()
    return result


@router.post("/config")
async def save_config(body: HaproxyConfigBody) -> dict:
    base = (body.base_url or "").strip().rstrip("/")
    if body.mode == "remote" and base and not is_safe_url(base):
        raise HTTPException(
            422, "URL панели не разрешён: нужен http(s) с публичным (маршрутизируемым) хостом")
    data = storage.load_settings()
    current = AppSettings(**data).haproxy
    cfg = {
        **current.model_dump(),
        "enabled": body.enabled,
        "mode": body.mode,
        "base_url": base,
    }
    if body.mode == "remote" and body.admin_token.strip():
        cfg["admin_token_enc"] = nodeflow_client.encrypt(body.admin_token.strip())
    data["haproxy"] = cfg
    storage.save_settings(data)
    result = _public(AppSettings(**data).haproxy)
    result["ok"] = True
    return result


@router.post("/deploy")
async def deploy_local(body: HaproxyDeployBody) -> dict:
    """Deploy/redeploy the local NodeFlow stack in the background and switch the
    account to local mode + enabled. Returns immediately; poll /local/status."""
    data = storage.load_settings()
    cfg = {**AppSettings(**data).haproxy.model_dump(), "mode": "local", "enabled": True}
    data["haproxy"] = cfg
    storage.save_settings(data)

    status = await nodeflow_server.status()
    if status.get("panel") == "no-docker":
        return {"ok": True, "started": False,
                "warning": "Docker недоступен в контейнере бэкенда — локальный деплой невозможен",
                "local": status}
    if not status.get("images_built"):
        return {"ok": True, "started": False,
                "warning": "Образы NodeFlow не собраны. На хосте выполните: "
                           "docker compose --profile nodeflow-build build nodeflow-panel nodeflow-migrate",
                "local": status}
    asyncio.create_task(nodeflow_server.deploy_bg(body.san_host.strip() or None))
    return {"ok": True, "started": True, "local": await nodeflow_server.status()}


@router.post("/stop")
async def stop_local() -> dict:
    try:
        await nodeflow_server.stop()
    except nodeflow_server.NodeFlowServerError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"ok": True, "local": await nodeflow_server.status()}


@router.get("/local/status")
async def local_status() -> dict:
    return await nodeflow_server.status()


@router.post("/test")
async def test_connection() -> dict:
    cfg = _load().haproxy
    client = _client_or_400(cfg)
    try:
        return await client.check()
    except nodeflow_client.NodeFlowError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.api_route("/proxy/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(path: str, request: Request) -> Response:
    cfg = _load().haproxy
    client = _client_or_400(cfg)

    body = await request.body()
    fwd_headers = {
        k: v for k, v in request.headers.items() if k.lower() not in _STRIP_REQ_HEADERS
    }
    try:
        upstream = await client.request(
            request.method, path,
            params=dict(request.query_params),
            content=body if body else None,
            headers=fwd_headers or None,
        )
    except nodeflow_client.NodeFlowError as exc:
        raise HTTPException(502, str(exc)) from exc

    resp_headers = {
        k: v for k, v in upstream.headers.items() if k.lower() not in _STRIP_RESP_HEADERS
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=upstream.headers.get("content-type"),
    )
