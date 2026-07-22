"""CLIProxyAPI gateway routes (Wave-7 Plan F Ф3) — /api/cliproxy.

The gateway is shared infrastructure, like the xray-checker container: one per
installation. Whoever turned it on owns the pool of provider accounts; everyone
else may use it but not reconfigure it. That is stated in the UI rather than
hidden, and enforced here.

The Management key never leaves the server. The client master key is returned to
the owner (they may want it for an external tool) and to nobody else.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import accounts, cliproxy_management as mgmt, cliproxy_server as srv
from app.services import storage
from app.models.settings import AppSettings

router = APIRouter(prefix="/api/cliproxy")


class ConfigBody(BaseModel):
    enabled: bool = False
    image: str = Field(default=srv.DEFAULT_IMAGE, max_length=200)


class OAuthStart(BaseModel):
    provider: str = Field(..., max_length=40)


class OAuthFinish(BaseModel):
    state: str = Field(default="", max_length=400)
    code: str = Field(default="", max_length=2000)
    redirect_url: str = Field(default="", max_length=4000)


def _me() -> str:
    return accounts.current_account.get() or ""


async def _require_owner() -> None:
    st = await srv.status()
    if not st["owner_is_me"]:
        raise HTTPException(
            403, "Шлюз настроен другим аккаунтом — изменять его может только он",
        )


def _client() -> mgmt.ManagementClient:
    key = srv.decrypt(srv._cfg().cliproxy_mgmt_key_enc)
    if not key:
        raise HTTPException(400, "Шлюз ещё не настроен")
    return mgmt.ManagementClient(srv.internal_base_url(), key)


async def _call(coro):
    try:
        return await coro
    except mgmt.ManagementAuthError:
        # Surfaced, never retried — five of these ban our IP for half an hour.
        raise HTTPException(502, "Management API отклонил ключ шлюза")
    except mgmt.ManagementDisabled as e:
        raise HTTPException(502, str(e))
    except mgmt.ManagementError as e:
        raise HTTPException(502, str(e))


# ── config / lifecycle ────────────────────────────────────────
@router.get("/config")
async def get_config() -> dict[str, Any]:
    cfg = srv._cfg()
    st = await srv.status()
    out: dict[str, Any] = {
        "enabled": cfg.cliproxy_enabled,
        "image": cfg.cliproxy_image or srv.DEFAULT_IMAGE,
        "container": st["container"],
        "owner_is_me": st["owner_is_me"],
        "base_url": st["base_url"],
        "has_keys": st["has_keys"],
    }
    # The client key is copyable BY THE OWNER only; the management key never.
    if st["owner_is_me"]:
        out["master_key"] = srv.decrypt(cfg.cliproxy_master_key_enc) or ""
    return out


@router.post("/config")
async def set_config(body: ConfigBody) -> dict[str, Any]:
    await _require_owner()
    aid = _me()
    raw = storage.load_settings(aid)
    s = AppSettings(**raw)
    s.ai.cliproxy_enabled = body.enabled
    s.ai.cliproxy_image = body.image.strip() or srv.DEFAULT_IMAGE
    raw["ai"] = s.ai.model_dump()
    storage.save_settings(raw, aid)

    warning: Optional[str] = None
    if body.enabled:
        try:
            await srv.start(aid)
        except srv.CliProxyError as e:
            # Docker missing is a warning, not a 500 — same posture as MCP and
            # the xray-checker.
            warning = str(e)
    else:
        try:
            await srv.stop()
        except srv.CliProxyError as e:
            warning = str(e)
    return {"ok": True, "warning": warning}


@router.get("/status")
async def get_status() -> dict[str, Any]:
    return await srv.status()


@router.post("/start")
async def start_gateway() -> dict[str, Any]:
    await _require_owner()
    try:
        await srv.start(_me())
    except srv.CliProxyError as e:
        return {"ok": False, "warning": str(e)}
    return {"ok": True}


@router.post("/stop")
async def stop_gateway() -> dict[str, Any]:
    await _require_owner()
    try:
        await srv.stop()
    except srv.CliProxyError as e:
        return {"ok": False, "warning": str(e)}
    return {"ok": True}


# ── provider accounts ─────────────────────────────────────────
@router.get("/accounts")
async def list_accounts() -> dict[str, Any]:
    files = await _call(_client().list_auth_files())
    return {"accounts": [mgmt.scrub_auth_file(f) for f in files]}


@router.delete("/accounts/{name}")
async def delete_account(name: str) -> dict[str, Any]:
    await _require_owner()
    await _call(_client().delete_auth_file(name))
    return {"ok": True}


class AccountPatch(BaseModel):
    disabled: bool


@router.patch("/accounts/{name}")
async def patch_account(name: str, body: AccountPatch) -> dict[str, Any]:
    await _require_owner()
    await _call(_client().set_auth_disabled(name, body.disabled))
    return {"ok": True}


# ── OAuth onboarding ──────────────────────────────────────────
@router.post("/oauth/start")
async def oauth_start(body: OAuthStart) -> dict[str, Any]:
    await _require_owner()
    try:
        data = await _call(_client().start_oauth(body.provider))
    except HTTPException:
        raise
    url = (data or {}).get("url", "")
    if not url.startswith("https://"):
        # The UI renders this as a link; anything else is not clickable.
        raise HTTPException(502, "Шлюз вернул недопустимую ссылку авторизации")
    return {"url": url, "state": (data or {}).get("state", "")}


@router.post("/oauth/callback")
async def oauth_callback(body: OAuthFinish) -> dict[str, Any]:
    await _require_owner()
    payload: dict[str, Any] = {}
    if body.redirect_url.strip():
        payload["redirect_url"] = body.redirect_url.strip()
    if body.state.strip():
        payload["state"] = body.state.strip()
    if body.code.strip():
        payload["code"] = body.code.strip()
    if not payload.get("redirect_url") and not payload.get("code"):
        raise HTTPException(422, "Нужен redirect_url или code")
    await _call(_client().finish_oauth(payload))
    return {"ok": True}


@router.get("/oauth/status")
async def oauth_status(state: str) -> dict[str, Any]:
    data = await _call(_client().oauth_status(state))
    return {"status": (data or {}).get("status", "wait"),
            "error": (data or {}).get("error")}
