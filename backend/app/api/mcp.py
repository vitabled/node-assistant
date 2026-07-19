"""
MCP config + status API (Ф3). Account-gated (wired under require_account).

- GET  /api/mcp/config  → current config + the owner's copyable auth token + endpoint.
- POST /api/mcp/config  → persist enable/readonly/port, (re)start or stop the
  container. Best-effort container control: if Docker is absent we still persist
  and return 200 with a `warning` (mirrors the xray-checker save endpoint).
- GET  /api/mcp/status  → container state + reachability.

The MCP_AUTH_TOKEN is generated on first enable, stored Fernet-encrypted, and
returned in full ONLY here (owner-authenticated) so it can be pasted into an
external MCP client.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.models.settings import AppSettings
from app.services import mcp_server, storage

router = APIRouter(prefix="/api/mcp")


class McpConfigBody(BaseModel):
    enabled: bool = False
    readonly: bool = True
    http_port: int = Field(3100, ge=1, le=65535)


def _public(account_id: str | None = None) -> dict:
    data = storage.load_settings(account_id)
    s = AppSettings(**data)
    cfg = s.mcp
    rw_ready = bool(s.remnawave.panel_url and s.remnawave.api_token)
    # Read-only: never generates a token here (a GET must not write). The token is
    # minted in save_config on enable.
    token = mcp_server.read_auth_token(account_id) if cfg.enabled else None
    return {
        "enabled": cfg.enabled,
        "readonly": cfg.readonly,
        "http_port": cfg.http_port,
        "image": cfg.image,
        "endpoint": mcp_server.endpoint(cfg),
        "auth_token": token,  # plaintext, owner-only; null until enabled
        "remnawave_ready": rw_ready,
    }


@router.get("/config")
async def get_config() -> dict:
    return _public()


@router.post("/config")
async def save_config(body: McpConfigBody) -> dict:
    # Single load → merge → save (one write; no separate token-write races with
    # this one). The token is generated inline on the FIRST enable.
    data = storage.load_settings()
    current = AppSettings(**data).mcp
    mcp_cfg = {
        **current.model_dump(),
        "enabled": body.enabled,
        "readonly": body.readonly,
        "http_port": body.http_port,
    }
    if body.enabled and not mcp_cfg.get("auth_token_enc"):
        mcp_cfg["auth_token_enc"] = mcp_server.encrypt_new_token()
    data["mcp"] = mcp_cfg
    storage.save_settings(data)

    result = _public()
    if body.enabled:
        try:
            await mcp_server.start()
        except mcp_server.McpError as exc:
            result["warning"] = f"Настройки сохранены, но MCP не запущен: {exc}"
    else:
        try:
            await mcp_server.stop()
        except Exception:
            pass
    result["ok"] = True
    return result


@router.get("/status")
async def get_status() -> dict:
    return await mcp_server.status()
