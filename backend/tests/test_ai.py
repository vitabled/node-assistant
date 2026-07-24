"""Tests for the built-in AI agent (services/ai_agent + api/ai).

The provider HTTP call is mocked (a scripted tool-call turn then a final turn),
so the tool-calling loop, streaming events, key masking and error paths are
covered without a real LLM.
"""

import asyncio
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.models.settings import AiConfig
from app.services import ai_agent, storage
from app.main import app

client = TestClient(app)


# ── fake httpx for wire-format tests ──────────────────────────
class _FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._p = payload
        self.text = json.dumps(payload) if not isinstance(payload, str) else payload

    def json(self):
        if isinstance(self._p, str):
            raise ValueError("not json")
        return self._p


class _FakeClient:
    def __init__(self, resp, sink):
        self._resp = resp
        self._sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        self._sink.update(url=url, json=json, headers=headers)
        return self._resp


def _fake_httpx(monkeypatch, resp, sink):
    monkeypatch.setattr(
        ai_agent.httpx, "AsyncClient", lambda **k: _FakeClient(resp, sink)
    )


def _auth():
    login = f"ai-{uuid.uuid4().hex[:8]}"
    r = client.post("/api/auth/register", json={"login": login, "password": "pw-1"})
    body = r.json()
    return {"Authorization": f"Bearer {body['token']}"}, body["id"]


def _configure(h, **over):
    body = {
        "enabled": True,
        "provider": "openai",
        "base_url": "https://mock.example/v1",
        "model": "gpt-x",
        "api_key": "sk-secret-key-123456789",
        "max_steps": 4,
    }
    body.update(over)
    return client.post("/api/ai/config", headers=h, json=body)


# ── config: key masking + persistence ─────────────────────────
def test_config_masks_key_and_persists_encrypted():
    h, aid = _auth()
    r = _configure(h)
    assert r.status_code == 200
    pub = r.json()
    assert pub["has_key"] is True
    assert "api_key" not in pub and "api_key_enc" not in pub  # never returned

    raw = storage.load_settings(aid)
    assert "sk-secret-key-123456789" not in json.dumps(raw)  # encrypted at rest
    assert ai_agent.decrypt_key(raw["ai"]["api_key_enc"]) == "sk-secret-key-123456789"


def test_config_blank_key_keeps_existing():
    h, aid = _auth()
    _configure(h)
    # Re-save without a key → keep the vaulted one.
    client.post(
        "/api/ai/config",
        headers=h,
        json={"enabled": True, "provider": "openai", "api_key": ""},
    )
    raw = storage.load_settings(aid)
    assert ai_agent.decrypt_key(raw["ai"]["api_key_enc"]) == "sk-secret-key-123456789"


def test_ai_routes_require_account():
    assert client.get("/api/ai/config").status_code == 401
    assert client.post("/api/ai/chat", json={"prompt": "hi"}).status_code == 401


# ── chat: tool-calling loop (mocked provider) ─────────────────
def _script_provider(monkeypatch, turns):
    """Make _provider_turn return successive scripted turns."""
    state = {"i": 0}

    # **kw so a new optional argument on _provider_turn (e.g. Wave-7's `mcp`
    # tool list) doesn't break every chat test with a TypeError.
    async def fake(config, key, messages, with_tools=True, system="", **kw):
        t = turns[min(state["i"], len(turns) - 1)]
        state["i"] += 1
        return t

    monkeypatch.setattr(ai_agent, "_provider_turn", fake)


def _stream(h, prompt="привет"):
    r = client.post("/api/ai/chat", headers=h, json={"prompt": prompt})
    assert r.status_code == 200
    return [json.loads(ln) for ln in r.text.splitlines() if ln.strip()]


def test_chat_executes_tool_then_returns_final(monkeypatch):
    h, _ = _auth()
    _configure(h)
    _script_provider(
        monkeypatch,
        [
            {
                "text": "",
                "tool_calls": [{"id": "t1", "name": "list_rules", "args": {}}],
                "raw": {"role": "assistant"},
            },
            {"text": "У вас 0 правил.", "tool_calls": [], "raw": {"role": "assistant"}},
        ],
    )
    events = _stream(h)
    types = [e["type"] for e in events]
    assert "tool_call" in types
    assert any(e["type"] == "tool_call" and e["name"] == "list_rules" for e in events)
    assert any(
        e["type"] == "tool_result" and e["name"] == "list_rules" and e["ok"]
        for e in events
    )
    assert any(e["type"] == "text" and "0 правил" in e["delta"] for e in events)
    assert types[-1] == "done"


def test_chat_no_tool_just_answers(monkeypatch):
    h, _ = _auth()
    _configure(h)
    _script_provider(monkeypatch, [{"text": "Привет!", "tool_calls": [], "raw": {}}])
    events = _stream(h)
    assert any(e["type"] == "text" and e["delta"] == "Привет!" for e in events)
    assert events[-1]["type"] == "done"


def test_chat_provider_error_surfaces_cleanly(monkeypatch):
    h, _ = _auth()
    _configure(h)

    async def boom(config, key, messages, with_tools=True, system="", **kw):
        raise ai_agent.AgentError(
            "Провайдер отклонил ключ (401/403) — проверьте API-ключ и модель."
        )

    monkeypatch.setattr(ai_agent, "_provider_turn", boom)
    events = _stream(h)
    assert events[-1]["type"] == "error"
    assert "ключ" in events[-1]["message"]


def test_chat_disabled_streams_error():
    h, _ = _auth()
    _configure(h, enabled=False)
    events = _stream(h)
    assert events[0]["type"] == "error"
    assert "выключен" in events[0]["message"]


def test_chat_missing_key_streams_error(monkeypatch):
    h, aid = _auth()
    # enabled but never set a key
    client.post(
        "/api/ai/config", headers=h, json={"enabled": True, "provider": "openai"}
    )
    events = _stream(h)
    assert events[0]["type"] == "error"
    assert "ключ" in events[0]["message"].lower()


def test_step_limit_terminates(monkeypatch):
    h, _ = _auth()
    _configure(h, max_steps=2)
    # Provider ALWAYS asks for a tool → loop must stop at max_steps, not spin.
    _script_provider(
        monkeypatch,
        [
            {
                "text": "",
                "tool_calls": [{"id": "t", "name": "list_rules", "args": {}}],
                "raw": {"role": "assistant"},
            }
        ],
    )
    events = _stream(h)
    # 2 steps → 2 tool_calls max, then a synthetic final text + done.
    assert sum(1 for e in events if e["type"] == "tool_call") <= 2
    assert events[-1]["type"] == "done"


def test_unknown_provider_rejected():
    h, _ = _auth()
    r = client.post(
        "/api/ai/config", headers=h, json={"enabled": True, "provider": "bogus"}
    )
    assert r.status_code == 422


def test_chat_unknown_tool_returns_error_result(monkeypatch):
    h, _ = _auth()
    _configure(h)
    _script_provider(
        monkeypatch,
        [
            {
                "text": "",
                "tool_calls": [{"id": "t1", "name": "nope", "args": {}}],
                "raw": {"role": "assistant"},
            },
            {"text": "ок", "tool_calls": [], "raw": {"role": "assistant"}},
        ],
    )
    events = _stream(h)
    assert any(
        e["type"] == "tool_result" and e["name"] == "nope" and e["ok"] is False
        for e in events
    )


# ── SSRF guard on base_url (fetch-time) ───────────────────────
def test_provider_turn_blocks_internal_base_url():
    cfg = AiConfig(enabled=True, provider="openai", base_url="http://169.254.169.254")
    with pytest.raises(ai_agent.AgentError) as ei:
        asyncio.run(ai_agent._provider_turn(cfg, "k", []))
    assert "SSRF" in str(ei.value)


def test_provider_turn_blocks_loopback_base_url():
    cfg = AiConfig(enabled=True, provider="anthropic", base_url="http://127.0.0.1:8000")
    with pytest.raises(ai_agent.AgentError):
        asyncio.run(ai_agent._provider_turn(cfg, "k", []))


# ── real wire-format (fake httpx) ─────────────────────────────
def test_openai_turn_parses_tool_calls(monkeypatch):
    sink: dict = {}
    resp = _FakeResp(
        200,
        {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "c1",
                                "function": {"name": "list_rules", "arguments": "{}"},
                            }
                        ],
                    }
                }
            ]
        },
    )
    _fake_httpx(monkeypatch, resp, sink)
    cfg = AiConfig(provider="openai", base_url="https://api.openai.com/v1", model="m")
    out = asyncio.run(
        ai_agent._openai_turn(cfg, "sk-x", [{"role": "user", "content": "hi"}])
    )
    assert out["tool_calls"][0]["name"] == "list_rules"
    assert "tools" in sink["json"]  # tools offered by default


def test_anthropic_turn_sends_system_and_parses(monkeypatch):
    sink: dict = {}
    resp = _FakeResp(
        200,
        {
            "content": [
                {"type": "text", "text": "привет"},
                {"type": "tool_use", "id": "u1", "name": "list_nodes", "input": {}},
            ]
        },
    )
    _fake_httpx(monkeypatch, resp, sink)
    cfg = AiConfig(
        provider="anthropic", base_url="https://api.anthropic.com/v1", model="claude"
    )
    out = asyncio.run(
        ai_agent._anthropic_turn(cfg, "k", [{"role": "user", "content": "hi"}])
    )
    # System instruction goes at the TOP LEVEL for Anthropic (not in messages).
    assert sink["json"]["system"] == ai_agent._SYSTEM
    assert out["text"] == "привет"
    assert out["tool_calls"][0]["name"] == "list_nodes"


def test_openai_turn_without_tools_omits_tools(monkeypatch):
    sink: dict = {}
    _fake_httpx(
        monkeypatch,
        _FakeResp(200, {"choices": [{"message": {"content": "final"}}]}),
        sink,
    )
    cfg = AiConfig(provider="openai", base_url="https://api.openai.com/v1", model="m")
    out = asyncio.run(ai_agent._openai_turn(cfg, "k", [], with_tools=False))
    assert "tools" not in sink["json"]  # last-step tools-off turn
    assert out["text"] == "final"


def test_malformed_provider_body_raises_agent_error(monkeypatch):
    sink: dict = {}
    _fake_httpx(monkeypatch, _FakeResp(200, ["garbage-not-an-object"]), sink)
    cfg = AiConfig(provider="openai", base_url="https://api.openai.com/v1", model="m")
    with pytest.raises(ai_agent.AgentError):
        asyncio.run(ai_agent._openai_turn(cfg, "k", []))


# ── redaction ─────────────────────────────────────────────────
def test_redact_masks_api_key():
    assert "sk-abcdef123456" not in ai_agent.redact("boom sk-abcdef123456 oops")
    assert "supersecret" not in ai_agent.redact("x supersecret y", "supersecret")
