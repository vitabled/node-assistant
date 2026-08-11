"""Wave-4 PR-9 — API «Обходы БС»: список хостов, применение CDN-домена."""
import asyncio
import uuid as _uuid

from fastapi.testclient import TestClient

from app.main import app
import app.api.obhod as ob

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"ob-{_uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


class FakeClient:
    def __init__(self):
        self.patched = []

    async def list_hosts(self):
        return [{"uuid": "h1", "remark": "DE-1", "address": "de.example.com", "port": 443,
                 "sni": "old.example.com", "host": "old.example.com", "isDisabled": False},
                {"uuid": "h2", "remark": "NL-1", "address": "nl.example.com", "port": 443}]

    async def update_host(self, uuid, body):
        self.patched.append((uuid, body))
        return {"uuid": uuid, **body}


def _patch(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(ob.panel_registry, "client_for", lambda panel_id="": fake)
    return fake


def test_list_hosts_for_picker(monkeypatch):
    _patch(monkeypatch)
    r = client.get("/api/obhod/hosts", headers=_auth())
    assert r.status_code == 200
    hosts = r.json()["hosts"]
    assert hosts[0]["remark"] == "DE-1"
    assert hosts[0]["sni"] == "old.example.com"
    assert hosts[1]["sni"] is None


def test_beeline_apply_patches_sni_and_host(monkeypatch):
    fake = _patch(monkeypatch)
    r = client.post("/api/obhod/beeline/apply", headers=_auth(), json={
        "host_uuids": ["h1", "h2"], "domain": "cdn123.b-cdn.net",
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["applied"] == ["h1", "h2"]
    assert fake.patched == [
        ("h1", {"sni": "cdn123.b-cdn.net", "host": "cdn123.b-cdn.net"}),
        ("h2", {"sni": "cdn123.b-cdn.net", "host": "cdn123.b-cdn.net"}),
    ]


def test_beeline_apply_validates_domain():
    r = client.post("/api/obhod/beeline/apply", headers=_auth(), json={
        "host_uuids": ["h1"], "domain": "not a domain!!",
    })
    assert r.status_code == 422


def test_beeline_apply_requires_hosts():
    r = client.post("/api/obhod/beeline/apply", headers=_auth(), json={
        "host_uuids": [], "domain": "cdn.example.ru",
    })
    assert r.status_code == 422


def test_update_host_sends_patch_with_uuid():
    """Метод клиента: PATCH /api/hosts с uuid и только изменяемыми полями."""
    from app.services.remnawave_client import RemnavaveClient
    seen = {}

    async def fake_req(self, method, path, **kwargs):
        seen.update(method=method, path=path, kwargs=kwargs)
        return {"response": {"uuid": "h1"}}

    monkeypatch = __import__("pytest").MonkeyPatch()
    monkeypatch.setattr(RemnavaveClient, "_req", fake_req)
    c = RemnavaveClient("http://panel", "tok")
    asyncio.run(c.update_host("h1", {"sni": "cdn.example.ru"}))
    assert seen["method"] == "PATCH" and seen["path"] == "/api/hosts"
    assert seen["kwargs"]["json"] == {"uuid": "h1", "sni": "cdn.example.ru"}
    monkeypatch.undo()
