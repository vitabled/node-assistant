"""Wave-5 Plan L (slice 1) — export/import round-trip, secret-strip, confirm, isolation."""
import uuid

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register", json={"login": f"ex-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _export(h) -> bytes:
    r = client.post("/api/export", headers=h, json={})
    assert r.status_code == 200
    return r.content


def _import(h, blob, confirm=True):
    return client.post("/api/import", headers=h,
                       files={"file": ("e.tar.gz", blob, "application/gzip")},
                       data={"confirm": "true" if confirm else "false"})


def test_roundtrip_strips_secrets_and_moves_data():
    a = _auth()
    # seed account A: a config template, a host, and a panel with a secret token
    client.post("/api/config-templates", headers=a, json={"name": "t1", "kind": "xray-json", "content_json": {}})
    client.post("/api/hosts", headers=a, json={"remark": "H1", "address": "n.example.com", "port": 443})
    client.post("/api/settings/remnawave", headers=a, json={"panel_url": "https://p", "api_token": "SECRET"})

    blob = _export(a)

    b = _auth()
    rep = _import(b, blob)
    assert rep.status_code == 200 and "settings.json" in rep.json()["applied"]
    # data moved
    assert len(client.get("/api/config-templates", headers=b).json()) == 1
    assert len(client.get("/api/hosts", headers=b).json()) == 1
    rw = client.get("/api/settings", headers=b).json()["remnawave"]
    assert rw["panel_url"] == "https://p"     # non-secret carried over
    assert rw["api_token"] == ""              # secret stripped


def test_import_keeps_target_credentials():
    # A no-secrets import must NOT touch the target's credential sections.
    a = _auth()
    client.post("/api/settings/remnawave", headers=a, json={"panel_url": "https://src", "api_token": "x"})
    blob = _export(a)
    b = _auth()
    client.post("/api/settings/remnawave", headers=b, json={"panel_url": "https://dst", "api_token": "KEEPME"})
    _import(b, blob)
    rw = client.get("/api/settings", headers=b).json()["remnawave"]
    assert rw["panel_url"] == "https://dst"   # target's panel config untouched
    assert rw["api_token"] == "KEEPME"        # target's secret preserved


def test_confirm_and_bad_archive():
    h = _auth()
    blob = _export(h)
    assert _import(h, blob, confirm=False).status_code == 400
    assert _import(h, b"not a tar.gz").status_code == 422


def test_isolation():
    a = _auth()
    client.post("/api/hosts", headers=a, json={"remark": "solo", "address": "a.com", "port": 443})
    blob = _export(a)
    b = _auth()
    _import(b, blob)
    # importing into B did not touch A
    assert len(client.get("/api/hosts", headers=a).json()) == 1
    assert len(client.get("/api/hosts", headers=b).json()) == 1
