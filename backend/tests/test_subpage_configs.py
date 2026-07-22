"""Wave-7 Plan G Ф2 — subscription-page-configs proxy.

The point of these tests is the SHAPE of what we send: the panel's page-config
endpoints differ from the neighbouring subscription-templates in three ways that
a copy-paste would get wrong (name window 2..30, update is PATCH on the
collection, reorder takes `items` not `uuids`).
"""
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.remnawave_client import RemnavaveClient

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"spc-{_uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _panel(headers, url="https://panel.example"):
    return client.post("/api/settings/remnawave/panels", headers=headers,
                       json={"name": "P", "panel_url": url, "api_token": "t"}).json()


@pytest.fixture
def calls(monkeypatch):
    """Capture every (method, path, json) the client would send."""
    seen: list[tuple] = []

    async def fake_req(self, method, path, **kw):
        seen.append((method, path, kw.get("json")))
        if path.endswith("/actions/reorder") or (method == "GET" and path.count("/") == 2):
            return {"response": {"total": 0, "configs": []}}
        if method == "DELETE":
            return {"response": {"isDeleted": True}}
        return {"response": {"uuid": "u1", "viewPosition": 0, "name": "n", "config": None}}

    monkeypatch.setattr(RemnavaveClient, "_req", fake_req, raising=True)
    return seen


# ── gating ────────────────────────────────────────────────────
def test_requires_auth():
    assert client.get("/api/subpage-configs").status_code == 401


def test_no_panel_configured_is_400():
    h = _auth()
    assert client.get("/api/subpage-configs", headers=h).status_code == 400


def test_unknown_panel_id_is_404(calls):
    h = _auth()
    _panel(h)
    assert client.get("/api/subpage-configs?panel_id=nope", headers=h).status_code == 404


def test_listing_echoes_the_panel(calls):
    h = _auth()
    p = _panel(h)
    r = client.get("/api/subpage-configs", headers=h)
    assert r.status_code == 200 and r.json()["panel_id"] == p["id"]


# ── request shapes ────────────────────────────────────────────
def test_create_sends_only_name(calls):
    h = _auth(); _panel(h)
    r = client.post("/api/subpage-configs", headers=h, json={"name": "Мой дизайн"})
    assert r.status_code == 201
    method, path, body = calls[-1]
    assert (method, path) == ("POST", "/api/subscription-page-configs")
    assert set(body) == {"name"}          # the panel accepts nothing else


def test_name_is_clamped_to_thirty_not_two_hundred_fifty_five(calls):
    """The templates helper slices at 255; page-config names max out at 30."""
    h = _auth(); _panel(h)
    client.post("/api/subpage-configs", headers=h, json={"name": "A" * 60})
    assert len(calls[-1][2]["name"]) == 30


def test_name_is_sanitised_to_the_panel_charset(calls):
    h = _auth(); _panel(h)
    client.post("/api/subpage-configs", headers=h, json={"name": "Мой/дизайн!"})
    name = calls[-1][2]["name"]
    assert all(c.isascii() for c in name)
    assert "/" not in name and "!" not in name


def test_short_name_is_padded_to_the_minimum(calls):
    h = _auth(); _panel(h)
    client.post("/api/subpage-configs", headers=h, json={"name": "!"})
    assert len(calls[-1][2]["name"]) >= 2


def test_update_is_patch_on_the_collection_with_uuid_in_body(calls):
    h = _auth(); _panel(h)
    r = client.put("/api/subpage-configs/u1", headers=h, json={"name": "new"})
    assert r.status_code == 200
    method, path, body = calls[-1]
    assert (method, path) == ("PATCH", "/api/subscription-page-configs")
    assert body["uuid"] == "u1"


def test_update_omits_config_when_not_supplied(calls):
    """Merge-vs-replace is unspecified, so an untouched design must not be sent
    as an explicit null."""
    h = _auth(); _panel(h)
    client.put("/api/subpage-configs/u1", headers=h, json={"name": "new"})
    assert "config" not in calls[-1][2]


def test_update_passes_config_through_untouched(calls):
    h = _auth(); _panel(h)
    payload = {"theme": "dark", "blocks": [1, 2, {"x": None}]}
    client.put("/api/subpage-configs/u1", headers=h, json={"config": payload})
    assert calls[-1][2]["config"] == payload


def test_empty_update_is_422_not_a_silent_noop(calls):
    h = _auth(); _panel(h)
    assert client.put("/api/subpage-configs/u1", headers=h, json={}).status_code == 422


def test_reorder_sends_items_not_uuids(calls):
    """Our MCP fork exposes {uuids: [...]}; the API requires
    {items:[{uuid, viewPosition}]}."""
    h = _auth(); _panel(h)
    r = client.post("/api/subpage-configs/reorder", headers=h,
                    json={"items": [{"uuid": "a", "viewPosition": 0},
                                    {"uuid": "b", "viewPosition": 1}]})
    assert r.status_code == 200
    method, path, body = calls[-1]
    assert path.endswith("/actions/reorder")
    assert "uuids" not in body
    assert body["items"] == [{"uuid": "a", "viewPosition": 0}, {"uuid": "b", "viewPosition": 1}]


def test_clone_sends_clone_from_uuid(calls):
    h = _auth(); _panel(h)
    r = client.post("/api/subpage-configs/u1/clone", headers=h)
    assert r.status_code == 201
    assert calls[-1][2] == {"cloneFromUuid": "u1"}


def test_delete_targets_the_uuid_path(calls):
    h = _auth(); _panel(h)
    r = client.delete("/api/subpage-configs/u1", headers=h)
    assert r.status_code == 200
    method, path, _ = calls[-1]
    assert (method, path) == ("DELETE", "/api/subscription-page-configs/u1")


# ── route resolution ──────────────────────────────────────────
def test_reorder_is_not_swallowed_by_the_uuid_route(calls):
    """`POST /reorder` must reach the reorder handler, not be parsed as a uuid."""
    h = _auth(); _panel(h)
    client.post("/api/subpage-configs/reorder", headers=h, json={"items": []})
    assert calls[-1][1].endswith("/actions/reorder")


# ── isolation ─────────────────────────────────────────────────
def test_panels_are_per_account(calls):
    h1, h2 = _auth(), _auth()
    _panel(h1)
    assert client.get("/api/subpage-configs", headers=h2).status_code == 400
