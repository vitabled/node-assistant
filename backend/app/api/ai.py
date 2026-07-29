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

from app.services import accounts, ai_agent, ai_tools, ai_web, net_guard, storage

router = APIRouter(prefix="/api/ai")

_PROVIDERS = ("openai", "anthropic")
_GATEWAYS = ("none", "cliproxy")


class AiConfigBody(BaseModel):
    enabled: bool = False
    provider: str = "openai"
    base_url: str = Field("https://api.openai.com/v1", max_length=300)
    model: str = Field("gpt-4o-mini", max_length=120)
    api_key: str | None = None  # write-only; blank/None keeps the existing key
    max_steps: int = Field(6, ge=1, le=20)
    readonly: bool = True
    active_preset_id: str = Field("", max_length=64)  # Plan I; "" = default preset
    gateway: str = "none"  # Plan J; none | cliproxy
    use_mcp: bool = False  # Wave-7 Plan E Ф2: borrow the MCP server's tools
    # Веб-доступ. `web_api_key` — write-only, как и ключ провайдера.
    web_enabled: bool = True
    web_provider: str = "duckduckgo"
    web_api_key: str | None = None
    web_base_url: str = Field("", max_length=300)
    web_max_results: int = Field(5, ge=1, le=ai_web.MAX_RESULTS)

    @field_validator("provider")
    @classmethod
    def _provider(cls, v: str) -> str:
        if v not in _PROVIDERS:
            raise ValueError(f"provider должен быть одним из {_PROVIDERS}")
        return v

    @field_validator("gateway")
    @classmethod
    def _gateway(cls, v: str) -> str:
        if v not in _GATEWAYS:
            raise ValueError(f"gateway должен быть одним из {_GATEWAYS}")
        return v

    @field_validator("web_provider")
    @classmethod
    def _web_provider(cls, v: str) -> str:
        if v not in ai_web.WEB_PROVIDERS:
            raise ValueError(f"web_provider должен быть одним из {ai_web.WEB_PROVIDERS}")
        return v

    @field_validator("web_base_url")
    @classmethod
    def _web_base_url(cls, v: str) -> str:
        # Адрес своего SearXNG задаёт пользователь, а ходит по нему НАШ сервер
        # изнутри сети — гард обязателен и здесь, и при каждом запросе (то же
        # правило, что у `openstack.auth_url` и реестра чекеров).
        v = (v or "").strip()
        if v and not net_guard.is_safe_url(v):
            raise ValueError("нужен http(s) на публичный хост (защита от SSRF)")
        return v


class Attachment(BaseModel):
    """Вложение чата. Файлы НЕ персистятся: они относятся к одному вопросу, а не
    к аккаунту, поэтому едут в теле запроса и живут ровно столько же."""
    name: str = Field("", max_length=200)
    mime: str = Field("", max_length=100)
    # Текстовый файл — как есть; картинка — base64 (форму блока выбирает
    # ai_agent.build_user_content, она у провайдеров разная).
    text: str = Field("", max_length=ai_agent.MAX_TEXT_CHARS)
    data_b64: str = Field("", max_length=6_000_000)


class HistoryMsg(BaseModel):
    """Реплика прошлого хода. Историю ведёт клиент: сервер переписку не хранит,
    поэтому ей нечего утекать и нечего чистить по расписанию."""
    role: str = Field("user", max_length=16)
    content: str = Field("", max_length=ai_agent.MAX_HISTORY_CHARS)


class ChatBody(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=8000)
    attachments: list[Attachment] = Field(default_factory=list,
                                          max_length=ai_agent.MAX_ATTACHMENTS)
    history: list[HistoryMsg] = Field(
        default_factory=list, max_length=ai_agent.MAX_HISTORY_MESSAGES * 2)


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
        "gateway": cfg.gateway,
        "use_mcp": cfg.use_mcp,
        "web_enabled": cfg.web_enabled,
        "web_provider": cfg.web_provider,
        "web_base_url": cfg.web_base_url,
        "web_max_results": cfg.web_max_results,
        "web_needs_key": ai_web.needs_key(cfg.web_provider),
        "has_web_key": bool(cfg.web_api_key_enc),  # never the key itself
        "has_key": bool(cfg.api_key_enc),  # never the key itself
        # Есть ли ЧЕМ авторизоваться: через CLIProxyAPI ключ провайдера не нужен,
        # доступ даёт OAuth-аккаунт внутри шлюза. Фронт гейтит композер по этому
        # полю, а не по `has_key`.
        "auth_ready": bool(ai_agent.effective_target(cfg)[1]),
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
        "gateway": body.gateway,
        "use_mcp": body.use_mcp,
        "web_enabled": body.web_enabled,
        "web_provider": body.web_provider,
        "web_base_url": body.web_base_url.strip(),
        "web_max_results": body.web_max_results,
    }
    # Only overwrite the key when a fresh non-blank one is supplied.
    if body.api_key and body.api_key.strip():
        ai_cfg["api_key_enc"] = ai_agent.encrypt_key(body.api_key.strip())
    if body.web_api_key and body.web_api_key.strip():
        ai_cfg["web_api_key_enc"] = ai_agent.encrypt_key(body.web_api_key.strip())
    data["ai"] = ai_cfg
    storage.save_settings(data)
    return {"ok": True, **_public()}


@router.get("/models")
async def list_models() -> dict:
    """Model ids from the configured endpoint — ЛЮБОГО провайдера, не только
    шлюза CLIProxyAPI: и OpenAI-совместимые, и Anthropic отдают один и тот же
    `{"data":[{"id":…}]}`. Гейт по gateway снят (Волна 6, План C Ф2), иначе
    каталог не подгружался бы у тех, кто ходит к провайдеру напрямую.

    Никогда не ошибается: пустой список = «вводите модель вручную»."""
    cfg = ai_agent._cfg()
    _, key = ai_agent.effective_target(cfg)
    return {"models": await ai_agent.list_models(cfg, key)}


@router.post("/chat")
async def chat(body: ChatBody) -> StreamingResponse:
    account_id = accounts.current_account.get() or ""
    cfg = ai_agent._cfg(account_id)

    async def gen():
        if not cfg.enabled:
            yield json.dumps({"type": "error", "message": "ИИ-агент выключен."}) + "\n"
            return
        async for event in ai_agent.run_agent(
            body.prompt, cfg, account_id,
            attachments=[a.model_dump() for a in body.attachments],
            history=[m.model_dump() for m in body.history],
        ):
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@router.get("/tools")
async def tools_status() -> dict:
    """What the assistant can actually reach right now.

    The UI shows this ABOVE the composer so the user knows the boundaries before
    asking, rather than learning them from a refusal. Three honest states:
    built-in only / built-in + Remnawave / built-in only because MCP belongs to
    another account.
    """
    from app.services import mcp_server

    account_id = accounts.current_account.get() or ""
    cfg = ai_agent._cfg(account_id)
    ctx = ai_agent.build_context(cfg, account_id)
    names = [t.name for t in ai_tools.available(ctx)]
    base = {
        "builtin": len(names),
        "tools": names,
        "writes": not cfg.readonly,
        "web": cfg.web_enabled,
        "web_provider": ai_web.provider_label(cfg.web_provider),
    }
    if not cfg.use_mcp:
        return {**base, "mcp": 0, "reason": "off"}
    try:
        status = await mcp_server.status()
    except Exception:
        return {**base, "mcp": 0, "reason": "unavailable"}
    state = status.get("container")
    if state == "foreign":
        return {**base, "mcp": 0, "reason": "foreign"}
    if state != "running" or not status.get("reachable"):
        return {**base, "mcp": 0, "reason": "unavailable"}
    mcp = await ai_agent._mcp_tools(cfg)
    return {
        **base,
        "mcp": len(mcp),
        "capped": len(mcp) >= ai_agent.MAX_MCP_TOOLS,
        "reason": "ok" if mcp else "unavailable",
    }
