"""Wave-4 PR-1 — downstream-401/403 не уходят наружу (иначе apiClient
разлогинивал оператора: панель с плохим токеном выбивала его в вечный логаут).
Плюс маркер x-session-invalid на настоящих сессионных 401."""
import uuid

from fastapi.testclient import TestClient

from app.api.downstream import downstream_exception
from app.main import app
import app.api.settings as st
from app.services.remnawave_client import RemnavaveError

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"ds-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ── helper ──────────────────────────────────────────────────────
def test_helper_clamps_401_and_403_to_502():
    for s in (401, 403):
        exc = downstream_exception(s, "bad token", "Панель Remnawave")
        assert exc.status_code == 502
        assert f"ответил {s}" in exc.detail and "bad token" in exc.detail


def test_helper_keeps_other_statuses():
    assert downstream_exception(404, "nf").status_code == 404
    assert downstream_exception(429, "rl").status_code == 429
    assert downstream_exception(None, "x").status_code == 502
    assert downstream_exception(0, "x").status_code == 502


# ── endpoint level: 401 панели → 502, а не 401 ──────────────────
def test_check_panel_401_becomes_502(monkeypatch):
    class Fake:
        def __init__(self, url, token):
            pass

        async def check_connection(self):
            raise RemnavaveError(401, "Unauthorized")

    monkeypatch.setattr(st, "RemnavaveClient", Fake)
    r = client.post("/api/settings/remnawave/check", headers=_auth(),
                    json={"panel_url": "http://form", "api_token": "bad"})
    assert r.status_code == 502
    assert "ответил 401" in r.json()["detail"]


def test_check_panel_404_kept(monkeypatch):
    class Fake:
        def __init__(self, url, token):
            pass

        async def check_connection(self):
            raise RemnavaveError(404, "not found")

    monkeypatch.setattr(st, "RemnavaveClient", Fake)
    r = client.post("/api/settings/remnawave/check", headers=_auth(),
                    json={"panel_url": "http://form", "api_token": "tok"})
    assert r.status_code == 404


# ── маркер сессионного 401 ──────────────────────────────────────
def test_real_session_401_carries_marker():
    r = client.get("/api/settings/remnawave/panels")  # без токена
    assert r.status_code == 401
    assert r.headers.get("x-session-invalid") == "1"


def test_wrong_login_401_carries_marker():
    r = client.post("/api/auth/login", json={"login": "ghost", "password": "nope-nope"})
    assert r.status_code == 401
    assert r.headers.get("x-session-invalid") == "1"
