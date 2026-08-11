"""Wave-4 PR-7 — прокси шаблонов XRAY_JSON для раздела «Авто»."""
import uuid as _uuid

from fastapi.testclient import TestClient

from app.main import app
import app.api.xray_templates as xt

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"at-{_uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


class FakeClient:
    def __init__(self):
        self.store = {}

    async def list_subscription_templates(self):
        return [{"uuid": "t1", "name": "Main", "templateType": "XRAY_JSON"},
                {"uuid": "t2", "name": "Clash", "templateType": "MIHOMO"}]

    async def get_subscription_template(self, uuid):
        return {"uuid": uuid, "name": "Main", "templateJson": {"routing": {"rules": []}}}

    async def create_subscription_template(self, name, template_type):
        assert template_type == "XRAY_JSON"
        t = {"uuid": "t-new", "name": name}
        self.store["t-new"] = t
        return t

    async def update_subscription_template(self, uuid, *, template_json=None, **kw):
        self.store[uuid] = {"uuid": uuid, "templateJson": template_json}
        return self.store[uuid]


def _patch(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(xt.panel_registry, "client_for", lambda panel_id="": fake)
    return fake


def test_list_only_xray_json(monkeypatch):
    _patch(monkeypatch)
    r = client.get("/api/xray-templates", headers=_auth())
    assert r.status_code == 200
    tpls = r.json()["templates"]
    assert [t["name"] for t in tpls] == ["Main"]       # MIHOMO отфильтрован


def test_get_with_content(monkeypatch):
    _patch(monkeypatch)
    r = client.get("/api/xray-templates/t1", headers=_auth())
    assert r.status_code == 200
    assert r.json()["templateJson"]["routing"]["rules"] == []


def test_create_then_update(monkeypatch):
    fake = _patch(monkeypatch)
    a = _auth()
    r = client.post("/api/xray-templates", headers=a, json={"name": "Авто-шаблон"})
    assert r.status_code == 201 and r.json()["uuid"] == "t-new"

    doc = {"dns": {"servers": ["1.1.1.1"]}, "routing": {"rules": []}}
    r2 = client.put("/api/xray-templates/t-new", headers=a, json={"template_json": doc})
    assert r2.status_code == 200
    assert fake.store["t-new"]["templateJson"] == doc
