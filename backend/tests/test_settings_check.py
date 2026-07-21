"""Plan E 11b — «Проверить соединение» Remnawave тестирует введённые в форму
значения (тело запроса), а не только сохранённые настройки."""
import uuid

from fastapi.testclient import TestClient

from app.main import app
import app.api.settings as st

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"cs-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_check_uses_form_creds(monkeypatch):
    seen = {}

    class Fake:
        def __init__(self, url, token):
            seen["url"], seen["token"] = url, token

        async def check_connection(self):
            return {"version": "ok"}

    monkeypatch.setattr(st, "RemnavaveClient", Fake)
    r = client.post("/api/settings/remnawave/check", headers=_auth(),
                    json={"panel_url": "http://form", "api_token": "formtok"})
    assert r.status_code == 200 and r.json()["ok"] is True
    # tested the FORM creds, not the (empty) saved ones
    assert seen == {"url": "http://form", "token": "formtok"}


def test_check_falls_back_to_saved(monkeypatch):
    seen = {}

    class Fake:
        def __init__(self, url, token):
            seen["url"], seen["token"] = url, token

        async def check_connection(self):
            return {}

    monkeypatch.setattr(st, "RemnavaveClient", Fake)
    monkeypatch.setattr(st.storage, "load_settings",
                        lambda aid=None: {"remnawave": {"panel_url": "http://saved", "api_token": "savedtok"}})
    r = client.post("/api/settings/remnawave/check", headers=_auth(), json={})
    assert r.status_code == 200
    assert seen == {"url": "http://saved", "token": "savedtok"}


def test_check_400_when_nothing_configured():
    # fresh account, empty body → no creds anywhere → 400 (not 500)
    r = client.post("/api/settings/remnawave/check", headers=_auth(), json={})
    assert r.status_code == 400
