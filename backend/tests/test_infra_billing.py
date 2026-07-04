"""HTTP tests for api/infra_billing.py — account-gated (no PIN sub-gate),
per-account data, and the Remnawave-config guard on proxy routes."""
import uuid

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"ib-{uuid.uuid4().hex[:8]}", "password": "pw-1"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_all_infra_routes_require_an_account():
    assert client.get("/api/infra-billing/settings").status_code == 401
    assert client.get("/api/infra-billing/payments").status_code == 401


def test_settings_roundtrip_without_pin():
    h = _auth()
    s = client.get("/api/infra-billing/settings", headers=h).json()
    assert "pinSet" not in s
    upd = client.put("/api/infra-billing/settings", headers=h,
                     json={"base_currency": "USD"})
    assert upd.status_code == 200
    assert client.get("/api/infra-billing/settings", headers=h).json()["baseCurrency"] == "USD"


def test_verify_session_endpoint_is_gone():
    h = _auth()
    assert client.post("/api/infra-billing/auth/verify-session", headers=h,
                       json={"pin": ""}).status_code == 404


def test_payments_and_projects_crud():
    h = _auth()
    pay = client.post("/api/infra-billing/payments", headers=h,
                      json={"amount": 42.0, "type": "topup", "note": "n"})
    assert pay.status_code == 201
    assert any(p["note"] == "n" for p in client.get("/api/infra-billing/payments", headers=h).json())

    proj = client.post("/api/infra-billing/projects", headers=h,
                       json={"name": "Proj", "description": "", "node_uuids": []})
    assert proj.status_code == 201
    assert any(p["name"] == "Proj" for p in client.get("/api/infra-billing/projects", headers=h).json())


def test_provider_routes_require_remnawave_config():
    h = _auth()
    # A fresh account has no Remnawave config → proxy routes surface a 400.
    assert client.get("/api/infra-billing/providers", headers=h).status_code == 400


def test_billing_isolated_between_accounts():
    a, b = _auth(), _auth()
    client.post("/api/infra-billing/payments", headers=a,
                json={"amount": 7.0, "note": "A-only"})
    assert any(p["note"] == "A-only" for p in client.get("/api/infra-billing/payments", headers=a).json())
    assert client.get("/api/infra-billing/payments", headers=b).json() == []
