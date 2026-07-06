"""Auth, session, per-account isolation and PIN-gate-removal tests.

Covers the required edge cases: empty fields, duplicate login (boundary/uniqueness),
wrong password, missing/malformed/garbage token (permission + malformed input),
token for a deleted account (deleted resource), and data isolation A vs B.
"""
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.services import accounts

client = TestClient(app)


def _register(login, password="Str0ng-pw"):
    return client.post("/api/auth/register", json={"login": login, "password": password})


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _uniq(prefix="user"):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ── registration ──────────────────────────────────────────────
def test_register_returns_token_and_logs_in():
    r = _register(_uniq())
    assert r.status_code == 201
    body = r.json()
    assert body["token"] and body["id"] and body["login"]
    # token works against a protected route
    me = client.get("/api/auth/me", headers=_auth(body["token"]))
    assert me.status_code == 200
    assert me.json()["id"] == body["id"]


def test_register_duplicate_login_conflicts():
    login = _uniq("dup")
    assert _register(login).status_code == 201
    r = _register(login)
    assert r.status_code == 409


def test_register_duplicate_login_case_insensitive():
    login = _uniq("Case")
    assert _register(login.lower()).status_code == 201
    assert _register(login.upper()).status_code == 409


def test_register_empty_fields_rejected():
    # empty login
    assert client.post("/api/auth/register", json={"login": "", "password": "x"}).status_code == 422
    # empty password
    assert client.post("/api/auth/register", json={"login": _uniq(), "password": ""}).status_code == 422
    # whitespace-only login collapses to empty -> 422
    assert client.post("/api/auth/register", json={"login": "   ", "password": "x"}).status_code == 422
    # whitespace-only password collapses to empty -> 422
    assert client.post("/api/auth/register", json={"login": _uniq(), "password": "   "}).status_code == 422


# ── login ─────────────────────────────────────────────────────
def test_login_wrong_password():
    login = _uniq("wp")
    _register(login, "correct-horse")
    r = client.post("/api/auth/login", json={"login": login, "password": "wrong"})
    assert r.status_code == 401


def test_login_nonexistent_account():
    r = client.post("/api/auth/login", json={"login": _uniq("ghost"), "password": "whatever"})
    assert r.status_code == 401


def test_login_success_after_register():
    login = _uniq("li")
    _register(login, "my-password-1")
    r = client.post("/api/auth/login", json={"login": login, "password": "my-password-1"})
    assert r.status_code == 200
    assert r.json()["token"]


# ── token enforcement (permission + malformed input) ──────────
def test_protected_route_without_token_401():
    assert client.get("/api/settings").status_code == 401


def test_protected_route_malformed_header_401():
    # not a Bearer scheme
    assert client.get("/api/settings", headers={"Authorization": "Basic abc"}).status_code == 401
    # bearer with garbage jwt
    assert client.get("/api/settings", headers=_auth("not-a-jwt")).status_code == 401
    # empty bearer
    assert client.get("/api/settings", headers={"Authorization": "Bearer "}).status_code == 401


def test_token_for_deleted_account_401():
    # Issue a valid JWT for an account id that isn't in the registry.
    ghost_token = accounts.issue_token(str(uuid.uuid4()))
    assert client.get("/api/settings", headers=_auth(ghost_token)).status_code == 401


# ── isolation A vs B ──────────────────────────────────────────
def test_settings_isolated_between_accounts():
    a = _register(_uniq("iso-a")).json()
    b = _register(_uniq("iso-b")).json()

    # A writes a distinctive Remnawave panel URL.
    save = client.post(
        "/api/settings/remnawave",
        headers=_auth(a["token"]),
        json={"panel_url": "https://panel-A.example", "api_token": "tokA",
              "default_internal_squad_ids": [], "default_external_squad_ids": []},
    )
    assert save.status_code == 200

    # A reads it back.
    a_view = client.get("/api/settings", headers=_auth(a["token"])).json()
    assert a_view["remnawave"]["panel_url"] == "https://panel-A.example"

    # B must NOT see A's data — B sees defaults (empty panel_url).
    b_view = client.get("/api/settings", headers=_auth(b["token"])).json()
    assert b_view["remnawave"]["panel_url"] == ""


def test_templates_isolated_between_accounts():
    a = _register(_uniq("tpl-a")).json()
    b = _register(_uniq("tpl-b")).json()

    created = client.post("/api/templates", headers=_auth(a["token"]),
                          json={"name": "A-only", "config": "cfg", "is_default": False})
    assert created.status_code == 201

    assert any(t["name"] == "A-only" for t in client.get("/api/templates", headers=_auth(a["token"])).json())
    assert client.get("/api/templates", headers=_auth(b["token"])).json() == []


# ── PIN gate removed ──────────────────────────────────────────
def test_infra_settings_has_no_pin_and_payments_open_with_account():
    a = _register(_uniq("pin")).json()
    h = _auth(a["token"])

    s = client.get("/api/infra-billing/settings", headers=h)
    assert s.status_code == 200
    assert "pinSet" not in s.json()          # PIN concept fully removed

    # verify-session endpoint no longer exists
    assert client.post("/api/infra-billing/auth/verify-session", headers=h, json={"pin": ""}).status_code == 404

    # payments accessible with just the account token (no X-Billing-Session)
    assert client.get("/api/infra-billing/payments", headers=h).status_code == 200


def test_infra_billing_isolated_between_accounts():
    a = _register(_uniq("bill-a")).json()
    b = _register(_uniq("bill-b")).json()

    made = client.post("/api/infra-billing/payments", headers=_auth(a["token"]),
                       json={"amount": 123.45, "type": "topup", "note": "A-payment"})
    assert made.status_code == 201

    a_pays = client.get("/api/infra-billing/payments", headers=_auth(a["token"])).json()
    b_pays = client.get("/api/infra-billing/payments", headers=_auth(b["token"])).json()
    assert any(p["note"] == "A-payment" for p in a_pays)
    assert b_pays == []
