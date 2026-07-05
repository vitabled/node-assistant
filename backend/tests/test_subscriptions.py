"""Ф8 — per-account subscription store, CRUD isolation, and the internal
aggregator-source endpoint (cross-account active set)."""
import sys
import types

sys.modules.setdefault("asyncssh", types.ModuleType("asyncssh"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import accounts  # noqa: E402

client = TestClient(app)


def _register(login):
    r = client.post("/api/auth/register", json={"login": login, "password": "Str0ng-pw"})
    assert r.status_code == 201, r.text
    return r.json()


def _auth(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_crud_and_per_account_isolation():
    a = _register("subs-a")
    b = _register("subs-b")

    # A creates a background sub
    r = client.post("/api/subscriptions", headers=_auth(a["token"]),
                    json={"url": "https://sub.example.com/a", "background": True})
    assert r.status_code == 201
    sub_a = r.json()
    assert sub_a["background"] is True

    # A sees its sub; B sees none (isolation)
    assert len(client.get("/api/subscriptions", headers=_auth(a["token"])).json()) == 1
    assert client.get("/api/subscriptions", headers=_auth(b["token"])).json() == []

    # patch toggles background off
    r = client.patch(f"/api/subscriptions/{sub_a['id']}", headers=_auth(a["token"]),
                     json={"background": False})
    assert r.json()["background"] is False

    # delete
    assert client.delete(f"/api/subscriptions/{sub_a['id']}", headers=_auth(a["token"])).status_code == 204
    assert client.get("/api/subscriptions", headers=_auth(a["token"])).json() == []


def test_crud_requires_auth():
    assert client.get("/api/subscriptions").status_code == 401
    assert client.post("/api/subscriptions", json={"url": "x"}).status_code == 401


def test_patch_unknown_sub_404():
    a = _register("subs-404")
    r = client.patch("/api/subscriptions/nope", headers=_auth(a["token"]), json={"background": True})
    assert r.status_code == 404


def test_internal_agg_subs_is_ungated_and_cross_account():
    a = _register("agg-a")
    b = _register("agg-b")
    # A: one background + one non-background; B: one background
    client.post("/api/subscriptions", headers=_auth(a["token"]),
                json={"url": "https://a.example/bg", "background": True})
    client.post("/api/subscriptions", headers=_auth(a["token"]),
                json={"url": "https://a.example/fg", "background": False})
    client.post("/api/subscriptions", headers=_auth(b["token"]),
                json={"url": "https://b.example/bg", "background": True})

    # internal endpoint: NO auth header, returns only background+enabled across accounts
    r = client.get("/internal/agg-subs")
    assert r.status_code == 200
    items = r.json()
    urls = {i["url"] for i in items}
    assert "https://a.example/bg" in urls
    assert "https://b.example/bg" in urls
    assert "https://a.example/fg" not in urls  # non-background excluded
    # each item is tagged with its owning account
    by_url = {i["url"]: i for i in items}
    assert by_url["https://a.example/bg"]["account_id"] == a["id"]
    assert by_url["https://b.example/bg"]["account_id"] == b["id"]


def test_rejects_non_http_scheme_url():
    a = _register("subs-scheme")
    for bad in ["file:///etc/passwd", "ftp://h/x", "gopher://h/"]:
        r = client.post("/api/subscriptions", headers=_auth(a["token"]),
                        json={"url": bad, "background": True})
        assert r.status_code == 422, bad


def test_internal_agg_subs_excludes_disabled():
    a = _register("agg-dis")
    r = client.post("/api/subscriptions", headers=_auth(a["token"]),
                    json={"url": "https://dis.example/bg", "background": True})
    sub = r.json()
    client.patch(f"/api/subscriptions/{sub['id']}", headers=_auth(a["token"]),
                 json={"enabled": False})
    urls = {i["url"] for i in client.get("/internal/agg-subs").json()}
    assert "https://dis.example/bg" not in urls
