"""Tests for the HAPROXY (NodeFlow) config + generic proxy API (api/haproxy.py).

The real NodeFlow panel/containers are not reached (proxy forward is monkeypatched;
local deploy stops at the images-not-built guard). Covered: account gating, the
default `local` mode, remote SSRF guard on save, admin-token encryption-at-rest +
blank-keeps-stored, not-configured guards (remote 400 / local 409), the generic
proxy forwarding for remote, and the local-deploy images guard.
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
    assert client.post("/api/haproxy/deploy", json={}).status_code == 401


def test_default_config_is_local():
    h, _ = _auth()
    cfg = client.get("/api/haproxy/config", headers=h).json()
    assert cfg["mode"] == "local"
    assert cfg["enabled"] is False
    assert cfg["configured"] is False  # no local panel deployed yet
    assert "local" in cfg  # local status block present in local mode


def test_save_remote_rejects_private_url():
    h, _ = _auth()
    r = client.post("/api/haproxy/config", headers=h,
                    json={"enabled": True, "mode": "remote", "base_url": "http://127.0.0.1:8080",
                          "admin_token": "secret-token"})
    assert r.status_code == 422


def test_save_remote_encrypts_token_and_blank_keeps_it(monkeypatch):
    monkeypatch.setattr(haproxy_api, "is_safe_url", lambda u: True)
    h, aid = _auth()
    r = client.post("/api/haproxy/config", headers=h,
                    json={"enabled": True, "mode": "remote", "base_url": "https://haproxy.example.com/",
                          "admin_token": "super-secret-token"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["mode"] == "remote" and body["has_token"]
    assert body["base_url"] == "https://haproxy.example.com"

    raw = (accounts.data_dir(aid) / "settings.json").read_text(encoding="utf-8")
    assert "super-secret-token" not in raw and "admin_token_enc" in raw
    stored = storage.load_settings(aid)["haproxy"]["admin_token_enc"]
    assert nodeflow_client.decrypt(stored) == "super-secret-token"

    r2 = client.post("/api/haproxy/config", headers=h,
                     json={"enabled": False, "mode": "remote", "base_url": "https://haproxy.example.com",
                           "admin_token": ""})
    assert r2.status_code == 200 and r2.json()["has_token"] is True
    assert storage.load_settings(aid)["haproxy"]["admin_token_enc"] == stored


def test_remote_proxy_and_test_require_configuration():
    h, _ = _auth()
    client.post("/api/haproxy/config", headers=h, json={"mode": "remote", "enabled": True})
    assert client.post("/api/haproxy/test", headers=h).status_code == 400
    assert client.get("/api/haproxy/proxy/overview", headers=h).status_code == 400


def test_local_proxy_requires_deploy():
    h, _ = _auth()  # default local, nothing deployed → 409
    assert client.get("/api/haproxy/proxy/overview", headers=h).status_code == 409
    assert client.post("/api/haproxy/test", headers=h).status_code == 409


def test_proxy_forwards_remote(monkeypatch):
    h, aid = _auth()
    data = storage.load_settings(aid)
    data["haproxy"] = {"enabled": True, "mode": "remote", "base_url": "https://haproxy.example.com",
                       "admin_token_enc": nodeflow_client.encrypt("tok-123")}
    storage.save_settings(data, aid)

    captured = {}

    async def fake_request(self, method, subpath, *, params=None, content=None, headers=None):
        captured.update(method=method, subpath=subpath, params=params,
                        token=self.token, base=self.base, allow_internal=self.allow_internal)
        return httpx.Response(201, json={"echo": subpath, "ok": True})

    monkeypatch.setattr(nodeflow_client.NodeFlowClient, "request", fake_request)

    r = client.get("/api/haproxy/proxy/nodes/abc/routes?enabled=true", headers=h)
    assert r.status_code == 201
    assert r.json() == {"echo": "nodes/abc/routes", "ok": True}
    assert captured["method"] == "GET"
    assert captured["subpath"] == "nodes/abc/routes"
    assert captured["params"] == {"enabled": "true"}
    assert captured["token"] == "tok-123"
    assert captured["allow_internal"] is False  # remote target is SSRF-guarded


def test_deploy_reports_images_not_built():
    h, _ = _auth()
    r = client.post("/api/haproxy/deploy", headers=h, json={})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    # No NodeFlow images are built in this environment → guarded before any bring-up.
    assert body["started"] is False
    assert "warning" in body
    # switched the account into local+enabled
    cfg = client.get("/api/haproxy/config", headers=h).json()
    assert cfg["mode"] == "local" and cfg["enabled"] is True


def test_fernet_roundtrip_and_bad_ciphertext():
    ct = nodeflow_client.encrypt("hello")
    assert ct != "hello"
    assert nodeflow_client.decrypt(ct) == "hello"
    assert nodeflow_client.decrypt("") is None
    assert nodeflow_client.decrypt("not-valid-fernet") is None


def test_internal_client_skips_ssrf_guard():
    # The local shared panel is reached by container name (not a public host); the
    # guard must be exempt only when allow_internal is set.
    c = nodeflow_client.NodeFlowClient("http://nodeflow-panel:8080", "t", allow_internal=True)
    c._guard()  # must not raise
    bad = nodeflow_client.NodeFlowClient("http://nodeflow-panel:8080", "t")
    try:
        bad._guard()
        raised = False
    except nodeflow_client.NodeFlowError:
        raised = True
    assert raised
