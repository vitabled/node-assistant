"""
AI agent config + chat API (Ф4). Account-gated.

- GET  /api/ai/config → provider/model/limits + `has_key` (the key is NEVER
  returned — only whether one is stored).
- POST /api/ai/config → persist provider/model/base_url/limits; a non-empty
  `api_key` is Fernet-encrypted into the vault, an omitted/blank one keeps the
  existing key.
- POST /api/ai/chat  → streams the tool-calling loop as JSONL events
  (tool_call / tool_result / text / done / error) so the UI shows tools live.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

import json

from app.services import accounts, ai_agent, storage

router = APIRouter(prefix="/api/ai")

_PROVIDERS = ("openai", "anthropic")


class AiConfigBody(BaseModel):
    enabled: bool = False
    provider: str = "openai"
    base_url: str = Field("https://api.openai.com/v1", max_length=300)
    model: str = Field("gpt-4o-mini", max_length=120)
    api_key: str | None = None  # write-only; blank/None keeps the existing key
    max_steps: int = Field(6, ge=1, le=20)
    readonly: bool = True
    active_preset_id: str = Field("", max_length=64)  # Plan I; "" = default preset

    @field_validator("provider")
    @classmethod
    def _provider(cls, v: str) -> str:
        if v not in _PROVIDERS:
            raise ValueError(f"provider должен быть одним из {_PROVIDERS}")
        return v


class ChatBody(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=8000)


def _public(account_id: str | None = None) -> dict:
    cfg = ai_agent._cfg(account_id)
    return {
        "enabled": cfg.enabled,
        "provider": cfg.provider,
        "base_url": cfg.base_url,
        "model": cfg.model,
        "max_steps": cfg.max_steps,
        "readonly": cfg.readonly,
        "active_preset_id": cfg.active_preset_id,
        "has_key": bool(cfg.api_key_enc),  # never the key itself
    }


@router.get("/config")
async def get_config() -> dict:
    return _public()


@router.post("/config")
async def save_config(body: AiConfigBody) -> dict:
    data = storage.load_settings()
    current = ai_agent._cfg()
    ai_cfg = {
        **current.model_dump(),
        "enabled": body.enabled,
        "provider": body.provider,  # already validated to be a known provider
        "base_url": body.base_url.strip(),
        "model": body.model.strip(),
        "max_steps": body.max_steps,
        "readonly": body.readonly,
        "active_preset_id": body.active_preset_id.strip(),
    }
    # Only overwrite the key when a fresh non-blank one is supplied.
    if body.api_key and body.api_key.strip():
        ai_cfg["api_key_enc"] = ai_agent.encrypt_key(body.api_key.strip())
    data["ai"] = ai_cfg
    storage.save_settings(data)
    return {"ok": True, **_public()}


@router.post("/chat")
async def chat(body: ChatBody) -> StreamingResponse:
    account_id = accounts.current_account.get() or ""
    cfg = ai_agent._cfg(account_id)

    async def gen():
        if not cfg.enabled:
            yield json.dumps({"type": "error", "message": "ИИ-агент выключен."}) + "\n"
            return
        async for event in ai_agent.run_agent(body.prompt, cfg, account_id):
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")
