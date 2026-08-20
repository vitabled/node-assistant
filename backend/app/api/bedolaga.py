"""
BEDOLAGA support integration — API routes.

Proxies the panel's frontend to the bot's own Web API (bedolaga_client), plus
local config (base_url/token, AI auto-responder settings) persisted encrypted
in bedolaga_store. See docs/skills/ for the source project this was inspired by.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.services import bedolaga_store
from app.services.bedolaga_client import BedolagaClient

router = APIRouter(prefix="/api/bedolaga")


def _client() -> Optional[BedolagaClient]:
    cfg = bedolaga_store.get_config()
    token = bedolaga_store.get_token()
    if not cfg.get("base_url") or not token:
        return None
    return BedolagaClient(cfg["base_url"], token, cfg.get("auth_header", "X-API-Key"))


# ── настройки подключения ──────────────────────────────────────

class ConnectionConfig(BaseModel):
    base_url: str
    token: Optional[str] = None  # omit to keep existing
    auth_header: str = "X-API-Key"


@router.get("/config")
async def get_config():
    return bedolaga_store.get_config()


@router.post("/config")
async def save_config(body: ConnectionConfig):
    bedolaga_store.save_config(base_url=body.base_url, token=body.token, auth_header=body.auth_header)
    return {"ok": True}


@router.post("/config/test")
async def test_connection():
    client = _client()
    if not client:
        return {"ok": False, "error": "webapi не настроен: заполните base_url и токен"}
    data, err = await client.health()
    if err:
        return {"ok": False, "error": err}
    return {"ok": True, "health": data}


# ── AI-автоответчик (Shadow-режим, ворота, пост-фильтр) ─────────

class AiConfig(BaseModel):
    ai_enabled: bool = False
    shadow_mode: bool = True
    ai_provider_base_url: Optional[str] = None
    ai_provider_key: Optional[str] = None
    ai_model: Optional[str] = None
    telegram_topic_chat_id: Optional[str] = None
    telegram_topic_thread_id: Optional[str] = None
    max_ai_replies_per_ticket: int = 2
    allowed_domains: list[str] = []


@router.get("/ai-config")
async def get_ai_config():
    return bedolaga_store.get_config()


@router.post("/ai-config")
async def save_ai_config(body: AiConfig):
    bedolaga_store.save_config(
        ai_enabled=body.ai_enabled,
        shadow_mode=body.shadow_mode,
        ai_provider_base_url=body.ai_provider_base_url,
        ai_provider_key=body.ai_provider_key,
        ai_model=body.ai_model,
        telegram_topic_chat_id=body.telegram_topic_chat_id,
        telegram_topic_thread_id=body.telegram_topic_thread_id,
        max_ai_replies_per_ticket=body.max_ai_replies_per_ticket,
        allowed_domains=body.allowed_domains,
    )
    return {"ok": True}


# ── тикеты (Чаты клиентов / Канбан) ──────────────────────────────

@router.get("/tickets")
async def list_tickets(status: Optional[str] = None, limit: int = 50, offset: int = 0):
    client = _client()
    if not client:
        return {"items": [], "total": 0, "not_configured": True}
    data, err = await client.list_tickets(status=status, limit=limit, offset=offset)
    if err:
        return {"items": [], "total": 0, "error": err}
    return data


@router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: int):
    client = _client()
    if not client:
        return {"error": "webapi не настроен"}
    data, err = await client.get_ticket(ticket_id)
    if err:
        return {"error": err}
    return data


class ReplyBody(BaseModel):
    message: str


@router.post("/tickets/{ticket_id}/reply")
async def reply_ticket(ticket_id: int, body: ReplyBody):
    client = _client()
    if not client:
        return {"ok": False, "error": "webapi не настроен"}
    data, err = await client.reply_ticket(ticket_id, body.message)
    if err:
        return {"ok": False, "error": err}
    return {"ok": True, "result": data}


class PriorityBody(BaseModel):
    priority: str


@router.post("/tickets/{ticket_id}/priority")
async def set_priority(ticket_id: int, body: PriorityBody):
    client = _client()
    if not client:
        return {"ok": False, "error": "webapi не настроен"}
    data, err = await client.set_priority(ticket_id, body.priority)
    if err:
        return {"ok": False, "error": err}
    return {"ok": True, "result": data}


# ── дашборд ────────────────────────────────────────────────────

@router.get("/dashboard")
async def dashboard():
    client = _client()
    if not client:
        return {"not_configured": True}
    tickets_open, err1 = await client.list_tickets(status="open", limit=1)
    tickets_all, err2 = await client.list_tickets(limit=1)
    users, err3 = await client.list_users(limit=1)
    health, err4 = await client.health()
    return {
        "open_tickets": (tickets_open or {}).get("total", 0) if not err1 else None,
        "total_tickets": (tickets_all or {}).get("total", 0) if not err2 else None,
        "total_users": (users or {}).get("total", 0) if not err3 else None,
        "bot_health": health,
        "errors": [e for e in [err1, err2, err3, err4] if e],
    }
