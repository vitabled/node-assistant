"""Write-endpoints for node management (/api/node-ops/*).

Covers: auth gating, input validation (ip/domain/port/uuid), add-node (incl.
default config-profile resolution and status application), deploy (task
response only — no secrets echoed), patch (field passthrough + status) and
delete (confirm/soft). The Remnawave panel is faked; nothing here touches a
real panel or SSH.

`asyncssh` is stubbed (as in test_pipeline_scripts) so the SSH stack imports
without native deps.
"""
import sys
import types
import uuid

import pytest
from fastapi.testclient import TestClient

sys.modules.setdefault("asyncssh", types.ModuleType("asyncssh"))

import app.api.node_admin as node_admin  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)

NODE_UUID = "11111111-2222-4333-8444-555555555555"
PROFILE_UUID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
INBOUND_UUID = "ffffffff-1111-4222-8333-444444444444"

_PANEL_SETTINGS = {
    "remnawave_registry": {
        "panels": [{
            "id": "p1", "name": "Основная", "kind": "custom",
            "panel_url": "https://panel.example.com", "api_token": "panel-token",
        }],
        "active_panel_id": "p1",
    }
}


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"na-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _panel(monkeypatch):
    """Point _resolve_panel at a fake stored panel + fake RemnavaveClient."""
    monkeypatch.setattr(node_admin.storage, "load_settings", lambda: _PANEL_SETTINGS)

    class FakeRemnavaveClient:
        calls: list = []

        def __init__(self, base_url, token):
            self.base_url, self.token = base_url, token
            FakeRemnavaveClient.calls = []

        async def list_config_profiles(self):
            return [{
                "uuid": PROFILE_UUID,
                "inbounds": [{"uuid": INBOUND_UUID, "tag": "in-1"}],
            }]

        async def list_nodes(self):
            return [{
                "uuid": NODE_UUID, "name": "node-1", "address": "1.2.3.4",
                "configProfile": {"activeConfigProfileUuid": PROFILE_UUID,
                                  "activeInbounds": [INBOUND_UUID]},
            }]

        async def create_node(self, **kw):
            FakeRemnavaveClient.calls.append(("create_node", kw))
            return {"uuid": NODE_UUID, "name": kw["name"], "address": kw["address"],
                    "port": kw["port"]}

        async def update_node(self, node_uuid, body):
            FakeRemnavaveClient.calls.append(("update_node", node_uuid, body))
            return {"uuid": node_uuid, **body}

        async def delete_node(self, node_uuid):
            FakeRemnavaveClient.calls.append(("delete_node", node_uuid))
            return {"isDeleted": True}

        async def enable_node(self, node_uuid):
            FakeRemnavaveClient.calls.append(("enable_node", node_uuid))
            return {}

        async def disable_node(self, node_uuid):
            FakeRemnavaveClient.calls.append(("disable_node", node_uuid))
            return {}

    monkeypatch.setattr(node_admin, "RemnavaveClient", FakeRemnavaveClient)
    return FakeRemnavaveClient


# ── auth gating ───────────────────────────────────────────────────────────────

def test_routes_require_auth():
    assert client.post("/api/node-ops/add-node", json={}).status_code == 401
    assert client.post("/api/node-ops/deploy", json={}).status_code == 401
    assert client.patch(f"/api/node-ops/{NODE_UUID}", json={}).status_code == 401
    assert client.delete(f"/api/node-ops/{NODE_UUID}").status_code == 401


# ── validation ────────────────────────────────────────────────────────────────

def _add_body(**over):
    body = dict(name="node-1", address="1.2.3.4", port=62050)
    body.update(over)
    return body


def test_add_node_validation(monkeypatch):
    _panel(monkeypatch)
    h = _auth()
    # bad IPv4 octet
    r = client.post("/api/node-ops/add-node", headers=h, json=_add_body(address="1.2.3.999"))
    assert r.status_code == 422
    # shell-hostile "domain"
    r = client.post("/api/node-ops/add-node", headers=h, json=_add_body(address="x;rm -rf /"))
    assert r.status_code == 422
    # domain is allowed
    r = client.post("/api/node-ops/add-node", headers=h, json=_add_body(address="node1.example.com"))
    assert r.status_code in (200, 201)
    # port out of range
    r = client.post("/api/node-ops/add-node", headers=h, json=_add_body(port=70000))
    assert r.status_code == 422
    r = client.post("/api/node-ops/add-node", headers=h, json=_add_body(port=0))
    assert r.status_code == 422
    # bad uuid fields
    r = client.post("/api/node-ops/add-node", headers=h,
                    json=_add_body(config_profile_uuid="not-a-uuid"))
    assert r.status_code == 422
    r = client.post("/api/node-ops/add-node", headers=h,
                    json=_add_body(active_inbounds=["nope"]))
    assert r.status_code == 422
    # short name
    r = client.post("/api/node-ops/add-node", headers=h, json=_add_body(name="ab"))
    assert r.status_code == 422


def test_patch_validation():
    h = _auth()
    # malformed uuid in path (valid body — so the 422 comes from the uuid check)
    assert client.patch("/api/node-ops/not-a-uuid", headers=h, json={"name": "xyz"}).status_code == 422
    # empty body → nothing to update
    assert client.patch(f"/api/node-ops/{NODE_UUID}", headers=h, json={}).status_code == 400
    # bad address / port
    assert client.patch(f"/api/node-ops/{NODE_UUID}", headers=h,
                        json={"address": "1.2.3.999"}).status_code == 422
    assert client.patch(f"/api/node-ops/{NODE_UUID}", headers=h,
                        json={"port": 0}).status_code == 422
    # bad status enum
    assert client.patch(f"/api/node-ops/{NODE_UUID}", headers=h,
                        json={"status": "bogus"}).status_code == 422


def test_delete_validation():
    h = _auth()
    assert client.delete("/api/node-ops/not-a-uuid", headers=h, params={"confirm": "true"}).status_code == 422


# ── add-node ──────────────────────────────────────────────────────────────────

def test_add_node_uses_default_config_profile(monkeypatch):
    Fake = _panel(monkeypatch)
    r = client.post("/api/node-ops/add-node", headers=_auth(), json=_add_body())
    assert r.status_code == 201
    node = r.json()
    assert node["uuid"] == NODE_UUID and node["name"] == "node-1"
    _, kw = Fake.calls[0]
    assert kw["config_profile_uuid"] == PROFILE_UUID
    assert kw["active_inbounds"] == [INBOUND_UUID]
    assert kw["port"] == 62050
    # no status → no enable/disable call
    assert not [c for c in Fake.calls if c[0] in ("enable_node", "disable_node")]


def test_add_node_respects_explicit_profile_and_disabled_status(monkeypatch):
    Fake = _panel(monkeypatch)
    r = client.post("/api/node-ops/add-node", headers=_auth(), json=_add_body(
        config_profile_uuid=PROFILE_UUID, active_inbounds=[INBOUND_UUID],
        status="disabled",
    ))
    assert r.status_code == 201
    _, kw = Fake.calls[0]
    assert kw["config_profile_uuid"] == PROFILE_UUID
    assert kw["active_inbounds"] == [INBOUND_UUID]
    assert ("disable_node", NODE_UUID) in Fake.calls


def test_add_node_requires_panel(monkeypatch):
    monkeypatch.setattr(node_admin.storage, "load_settings", lambda: {})
    r = client.post("/api/node-ops/add-node", headers=_auth(), json=_add_body())
    assert r.status_code == 400
    assert "Remnawave не настроен" in r.json()["detail"]


def test_add_node_rejects_missing_profiles(monkeypatch):
    _panel(monkeypatch)

    class NoProfiles:
        def __init__(self, *a, **k):
            pass

        async def list_config_profiles(self):
            return []

    monkeypatch.setattr(node_admin, "RemnavaveClient", NoProfiles)
    r = client.post("/api/node-ops/add-node", headers=_auth(), json=_add_body())
    assert r.status_code == 400
    assert "config-профилей" in r.json()["detail"]


# ── deploy ────────────────────────────────────────────────────────────────────

def _deploy_body(**over):
    body = dict(
        ip="1.2.3.4", ssh_user="root", ssh_password="super-secret-pw",
        domain="node1.example.com", email="a@b.co", cert_provider="letsencrypt",
        remnanode_token="tok", open_ports="80,443", country_code="US",
        change_ssh_port=False,
    )
    body.update(over)
    return body


def test_deploy_returns_task_only_and_runs_pipeline(monkeypatch):
    _panel(monkeypatch)
    seen: dict = {}

    async def fake_run_pipeline(req, task):
        seen["req"] = req

    monkeypatch.setattr(node_admin, "run_pipeline", fake_run_pipeline)

    r = client.post("/api/node-ops/deploy", headers=_auth(), json=_deploy_body())
    assert r.status_code == 200
    payload = r.json()
    assert set(payload) == {"task_id", "task_type"}
    assert payload["task_type"] == "deploy"
    # the SSH password must never appear in the response
    assert "super-secret-pw" not in r.text
    # the background task actually ran with the request (incl. creds for SSH)
    assert seen["req"].ssh_password == "super-secret-pw"
    assert seen["req"].ip == "1.2.3.4"


def test_deploy_haproxy_mode(monkeypatch):
    _panel(monkeypatch)
    seen: dict = {}

    async def fake_run_pipeline(req, task):
        seen["req"] = req

    monkeypatch.setattr(node_admin, "run_pipeline", fake_run_pipeline)
    r = client.post("/api/node-ops/deploy", headers=_auth(), json=_deploy_body(
        mode="haproxy", haproxy_source_port=443, haproxy_dest_ip="5.6.7.8",
        haproxy_dest_port=8443,
    ))
    assert r.status_code == 200
    assert seen["req"].mode == "haproxy"


def test_deploy_rejects_bad_input():
    h = _auth()
    # invalid ip
    r = client.post("/api/node-ops/deploy", headers=h, json=_deploy_body(ip="999.1.1.1"))
    assert r.status_code == 422
    # remnanode mode without domain → model validator 422
    r = client.post("/api/node-ops/deploy", headers=h, json=_deploy_body(domain=""))
    assert r.status_code == 422


# ── patch ─────────────────────────────────────────────────────────────────────

def test_patch_sends_only_provided_fields(monkeypatch):
    Fake = _panel(monkeypatch)
    r = client.patch(f"/api/node-ops/{NODE_UUID}", headers=_auth(), json={
        "name": "renamed", "port": 62051,
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True
    kind, uuid_, body = Fake.calls[0]
    assert kind == "update_node" and uuid_ == NODE_UUID
    assert body == {"name": "renamed", "port": 62051}


def test_patch_status_disables_node(monkeypatch):
    Fake = _panel(monkeypatch)
    r = client.patch(f"/api/node-ops/{NODE_UUID}", headers=_auth(), json={"status": "disabled"})
    assert r.status_code == 200
    assert ("disable_node", NODE_UUID) in Fake.calls


def test_patch_status_active_enables_node(monkeypatch):
    Fake = _panel(monkeypatch)
    r = client.patch(f"/api/node-ops/{NODE_UUID}", headers=_auth(), json={"status": "active"})
    assert r.status_code == 200
    assert ("enable_node", NODE_UUID) in Fake.calls


def test_patch_config_profile_only(monkeypatch):
    Fake = _panel(monkeypatch)
    # only inbounds → current profile is preserved from list_nodes
    r = client.patch(f"/api/node-ops/{NODE_UUID}", headers=_auth(),
                     json={"active_inbounds": [INBOUND_UUID]})
    assert r.status_code == 200
    _, _, body = Fake.calls[0]
    assert body["configProfile"] == {
        "activeConfigProfileUuid": PROFILE_UUID,
        "activeInbounds": [INBOUND_UUID],
    }


def test_patch_404_when_panel_has_no_such_node(monkeypatch):
    _panel(monkeypatch)

    class Missing:
        def __init__(self, *a, **k):
            pass

        async def update_node(self, node_uuid, body):
            from app.services.remnawave_client import RemnavaveError
            raise RemnavaveError(404, "Node not found")

    monkeypatch.setattr(node_admin, "RemnavaveClient", Missing)
    r = client.patch(f"/api/node-ops/{NODE_UUID}", headers=_auth(), json={"name": "xyz"})
    assert r.status_code == 404


# ── delete ────────────────────────────────────────────────────────────────────

def test_delete_requires_confirmation(monkeypatch):
    _panel(monkeypatch)
    r = client.delete(f"/api/node-ops/{NODE_UUID}", headers=_auth())
    assert r.status_code == 400
    assert "confirm" in r.json()["detail"]
    r = client.delete(f"/api/node-ops/{NODE_UUID}", headers=_auth(), params={"confirm": "false"})
    assert r.status_code == 400


def test_delete_hard(monkeypatch):
    Fake = _panel(monkeypatch)
    r = client.delete(f"/api/node-ops/{NODE_UUID}", headers=_auth(), params={"confirm": "true"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert ("delete_node", NODE_UUID) in Fake.calls


def test_delete_soft_disables(monkeypatch):
    Fake = _panel(monkeypatch)
    r = client.delete(f"/api/node-ops/{NODE_UUID}", headers=_auth(),
                      params={"confirm": "true", "soft": "true"})
    assert r.status_code == 200
    assert r.json()["soft"] is True
    assert ("disable_node", NODE_UUID) in Fake.calls
    assert not [c for c in Fake.calls if c[0] == "delete_node"]


def test_delete_panel_404(monkeypatch):
    _panel(monkeypatch)

    class Missing:
        def __init__(self, *a, **k):
            pass

        async def delete_node(self, node_uuid):
            from app.services.remnawave_client import RemnavaveError
            raise RemnavaveError(404, "Node not found")

    monkeypatch.setattr(node_admin, "RemnavaveClient", Missing)
    r = client.delete(f"/api/node-ops/{NODE_UUID}", headers=_auth(), params={"confirm": "true"})
    assert r.status_code == 404
