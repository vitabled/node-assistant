"""Wave-5 Plan J — CLIProxyAPI gateway mode: SSRF exemption for the internal
gateway container, graceful list_models, config wiring."""
import asyncio
import uuid

import pytest

from fastapi.testclient import TestClient

from app.main import app
from app.models.settings import AiConfig
from app.services import ai_agent

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register", json={"login": f"g-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_internal_gateway_exempt_from_ssrf():
    cfg = AiConfig(gateway="cliproxy", gateway_internal=True,
                   base_url="http://node-installer-cliproxy:8317/v1")
    ai_agent._check_base_url(cfg)  # exempt → does not raise


def test_external_private_gateway_still_blocked():
    # gateway=cliproxy but NOT internal, private host → SSRF guard blocks
    cfg = AiConfig(gateway="cliproxy", gateway_internal=False, base_url="http://127.0.0.1:8317/v1")
    with pytest.raises(ai_agent.AgentError):
        ai_agent._check_base_url(cfg)
    # internal flag but non-container host → still checked (private → blocked)
    cfg2 = AiConfig(gateway="cliproxy", gateway_internal=True, base_url="http://127.0.0.1:8317/v1")
    with pytest.raises(ai_agent.AgentError):
        ai_agent._check_base_url(cfg2)


def test_list_models_graceful_on_blocked():
    cfg = AiConfig(gateway="cliproxy", gateway_internal=False, base_url="http://127.0.0.1/v1")
    assert asyncio.run(ai_agent.list_models(cfg, "k")) == []


def test_config_gateway_roundtrip_and_validation():
    h = _auth()
    # default → none, models endpoint empty
    assert client.get("/api/ai/config", headers=h).json()["gateway"] == "none"
    assert client.get("/api/ai/models", headers=h).json() == {"models": []}
    # set cliproxy
    r = client.post("/api/ai/config", headers=h, json={"enabled": True, "provider": "openai", "gateway": "cliproxy"})
    assert r.status_code == 200 and r.json()["gateway"] == "cliproxy"
    # bad gateway → 422
    assert client.post("/api/ai/config", headers=h, json={"gateway": "bogus"}).status_code == 422


# ── Волна 6, План C Ф2: каталог моделей разгейчен ──

def test_list_models_makes_no_network_call_without_a_key(monkeypatch):
    """Свежий аккаунт открывает вкладку настроек — запрос без ключа всё равно
    вернул бы 401, поэтому сети быть не должно вовсе."""
    def boom(*a, **k):
        raise AssertionError("сеть не должна трогаться без ключа")
    monkeypatch.setattr(ai_agent.httpx, "AsyncClient", boom)
    cfg = AiConfig(base_url="https://api.openai.com/v1")
    assert asyncio.run(ai_agent.list_models(cfg, "")) == []


class _FakeResp:
    status_code = 200
    def json(self):
        return {"data": [{"id": "claude-opus-4-8"}, {"id": "claude-haiku-4-5"}]}


class _RecordingClient:
    """Перехватывает заголовки одного GET, не выходя в сеть."""
    seen: dict = {}

    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def get(self, url, headers=None):
        _RecordingClient.seen = {"url": url, "headers": headers or {}}
        return _FakeResp()


def test_list_models_uses_anthropic_headers(monkeypatch):
    """Anthropic не понимает Bearer: нужны x-api-key + обязательная версия API."""
    monkeypatch.setattr(ai_agent.httpx, "AsyncClient", _RecordingClient)
    cfg = AiConfig(provider="anthropic", base_url="https://api.anthropic.com/v1")
    out = asyncio.run(ai_agent.list_models(cfg, "sk-ant"))
    assert out == ["claude-opus-4-8", "claude-haiku-4-5"]
    h = _RecordingClient.seen["headers"]
    assert h["x-api-key"] == "sk-ant"
    assert h["anthropic-version"] == "2023-06-01"
    assert "Authorization" not in h
    assert _RecordingClient.seen["url"] == "https://api.anthropic.com/v1/models"


def test_list_models_uses_bearer_for_openai_compatible(monkeypatch):
    monkeypatch.setattr(ai_agent.httpx, "AsyncClient", _RecordingClient)
    cfg = AiConfig(provider="openai", base_url="https://api.openai.com/v1")
    asyncio.run(ai_agent.list_models(cfg, "sk-oai"))
    h = _RecordingClient.seen["headers"]
    assert h["Authorization"] == "Bearer sk-oai"
    assert "x-api-key" not in h


def test_models_endpoint_no_longer_gated_on_gateway(monkeypatch):
    """Гейт `gateway != cliproxy → []` снят: прямой провайдер тоже отдаёт каталог."""
    monkeypatch.setattr(ai_agent.httpx, "AsyncClient", _RecordingClient)
    h = _auth()
    client.post("/api/ai/config", headers=h,
                json={"enabled": True, "provider": "openai", "gateway": "none",
                      "base_url": "https://api.openai.com/v1", "api_key": "sk-direct"})
    assert client.get("/api/ai/models", headers=h).json() == {
        "models": ["claude-opus-4-8", "claude-haiku-4-5"]
    }
