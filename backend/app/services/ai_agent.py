"""
Built-in AI agent (Ф4): a provider-agnostic tool-calling loop.

Two providers are supported behind one interface: an OpenAI-compatible
`/chat/completions` endpoint and Anthropic `/v1/messages`. The agent is given a
curated set of READ-ONLY tools that call our own services in-process (rules,
subscriptions, node health, remnawave nodes) — a superset-safe mirror of the MCP
node-assistant tools, but without requiring the MCP container to be running.

`run_agent(prompt, config, account_id)` is an async generator yielding events:
  {"type": "tool_call",   "name", "args"}
  {"type": "tool_result", "name", "ok", "preview"}
  {"type": "text",        "delta"}
  {"type": "done"}
  {"type": "error",       "message"}
so the API layer can stream them and the UI can show tool-calls as they happen.

⚠️ The provider API key lives in the Fernet vault (`AiConfig.api_key_enc`) and is
NEVER logged. All errors are redacted before surfacing.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from urllib.parse import urlparse
from typing import Any, AsyncIterator, Optional

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.config import settings
from app.models.settings import AiConfig, AppSettings
from app.services import metrics_store, net_guard, prompt_presets_store, rules_store, storage
from app.services.remnawave_client import RemnavaveClient

# Cap on a single tool result serialized back into the message history (prevents
# unbounded growth / token blow-up across the tool-calling loop).
_TOOL_RESULT_CAP = 4000

log = logging.getLogger("ai")

_KEY_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._\-]{8,})")


def redact(text: str, extra: str = "") -> str:
    out = _KEY_RE.sub("[redacted]", text or "")
    if extra:
        out = out.replace(extra, "[redacted]")
    return out


# ── Fernet vault (shared key = SHA-256 of encryption_key) ─────
def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.encryption_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_key(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_key(enc: str) -> Optional[str]:
    if not enc:
        return None
    try:
        return _fernet().decrypt(enc.encode()).decode()
    except InvalidToken:
        return None


def _cfg(account_id: Optional[str] = None) -> AiConfig:
    return AppSettings(**storage.load_settings(account_id)).ai


# ── read-only tools (in-process) ──────────────────────────────
def _rw_client(account_id: str) -> Optional[RemnavaveClient]:
    try:
        cfg = AppSettings(**storage.load_settings(account_id)).remnawave
    except Exception:
        return None
    if not cfg.panel_url or not cfg.api_token:
        return None
    return RemnavaveClient(cfg.panel_url, cfg.api_token)


async def _tool_list_rules(account_id: str, _args: dict) -> Any:
    rules = rules_store.list_rules(account_id)
    # never leak token_ref-backed secrets — only names/triggers/enabled
    return [
        {
            "id": r.get("id"),
            "name": r.get("name"),
            "enabled": r.get("enabled"),
            "trigger": (r.get("trigger") or {}).get("type"),
            "actions": [a.get("type") for a in (r.get("actions") or [])],
        }
        for r in rules
    ]


async def _tool_list_subscriptions(account_id: str, _args: dict) -> Any:
    subs = storage.load_subscriptions(account_id)
    return [
        {"id": s.get("id"), "url": s.get("url"), "enabled": s.get("enabled")}
        for s in subs
    ]


async def _tool_node_health(account_id: str, _args: dict) -> Any:
    try:
        uptime = await metrics_store.get_uptime_30d(metrics_store.LOCAL_CHECKER_ID)
    except Exception:
        uptime = None
    return {"uptime_30d": uptime}


async def _tool_list_nodes(account_id: str, _args: dict) -> Any:
    client = _rw_client(account_id)
    if client is None:
        return {"error": "Remnawave не настроен для аккаунта"}
    nodes = await client.list_nodes()
    return [
        {
            "uuid": n.get("uuid"),
            "name": n.get("name"),
            "address": n.get("address"),
            "isConnected": n.get("isConnected"),
            "isDisabled": n.get("isDisabled"),
        }
        for n in (nodes if isinstance(nodes, list) else [])
    ]


# name → (description, json-schema, coroutine fn). All read-only.
TOOLS: dict[str, dict] = {
    "list_rules": {
        "description": "Список правил автоматизации (Ф1) аккаунта: имя, триггер, действия, вкл/выкл.",
        "schema": {"type": "object", "properties": {}},
        "fn": _tool_list_rules,
    },
    "list_subscriptions": {
        "description": "Список отслеживаемых подписок аккаунта.",
        "schema": {"type": "object", "properties": {}},
        "fn": _tool_list_subscriptions,
    },
    "node_health": {
        "description": "Сводка доступности нод за 30 дней (xray-checker uptime).",
        "schema": {"type": "object", "properties": {}},
        "fn": _tool_node_health,
    },
    "list_nodes": {
        "description": "Список нод Remnawave (uuid, имя, адрес, статус). Требует настроенного Remnawave.",
        "schema": {"type": "object", "properties": {}},
        "fn": _tool_list_nodes,
    },
}


def _tool_specs_openai() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": n,
                "description": t["description"],
                "parameters": t["schema"],
            },
        }
        for n, t in TOOLS.items()
    ]


def _tool_specs_anthropic() -> list[dict]:
    return [
        {"name": n, "description": t["description"], "input_schema": t["schema"]}
        for n, t in TOOLS.items()
    ]


async def _run_tool(name: str, args: dict, account_id: str) -> tuple[bool, Any]:
    tool = TOOLS.get(name)
    if not tool:
        return False, f"неизвестный инструмент '{name}'"
    try:
        return True, await tool["fn"](account_id, args or {})
    except Exception as exc:
        return False, redact(str(exc))


# ── provider calls (one non-streaming turn) ───────────────────
class AgentError(Exception):
    pass


# CLIProxyAPI gateway (Plan J) container names reachable only on our network.
_INTERNAL_GATEWAY_HOSTS = {"node-installer-cliproxy", "cli-proxy"}


def _check_base_url(config: AiConfig) -> None:
    """SSRF guard on the account-supplied base_url, re-run every turn (DNS
    rebinding). Exemption: an INTERNAL CLIProxyAPI gateway on our
    node-assistant-net is reached by container-name and is unroutable externally
    — trusted, same posture as xray_checker._get_json for the local checker."""
    if getattr(config, "gateway", "none") == "cliproxy" and getattr(config, "gateway_internal", False):
        host = (urlparse(config.base_url).hostname or "").lower()
        if host in _INTERNAL_GATEWAY_HOSTS:
            return
    if not net_guard.is_safe_url(config.base_url):
        raise AgentError(
            "base_url не разрешён: нужен http(s) с публичным хостом (защита от SSRF)."
        )


async def list_models(config: AiConfig, key: str) -> list[str]:
    """Fetch available model ids from a {base_url}/models endpoint. Works for any
    provider, not just the CLIProxyAPI gateway: both OpenAI-compatible endpoints
    and Anthropic expose the same `{"data":[{"id":…}]}` shape.

    Never raises — returns [] on any failure, so the UI falls back to free-text.
    """
    # Без ключа сети быть не должно: свежий аккаунт открывает вкладку настроек,
    # и запрос всё равно вернул бы 401. Ранний выход держит эндпоинт бесплатным
    # и оставляет тесты сетенезависимыми.
    if not key:
        return []
    try:
        _check_base_url(config)
    except AgentError:
        return []
    url = f"{config.base_url.rstrip('/')}/models"
    # Anthropic не понимает Bearer — у него свой заголовок и обязательная версия
    # API (та же пара, что в `_anthropic_turn`).
    headers = (
        {"x-api-key": key, "anthropic-version": "2023-06-01"}
        if config.provider == "anthropic"
        else {"Authorization": f"Bearer {key}"}
    )
    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.get(url, headers=headers)
        if r.status_code >= 400:
            return []
        data = r.json()
        items = data.get("data") if isinstance(data, dict) else None
        return [m["id"] for m in (items or []) if isinstance(m, dict) and m.get("id")]
    except Exception:
        return []


async def _provider_turn(
    config: AiConfig, key: str, messages: list[dict], with_tools: bool = True, system: str = ""
) -> dict:
    """One assistant turn. Returns {"text", "tool_calls", "raw"}. Raises AgentError
    (redacted) on provider failure. SSRF guard runs every turn via
    _check_base_url (base_url is account-supplied and fetched by the SERVER
    carrying the key)."""
    _check_base_url(config)
    if config.provider == "anthropic":
        return await _anthropic_turn(config, key, messages, with_tools, system)
    return await _openai_turn(config, key, messages, with_tools)


async def _openai_turn(
    config: AiConfig, key: str, messages: list[dict], with_tools: bool = True
) -> dict:
    url = f"{config.base_url.rstrip('/')}/chat/completions"
    body: dict = {"model": config.model, "messages": messages}
    if with_tools:
        body["tools"] = _tool_specs_openai()
        body["tool_choice"] = "auto"
    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
    except Exception as exc:
        raise AgentError(f"Провайдер недоступен: {redact(str(exc), key)}")
    if r.status_code >= 400:
        raise AgentError(_provider_error(r, key))
    # Parsing is guarded too — a 200 with a malformed/HTML body must not escape the
    # generator mid-stream (the "never raises" contract).
    try:
        data = r.json()
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        tool_calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            if not isinstance(args, dict):
                args = {}
            tool_calls.append(
                {"id": tc.get("id"), "name": fn.get("name"), "args": args}
            )
        return {"text": msg.get("content") or "", "tool_calls": tool_calls, "raw": msg}
    except Exception as exc:
        raise AgentError(
            f"Некорректный ответ провайдера: {redact(str(exc), key)[:200]}"
        )


async def _anthropic_turn(
    config: AiConfig, key: str, messages: list[dict], with_tools: bool = True, system: str = ""
) -> dict:
    url = f"{config.base_url.rstrip('/')}/messages"
    body: dict = {
        "model": config.model,
        "max_tokens": 1024,
        "system": system or _SYSTEM,  # Anthropic takes system at top level, NOT in messages
        "messages": messages,
    }
    if with_tools:
        body["tools"] = _tool_specs_anthropic()
    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.post(
                url,
                json=body,
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
            )
    except Exception as exc:
        raise AgentError(f"Провайдер недоступен: {redact(str(exc), key)}")
    if r.status_code >= 400:
        raise AgentError(_provider_error(r, key))
    try:
        data = r.json()
        text_parts, tool_calls = [], []
        for block in data.get("content") or []:
            if block.get("type") == "text":
                text_parts.append(block.get("text") or "")
            elif block.get("type") == "tool_use":
                inp = block.get("input")
                tool_calls.append(
                    {
                        "id": block.get("id"),
                        "name": block.get("name"),
                        "args": inp if isinstance(inp, dict) else {},
                    }
                )
        return {
            "text": "".join(text_parts),
            "tool_calls": tool_calls,
            "raw": data.get("content") or [],
        }
    except Exception as exc:
        raise AgentError(
            f"Некорректный ответ провайдера: {redact(str(exc), key)[:200]}"
        )


def _provider_error(r: httpx.Response, key: str) -> str:
    try:
        body = r.json()
        msg = (
            body.get("error", {}).get("message")
            if isinstance(body.get("error"), dict)
            else body.get("error")
        )
        detail = msg or json.dumps(body)
    except Exception:
        detail = r.text[:200]
    if r.status_code in (401, 403):
        return "Провайдер отклонил ключ (401/403) — проверьте API-ключ и модель."
    return f"Ошибка провайдера {r.status_code}: {redact(str(detail), key)[:300]}"


# ── message assembly (append tool results per provider) ───────
def _append_tool_results_openai(
    messages: list[dict], assistant_raw: dict, results: list[dict]
) -> None:
    messages.append(assistant_raw)  # the assistant message with tool_calls
    for res in results:
        messages.append(
            {
                "role": "tool",
                "tool_call_id": res["id"],
                "content": json.dumps(res["result"], ensure_ascii=False)[
                    :_TOOL_RESULT_CAP
                ],
            }
        )


def _append_tool_results_anthropic(
    messages: list[dict], assistant_raw: list, results: list[dict]
) -> None:
    messages.append({"role": "assistant", "content": assistant_raw})
    messages.append(
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": res["id"],
                    "content": json.dumps(res["result"], ensure_ascii=False)[
                        :_TOOL_RESULT_CAP
                    ],
                }
                for res in results
            ],
        }
    )


_SYSTEM = (
    "Ты — ассистент панели node-installer/Remnawave. Отвечай кратко по-русски. "
    "Используй инструменты только для чтения данных панели. Не выдумывай данные."
)

# Non-editable suffix appended to EVERY active preset so a foreign preset (e.g.
# the Cloudflare one) can't strip awareness of our read-only tools (Plan I).
_TOOLING_SUFFIX = (
    "У тебя есть read-only инструменты панели node-installer/Remnawave "
    "(list_rules, list_subscriptions, node_health, list_nodes) — используй их для "
    "чтения данных, не выдумывай."
)


def build_system(account_id: str, config: AiConfig) -> str:
    """The effective system prompt: the account's active preset (fallback: the
    `default` builtin) + our always-on tooling suffix."""
    text = prompt_presets_store.resolve_active_text(
        getattr(config, "active_preset_id", "") or "", account_id
    )
    return f"{text or _SYSTEM}\n\n{_TOOLING_SUFFIX}"


async def run_agent(
    prompt: str, config: AiConfig, account_id: str, key: Optional[str] = None
) -> AsyncIterator[dict]:
    """Drive the tool-calling loop, yielding events. Never raises — errors become
    an {"type":"error"} event."""
    key = key if key is not None else decrypt_key(config.api_key_enc)
    if not key:
        yield {"type": "error", "message": "API-ключ провайдера не задан."}
        return

    system = build_system(account_id, config)
    if config.provider == "anthropic":
        messages: list[dict] = [{"role": "user", "content": prompt}]
    else:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]

    steps = max(1, config.max_steps)
    for step in range(steps):
        # Reserve the LAST step for a tools-off turn so the model must synthesize a
        # final answer from what it fetched, instead of dead-ending on the budget.
        is_last = step == steps - 1
        try:
            turn = await _provider_turn(config, key, messages, with_tools=not is_last, system=system)
        except AgentError as exc:
            yield {"type": "error", "message": str(exc)}
            return

        if not turn["tool_calls"]:
            if turn["text"]:
                yield {"type": "text", "delta": turn["text"]}
            yield {"type": "done"}
            return

        # Execute each requested tool, stream call + result events (with the call
        # id so the UI can match result→call even if calls are ever parallelized).
        results = []
        for tc in turn["tool_calls"]:
            yield {
                "type": "tool_call",
                "id": tc["id"],
                "name": tc["name"],
                "args": tc["args"],
            }
            ok, out = await _run_tool(tc["name"], tc["args"], account_id)
            preview = json.dumps(out, ensure_ascii=False)
            yield {
                "type": "tool_result",
                "id": tc["id"],
                "name": tc["name"],
                "ok": ok,
                "preview": preview[:500],
            }
            results.append({"id": tc["id"], "result": out})

        if config.provider == "anthropic":
            _append_tool_results_anthropic(messages, turn["raw"], results)
        else:
            _append_tool_results_openai(messages, turn["raw"], results)

    # Defensive: the tools-off last turn should already have returned above.
    yield {"type": "text", "delta": "(достигнут лимит шагов агента)"}
    yield {"type": "done"}
