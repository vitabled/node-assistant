"""Tests for the HAPROXY (NodeFlow) config + generic proxy API (api/haproxy.py).

The real NodeFlow panel is not reached (no daemon in CI): the proxy forward is
covered by monkeypatching NodeFlowClient.request. Covered: account gating, the SSRF
guard on save, admin-token encryption-at-rest + blank-keeps-stored, not-configured
guards, and that the proxy forwards method/subpath/query and returns the upstream
status + body.
"""

import uuid

import httpx
from fastapi.testclient import TestClient

from app.services import accounts, nodeflow_client, storage
from app.main import app
import app.api.haproxy as haproxy_api

client = TestClient(app)


def _auth():
    login = f"hap-{uuid.uuid4().hex[:8]}"
    r = client.post("/api/auth/register", json={"login": login, "password": "pw-1"})
    body = r.json()
    return {"Authorization": f"Bearer {body['token']}"}, body["id"]


def test_routes_require_account():
    assert client.get("/api/haproxy/config").status_code == 401
    assert client.post("/api/haproxy/config", json={"enabled": True}).status_code == 401
    assert client.post("/api/haproxy/test").status_code == 401
    assert client.get("/api/haproxy/proxy/overview").status_code == 401


def test_default_config_is_empty():
    h, _ = _auth()
    cfg = client.get("/api/haproxy/config", headers=h).json()
    assert cfg == {"enabled": False, "base_url": "", "has_token": False, "configured": False}


def test_save_rejects_private_url():
    h, _ = _auth()
    r = client.post("/api/haproxy/config", headers=h,
                    json={"enabled": True, "base_url": "http://127.0.0.1:8080",
                          "admin_token": "secret-token"})
    assert r.status_code == 422


def test_save_encrypts_token_and_blank_keeps_it(monkeypatch):
    # Bypass the SSRF guard so a test host is accepted at save time.
    monkeypatch.setattr(haproxy_api, "is_safe_url", lambda u: True)
    h, aid = _auth()
    r = client.post("/api/haproxy/config", headers=h,
                    json={"enabled": True, "base_url": "https://haproxy.example.com/",
                          "admin_token": "super-secret-token"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["enabled"] and body["has_token"]
    assert body["base_url"] == "https://haproxy.example.com"  # trailing slash trimmed

    # Encrypted at rest: plaintext never in settings.json; ciphertext + vault round-trip.
    raw = (accounts.data_dir(aid) / "settings.json").read_text(encoding="utf-8")
    assert "super-secret-token" not in raw
    assert "admin_token_enc" in raw
    stored = storage.load_settings(aid)["haproxy"]["admin_token_enc"]
    assert nodeflow_client.decrypt(stored) == "super-secret-token"

    # Blank token on a later save keeps the stored one (edit URL without re-typing).
    r2 = client.post("/api/haproxy/config", headers=h,
                     json={"enabled": False, "base_url": "https://haproxy.example.com",
                           "admin_token": ""})
    assert r2.status_code == 200 and r2.json()["has_token"] is True
    assert storage.load_settings(aid)["haproxy"]["admin_token_enc"] == stored


def test_proxy_and_test_require_configuration():
    h, _ = _auth()
    assert client.post("/api/haproxy/test", headers=h).status_code == 400
    assert client.get("/api/haproxy/proxy/overview", headers=h).status_code == 400


def test_proxy_forwards_and_returns_upstream(monkeypatch):
    h, aid = _auth()
    # Seed a configured panel directly (bypasses the save-time SSRF guard).
    data = storage.load_settings(aid)
    data["haproxy"] = {"enabled": True, "base_url": "https://haproxy.example.com",
                       "admin_token_enc": nodeflow_client.encrypt("tok-123")}
    storage.save_settings(data, aid)

    captured = {}

    async def fake_request(self, method, subpath, *, params=None, content=None, headers=None):
        captured.update(method=method, subpath=subpath, params=params,
                        token=self.token, base=self.base)
        return httpx.Response(201, json={"echo": subpath, "ok": True})

    monkeypatch.setattr(nodeflow_client.NodeFlowClient, "request", fake_request)

    r = client.get("/api/haproxy/proxy/nodes/abc/routes?enabled=true", headers=h)
    assert r.status_code == 201
    assert r.json() == {"echo": "nodes/abc/routes", "ok": True}
    assert captured["method"] == "GET"
    assert captured["subpath"] == "nodes/abc/routes"
    assert captured["params"] == {"enabled": "true"}
    assert captured["token"] == "tok-123"  # decrypted + injected server-side
    assert captured["base"] == "https://haproxy.example.com"


def test_fernet_roundtrip_and_bad_ciphertext():
    ct = nodeflow_client.encrypt("hello")
    assert ct != "hello"
    assert nodeflow_client.decrypt(ct) == "hello"
    assert nodeflow_client.decrypt("") is None
    assert nodeflow_client.decrypt("not-valid-fernet") is None
