"""Tests for main.py wiring — which routers are public vs. account-gated."""
import uuid

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_is_public():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    # Plan M: health also reports which process owns each background duty.
    assert body["role"] == "gateway"
    assert body["taskStore"]["mode"] == "memory"
    assert {d["name"] for d in body["duties"]} == {"monitoring", "deploy-worker"}


def test_auth_routes_are_public():
    r = client.post("/api/auth/register",
                    json={"login": f"m-{uuid.uuid4().hex[:8]}", "password": "pw"})
    assert r.status_code == 201


def test_data_routers_are_gated():
    # A representative route from each gated router → 401 without a token.
    for path in ["/api/settings", "/api/templates", "/api/traffic-rules",
                 "/api/infra-billing/settings", "/api/checker/status"]:
        assert client.get(path).status_code == 401, path


def test_gated_route_passes_with_token():
    r = client.post("/api/auth/register",
                    json={"login": f"m-{uuid.uuid4().hex[:8]}", "password": "pw"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    assert client.get("/api/settings", headers=h).status_code == 200
