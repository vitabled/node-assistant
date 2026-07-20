"""Wave-4 Plan A — «Хостинги» catalogue CRUD + isolation + validation."""
import uuid

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"hg-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_requires_auth():
    assert client.get("/api/hostings").status_code == 401


def test_crud_and_isolation():
    a = _auth()
    b = _auth()
    assert client.get("/api/hostings", headers=a).json() == []

    body = {
        "name": "Hetzner", "website": "https://hetzner.com",
        "features": "BBR, IPv6", "notes": "хороший",
        "tariffs": [{"name": "CX22", "specs": "2 vCPU / 4 GB", "price": 5.5, "currency": "EUR", "period": "mo"}],
        "locations": [{"city": "Falkenstein", "country_code": "DE", "lat": 50.5, "lng": 12.4}],
    }
    r = client.post("/api/hostings", headers=a, json=body)
    assert r.status_code == 201
    hid = r.json()["id"]
    assert r.json()["name"] == "Hetzner" and r.json()["created_at"] > 0

    lst = client.get("/api/hostings", headers=a).json()
    assert len(lst) == 1 and lst[0]["tariffs"][0]["price"] == 5.5
    assert lst[0]["locations"][0]["country_code"] == "DE"

    # per-account isolation
    assert client.get("/api/hostings", headers=b).json() == []

    r = client.put(f"/api/hostings/{hid}", headers=a, json={**body, "name": "Hetzner Cloud"})
    assert r.status_code == 200 and r.json()["name"] == "Hetzner Cloud" and r.json()["id"] == hid

    assert client.delete(f"/api/hostings/{hid}", headers=a).status_code == 204
    assert client.delete(f"/api/hostings/{hid}", headers=a).status_code == 404
    assert client.get("/api/hostings", headers=a).json() == []


def test_validation():
    a = _auth()
    assert client.post("/api/hostings", headers=a, json={"name": ""}).status_code == 422
    # lat out of range
    assert client.post("/api/hostings", headers=a,
                       json={"name": "X", "locations": [{"lat": 200}]}).status_code == 422


def test_update_missing_404():
    a = _auth()
    assert client.put("/api/hostings/nope", headers=a, json={"name": "X"}).status_code == 404
