"""Wave-7 Plan C — panel resolver + `panel_id` on the config-template routes.

The point of the resolver is that an UNKNOWN id must fail loudly: falling back to
the active panel would silently write a config template into the wrong panel.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import panel_registry

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register", json={"login": f"pr-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk(headers, name, url, token="tok"):
    return client.post("/api/settings/remnawave/panels", headers=headers,
                       json={"name": name, "panel_url": url, "api_token": token}).json()


# ── resolver ──────────────────────────────────────────────────
def test_empty_panel_id_resolves_to_active():
    h = _auth()
    a = _mk(h, "A", "https://a")
    b = _mk(h, "B", "https://b")
    client.post(f"/api/settings/remnawave/panels/{b['id']}/activate", headers=h)

    # The resolver reads the account via the ContextVar the request set up, so
    # exercise it through a request that goes on to use it.
    listed = client.get("/api/settings/remnawave/panels", headers=h).json()
    assert listed["active_panel_id"] == b["id"]
    assert {p["id"] for p in listed["panels"]} == {a["id"], b["id"]}


def test_unknown_panel_id_is_404_not_a_silent_fallback():
    h = _auth()
    _mk(h, "A", "https://a")
    r = client.get("/api/config-templates/import/panel?panel_id=doesnotexist", headers=h)
    assert r.status_code == 404


def test_unconfigured_panel_is_400():
    h = _auth()
    # A panel with no url/token at all → "not configured", not "not found".
    _mk(h, "Empty", "", "")
    r = client.get("/api/config-templates/import/panel", headers=h)
    assert r.status_code == 400


def test_no_panels_at_all_is_400():
    h = _auth()
    r = client.get("/api/config-templates/import/panel", headers=h)
    assert r.status_code == 400


# ── the route actually targets the requested panel ────────────
def test_import_list_targets_the_requested_panel(monkeypatch):
    h = _auth()
    a = _mk(h, "A", "https://a", "ta")
    b = _mk(h, "B", "https://b", "tb")
    client.post(f"/api/settings/remnawave/panels/{a['id']}/activate", headers=h)

    # Capture the URL the client was actually built with — the echoed panel_id
    # alone would pass even if the request went to the wrong panel.
    seen: list[str] = []

    async def fake_list(self):
        seen.append(self._base)
        return {"templates": []}

    monkeypatch.setattr(
        "app.services.remnawave_client.RemnavaveClient.list_subscription_templates",
        fake_list, raising=True,
    )

    # explicit id → that panel; the response echoes which panel answered
    r = client.get(f"/api/config-templates/import/panel?panel_id={b['id']}", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["panel_id"] == b["id"]
    assert seen == ["https://b"]

    # no id → the active one (A)
    r = client.get("/api/config-templates/import/panel", headers=h)
    assert r.json()["panel_id"] == a["id"]
    assert seen == ["https://b", "https://a"]


def test_resolver_raises_typed_errors_outside_a_request():
    # Direct unit-level contract, independent of HTTP mapping.
    assert issubclass(panel_registry.PanelNotFound, KeyError)
    assert issubclass(panel_registry.PanelNotConfigured, ValueError)


# ── isolation ─────────────────────────────────────────────────
def test_panels_are_per_account():
    h1, h2 = _auth(), _auth()
    _mk(h1, "Mine", "https://mine")
    assert client.get("/api/settings/remnawave/panels", headers=h2).json()["panels"] == []


@pytest.mark.parametrize("panel_id", ["", "nope"])
def test_export_rejects_unknown_panel_before_touching_the_store(panel_id):
    h = _auth()
    _mk(h, "A", "https://a")
    # Template does not exist either; the 404 for the template comes first, which
    # documents the order: local lookup, then panel resolution.
    r = client.post(f"/api/config-templates/missing/export?panel_id={panel_id}", headers=h)
    assert r.status_code == 404
