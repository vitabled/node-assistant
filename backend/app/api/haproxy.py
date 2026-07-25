"""
HAPROXY (NodeFlow) config + generic proxy API (Wave-7). Account-gated.

- GET  /api/haproxy/config → {enabled, base_url, has_token, configured}
- POST /api/haproxy/config → set base_url + admin token (Fernet-encrypted) + enabled;
  validates the URL is a public http(s) host (SSRF guard) before saving.
- POST /api/haproxy/test   → health + authenticated probe against the panel.
- ANY  /api/haproxy/proxy/{path} → forward to NodeFlow `/api/v1/{path}` with the admin
  bearer injected server-side. Raw-body passthrough → JSON *and* multipart agent-release
  uploads both work. Only the `/api/v1/` prefix is reachable; the token is never exposed.

Per-account isolation: each account registers its OWN NodeFlow instance (base_url + token);
the proxy always targets that account's configured panel and injects that account's token.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.models.settings import AppSettings
from app.services import nodeflow_client, storage
from app.services.net_guard import is_safe_url

router = APIRouter(prefix="/api/haproxy")

# Response/hop headers we must NOT copy back verbatim (they describe OUR transfer, not the
# panel's payload, or would break the client's decoding).
_STRIP_RESP_HEADERS = {
    "content-encoding", "transfer-encoding", "connection", "keep-alive",
    "content-length", "server", "date",
}
# Request headers we must NOT forward (auth is injected; host/length are recomputed).
_STRIP_REQ_HEADERS = {
    "host", "authorization", "cookie", "content-length", "connection",
    "accept-encoding", "origin", "referer",
}


class HaproxyConfigBody(BaseModel):
    enabled: bool = False
    base_url: str = ""
    # Blank on save = keep the stored token (edit URL/enabled without re-typing it).
    admin_token: str = Field("", description="PANEL_ADMIN_TOKEN; blank keeps the stored one")


def _load() -> AppSettings:
    return AppSettings(**storage.load_settings())


def _public(cfg) -> dict:
    return {
        "enabled": cfg.enabled,
        "base_url": cfg.base_url,
        "has_token": bool(cfg.admin_token_enc),
        "configured": bool(cfg.base_url and cfg.admin_token_enc),
    }


def _client_or_400(cfg) -> nodeflow_client.NodeFlowClient:
    if not cfg.base_url:
        raise HTTPException(400, "NodeFlow не настроен: укажите URL панели")
    token = nodeflow_client.decrypt(cfg.admin_token_enc) or ""
    if not token:
        raise HTTPException(400, "NodeFlow не настроен: укажите admin-токен панели")
    return nodeflow_client.NodeFlowClient(cfg.base_url, token)


@router.get("/config")
async def get_config() -> dict:
    return _public(_load().haproxy)


@router.post("/config")
async def save_config(body: HaproxyConfigBody) -> dict:
    base = (body.base_url or "").strip().rstrip("/")
    if base and not is_safe_url(base):
        raise HTTPException(
            422, "URL панели не разрешён: нужен http(s) с публичным (маршрутизируемым) хостом"
        )
    data = storage.load_settings()
    current = AppSettings(**data).haproxy
    cfg = {
        **current.model_dump(),
        "enabled": body.enabled,
        "base_url": base,
    }
    if body.admin_token.strip():
        cfg["admin_token_enc"] = nodeflow_client.encrypt(body.admin_token.strip())
    data["haproxy"] = cfg
    storage.save_settings(data)
    result = _public(AppSettings(**data).haproxy)
    result["ok"] = True
    return result


@router.post("/test")
async def test_connection() -> dict:
    cfg = _load().haproxy
    client = _client_or_400(cfg)
    try:
        return await client.check()
    except nodeflow_client.NodeFlowError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.api_route(
    "/proxy/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def proxy(path: str, request: Request) -> Response:
    cfg = _load().haproxy
    client = _client_or_400(cfg)

    body = await request.body()
    fwd_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _STRIP_REQ_HEADERS
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
        k: v for k, v in upstream.headers.items()
        if k.lower() not in _STRIP_RESP_HEADERS
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=upstream.headers.get("content-type"),
    )
