"""Wave-5 Plan D — user config templates CRUD + validator + kind↔enum mapper."""
import base64
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.models.config_templates import KIND_TO_ENUM, ENUM_TO_KIND

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"ct-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_crud_and_ordering():
    h = _auth()
    a = client.post("/api/config-templates",
                    json={"name": "xray one", "kind": "xray-json",
                          "content_json": {"dns": {}, "outbounds": []}}, headers=h)
    assert a.status_code == 201, a.text
    b = client.post("/api/config-templates",
                    json={"name": "clash one", "kind": "clash",
                          "content_yaml": "proxies: []\n"}, headers=h)
    assert b.status_code == 201
    lst = client.get("/api/config-templates", headers=h).json()
    assert [t["name"] for t in lst] == ["xray one", "clash one"]  # view_position order
    tid = a.json()["id"]
    up = client.put(f"/api/config-templates/{tid}",
                    json={"name": "xray renamed", "kind": "xray-json",
                          "content_json": {"dns": {"servers": ["1.1.1.1"]}}}, headers=h)
    assert up.status_code == 200 and up.json()["name"] == "xray renamed"
    assert up.json()["id"] == tid  # id + position preserved
    assert client.delete(f"/api/config-templates/{b.json()['id']}", headers=h).status_code == 204
    assert len(client.get("/api/config-templates", headers=h).json()) == 1


def test_validator_json_vs_yaml_and_name():
    h = _auth()
    # JSON core given YAML content → 422
    r = client.post("/api/config-templates",
                    json={"name": "bad", "kind": "xray-json", "content_yaml": "x: 1"}, headers=h)
    assert r.status_code == 422
    # YAML core given JSON content → 422
    r = client.post("/api/config-templates",
                    json={"name": "bad", "kind": "mihomo", "content_json": {"a": 1}}, headers=h)
    assert r.status_code == 422
    # both set → 422
    r = client.post("/api/config-templates",
                    json={"name": "bad", "kind": "xray-json",
                          "content_json": {}, "content_yaml": "x"}, headers=h)
    assert r.status_code == 422
    # empty name → 422
    r = client.post("/api/config-templates",
                    json={"name": "   ", "kind": "singbox"}, headers=h)
    assert r.status_code == 422


def test_isolation_and_export_gate():
    a = _auth()
    client.post("/api/config-templates",
                json={"name": "mine", "kind": "xray-json", "content_json": {}}, headers=a)
    b = _auth()
    assert client.get("/api/config-templates", headers=b).json() == []
    # export without a configured Remnawave panel → 400
    tid = client.get("/api/config-templates", headers=a).json()[0]["id"]
    r = client.post(f"/api/config-templates/{tid}/export", headers=a)
    assert r.status_code == 400
    assert client.get("/api/config-templates/import/panel", headers=a).status_code == 400


def test_kind_enum_mapper_and_base64_roundtrip():
    assert KIND_TO_ENUM["mihomo"] == "MIHOMO"
    assert ENUM_TO_KIND["XRAY_JSON"] == "xray-json"
    assert set(KIND_TO_ENUM) == {"xray-json", "xray-base64", "mihomo", "stash", "clash", "singbox"}
    yaml = "proxies:\n  - name: a\n"
    enc = base64.b64encode(yaml.encode()).decode()
    assert base64.b64decode(enc).decode() == yaml


def test_import_from_panel_at_cap_is_409_not_500(monkeypatch):
    """The template-cap ValueError must map to 409 in import, just as it does in
    create — not surface as an unhandled 500. (Wave-7 review, config_templates:125.)"""
    from app.api import config_templates as ct
    from app.services import config_templates_store as store

    h = _auth()
    # a configured panel so _client() passes
    client.post("/api/settings/remnawave/panels", headers=h,
                json={"name": "P", "panel_url": "https://p", "api_token": "t"})

    class FakeClient:
        async def get_subscription_template(self, uuid):
            return {"name": "x", "templateType": "XRAY_JSON", "templateJson": {}}

    monkeypatch.setattr(ct, "_client", lambda pid="": FakeClient())
    monkeypatch.setattr(store, "add_template",
                        lambda body: (_ for _ in ()).throw(ValueError("Достигнут лимит")))

    r = client.post("/api/config-templates/import/panel/some-uuid", headers=h)
    assert r.status_code == 409
