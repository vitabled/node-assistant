"""Wave-8 §4 — auto-backup to Telegram: config CRUD, token vault, run endpoint."""
import uuid

from fastapi.testclient import TestClient

import app.services.auto_backup as ab
import app.services.telegram as tg
from app.main import app

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"bk-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ── token vault (pure) ─────────────────────────────────────────
def test_token_fernet_roundtrip():
    enc = ab.encrypt_token("111:SECRET")
    assert enc and enc != "111:SECRET"
    assert ab.decrypt_token(enc) == "111:SECRET"
    assert ab.decrypt_token("") is None
    assert ab.decrypt_token("garbage") is None


# ── config CRUD ────────────────────────────────────────────────
def test_config_crud_and_token_hidden():
    a = _auth()
    r = client.get("/api/settings/auto-backup", headers=a).json()
    assert r["enabled"] is False and r["has_token"] is False and "bot_token_enc" not in r

    client.post("/api/settings/auto-backup", headers=a, json={
        "enabled": True, "interval_hours": 12, "include_secrets": True,
        "chat_id": "123", "bot_token": "111:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"})
    r2 = client.get("/api/settings/auto-backup", headers=a).json()
    assert r2["enabled"] and r2["interval_hours"] == 12 and r2["include_secrets"] is True
    assert r2["chat_id"] == "123" and r2["has_token"] is True
    assert "bot_token" not in r2 and "bot_token_enc" not in r2

    # blank bot_token keeps the stored one (write-only field)
    client.post("/api/settings/auto-backup", headers=a, json={
        "enabled": False, "interval_hours": 24, "include_secrets": False,
        "chat_id": "123", "bot_token": ""})
    r3 = client.get("/api/settings/auto-backup", headers=a).json()
    assert r3["has_token"] is True and r3["enabled"] is False


# ── run endpoint ───────────────────────────────────────────────
def test_run_sends_document(monkeypatch):
    a = _auth()
    captured = {}

    async def fake_send(token, chat_id, filename, data, caption=""):
        captured.update(token=token, chat_id=chat_id, filename=filename,
                        size=len(data), caption=caption)
        return {"ok": True}

    monkeypatch.setattr(tg, "send_document", fake_send)
    client.post("/api/settings/auto-backup", headers=a, json={
        "enabled": True, "interval_hours": 24, "include_secrets": False,
        "chat_id": "999", "bot_token": "111:TOPSECRETTOKENVALUEXXXXXXXXXXXX"})
    r = client.post("/api/settings/auto-backup/run", headers=a)
    assert r.status_code == 200
    assert captured["chat_id"] == "999"
    assert captured["token"] == "111:TOPSECRETTOKENVALUEXXXXXXXXXXXX"
    assert captured["filename"].endswith(".tar.gz") and captured["size"] > 0


def test_run_without_token_400():
    a = _auth()
    client.post("/api/settings/auto-backup", headers=a, json={
        "enabled": True, "interval_hours": 24, "include_secrets": False,
        "chat_id": "", "bot_token": ""})
    assert client.post("/api/settings/auto-backup/run", headers=a).status_code == 400


def test_include_secrets_propagates(monkeypatch):
    a = _auth()
    seen = {}

    def fake_build(account_id, include_secrets=False, **kw):
        seen["include_secrets"] = include_secrets
        return b"archive-bytes"

    async def fake_send(*args, **kw):
        return {"ok": True}

    monkeypatch.setattr("app.services.export_service.build_archive", fake_build)
    monkeypatch.setattr(tg, "send_document", fake_send)
    client.post("/api/settings/auto-backup", headers=a, json={
        "enabled": True, "interval_hours": 24, "include_secrets": True,
        "chat_id": "1", "bot_token": "111:TOKENXXXXXXXXXXXXXXXXXXXXXXXXXXX"})
    client.post("/api/settings/auto-backup/run", headers=a)
    assert seen["include_secrets"] is True
