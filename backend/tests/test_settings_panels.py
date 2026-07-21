"""Wave-5 Plan K — Remnawave panel registry: legacy migration, active resolution,
CRUD + activation, backward-compat, per-account isolation."""
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.models.settings import AppSettings

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register", json={"login": f"pn-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ── model resolver / migration ────────────────────────────────
def test_legacy_migration_and_resolution():
    s = AppSettings(remnawave={"panel_url": "https://p1", "api_token": "t1"})
    assert len(s.remnawave_registry.panels) == 1
    assert s.remnawave_registry.panels[0].id == "primary"
    assert s.remnawave.panel_url == "https://p1"


def test_active_selection_and_bad_id_fallback():
    reg = {"panels": [{"id": "a", "panel_url": "u1", "api_token": "t1"},
                      {"id": "b", "panel_url": "u2", "api_token": "t2"}], "active_panel_id": "b"}
    assert AppSettings(remnawave_registry=reg).remnawave.panel_url == "u2"
    reg2 = {"panels": [{"id": "a", "panel_url": "u1"}], "active_panel_id": "zzz"}
    s2 = AppSettings(remnawave_registry=reg2)
    assert s2.remnawave.panel_url == "u1" and s2.remnawave_registry.active_panel_id == "a"


def test_empty_stays_empty():
    s = AppSettings()
    assert s.remnawave.panel_url == "" and s.remnawave_registry.panels == []


# ── HTTP CRUD ─────────────────────────────────────────────────
def test_panel_crud_activate_and_delete():
    h = _auth()
    p1 = client.post("/api/settings/remnawave/panels", headers=h,
                     json={"name": "P1", "panel_url": "https://p1", "api_token": "t1"}).json()
    p2 = client.post("/api/settings/remnawave/panels", headers=h,
                     json={"name": "P2", "panel_url": "https://p2", "api_token": "t2"}).json()
    lst = client.get("/api/settings/remnawave/panels", headers=h).json()
    assert len(lst["panels"]) == 2 and lst["active_panel_id"] == p1["id"]  # first → active
    # activate second → computed .remnawave reflects it
    assert client.post(f"/api/settings/remnawave/panels/{p2['id']}/activate", headers=h).status_code == 200
    assert client.get("/api/settings", headers=h).json()["remnawave"]["panel_url"] == "https://p2"
    # update the active
    client.put(f"/api/settings/remnawave/panels/{p2['id']}", headers=h,
               json={"name": "P2x", "panel_url": "https://p2x", "api_token": "t2"})
    assert client.get("/api/settings", headers=h).json()["remnawave"]["panel_url"] == "https://p2x"
    # delete active → pointer moves to the remaining panel
    client.delete(f"/api/settings/remnawave/panels/{p2['id']}", headers=h)
    got = client.get("/api/settings/remnawave/panels", headers=h).json()
    assert got["active_panel_id"] == p1["id"] and len(got["panels"]) == 1
    assert client.put("/api/settings/remnawave/panels/nope", headers=h, json={"panel_url": "x"}).status_code == 404
    assert client.post("/api/settings/remnawave/panels/nope/activate", headers=h).status_code == 404


def test_compat_post_writes_active_panel():
    h = _auth()
    client.post("/api/settings/remnawave", headers=h, json={"panel_url": "https://c1", "api_token": "tk"})
    panels = client.get("/api/settings/remnawave/panels", headers=h).json()["panels"]
    assert len(panels) == 1 and panels[0]["panel_url"] == "https://c1"
    assert client.get("/api/settings", headers=h).json()["remnawave"]["panel_url"] == "https://c1"


def test_isolation():
    a = _auth()
    client.post("/api/settings/remnawave/panels", headers=a, json={"panel_url": "https://a"})
    b = _auth()
    assert client.get("/api/settings/remnawave/panels", headers=b).json()["panels"] == []
