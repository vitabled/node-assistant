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
