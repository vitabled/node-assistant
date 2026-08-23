"""Двухфазная отправка вложений чата (`services/ai_uploads` + `POST /api/ai/chat/upload`).

Ради чего модуль есть: 50-мегабайтный архив в base64 внутри тела `POST /chat`
давал ~67 МБ JSON, и у клиента за VPN такой запрос рвался на середине. Теперь
файл едет отдельным multipart-запросом, а чат получает лишь `upload_ids`.

Тесты идут через HTTP (`TestClient`), а не по функциям стора: проверять надо
именно связку «загрузил → сослался → агент увидел файл», в ней и был баг.
"""

import io
import json
import time
import uuid

from fastapi.testclient import TestClient

from app.services import ai_agent, ai_uploads
from app.main import app

client = TestClient(app)


def _auth():
    login = f"aiup-{uuid.uuid4().hex[:8]}"
    r = client.post("/api/auth/register", json={"login": login, "password": "pw-1"})
    body = r.json()
    return {"Authorization": f"Bearer {body['token']}"}, body["id"]


def _configure(h, **over):
    body = {
        "enabled": True, "provider": "openai",
        "base_url": "https://mock.example/v1", "model": "gpt-x",
        "api_key": "sk-test", "max_steps": 4,
    }
    body.update(over)
    return client.post("/api/ai/config", headers=h, json=body)


def _script_provider(monkeypatch, text="ок"):
    async def fake(config, key, messages, with_tools=True, system="", **kw):
        return {"text": text, "tool_calls": [], "raw": {}}
    monkeypatch.setattr(ai_agent, "_provider_turn", fake)


def _upload(h, name, mime, data: bytes, session_id="s1"):
    return client.post(
        "/api/ai/chat/upload", headers=h,
        files={"file": (name, io.BytesIO(data), mime)},
        data={"session_id": session_id},
    )


def _gzip_bytes(payload: bytes = b"1.2.3.0/24\n4.5.6.0/24\n") -> bytes:
    import gzip
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("blocks.txt")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


# ── ручка загрузки ───────────────────────────────────────────
def test_upload_returns_id_name_mime_size():
    h, _ = _auth()
    raw = _gzip_bytes()
    r = _upload(h, "as-ip-blocks.tar.gz", "application/gzip", raw)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "as-ip-blocks.tar.gz"
    assert body["mime"] == "application/gzip"
    assert body["size"] == len(raw)
    assert len(body["upload_id"]) == 32


def test_upload_requires_account():
    r = client.post("/api/ai/chat/upload",
                    files={"file": ("a.txt", io.BytesIO(b"hi"), "text/plain")})
    assert r.status_code == 401


def test_upload_rejects_over_limit(monkeypatch):
    h, _ = _auth()
    # Гоняем реальные 50 МБ ради одной проверки размера незачем — опускаем
    # потолок и проверяем ровно то правило, которое и падало у пользователя.
    monkeypatch.setattr(ai_uploads, "MAX_UPLOAD_BYTES", 1024)
    r = _upload(h, "big.tar.gz", "application/gzip", b"x" * 2048)
    assert r.status_code == 400
    assert "больше" in r.json()["detail"]


def test_upload_rejects_unknown_type():
    h, _ = _auth()
    r = _upload(h, "payload.exe", "application/x-msdownload", b"MZ\x00\x00")
    assert r.status_code == 400
    assert "не принимается" in r.json()["detail"]


def test_upload_rejects_empty_file():
    h, _ = _auth()
    r = _upload(h, "empty.txt", "text/plain", b"")
    assert r.status_code == 400


# ── чат с upload_id ──────────────────────────────────────────
def test_chat_with_upload_id_unpacks_archive(monkeypatch):
    """Файл, уехавший отдельно, доезжает до агента распакованным — как раньше
    доезжал base64 в теле."""
    h, _ = _auth()
    _configure(h)
    seen: dict = {}

    async def fake(config, key, messages, with_tools=True, system="", **kw):
        seen["messages"] = messages
        return {"text": "ок", "tool_calls": [], "raw": {}}
    monkeypatch.setattr(ai_agent, "_provider_turn", fake)

    up = _upload(h, "as-ip-blocks.tar.gz", "application/gzip", _gzip_bytes()).json()
    r = client.post("/api/ai/chat", headers=h,
                    json={"prompt": "что в архиве?", "upload_ids": [up["upload_id"]],
                          "session_id": "s1"})
    assert r.status_code == 200
    events = [json.loads(ln) for ln in r.text.splitlines() if ln.strip()]
    assert events[-1]["type"] == "done"
    blob = json.dumps(seen["messages"], ensure_ascii=False)
    assert "as-ip-blocks.tar.gz" in blob
    assert "blocks.txt" in blob


def test_chat_with_upload_id_text_file(monkeypatch):
    h, _ = _auth()
    _configure(h)
    seen: dict = {}

    async def fake(config, key, messages, with_tools=True, system="", **kw):
        seen["messages"] = messages
        return {"text": "ок", "tool_calls": [], "raw": {}}
    monkeypatch.setattr(ai_agent, "_provider_turn", fake)

    up = _upload(h, "nginx.conf", "text/plain", b"server { listen 443; }").json()
    r = client.post("/api/ai/chat", headers=h,
                    json={"prompt": "разбери конфиг", "upload_ids": [up["upload_id"]]})
    assert r.status_code == 200
    assert "listen 443" in json.dumps(seen["messages"], ensure_ascii=False)


def test_chat_unknown_upload_id_is_400(monkeypatch):
    """Молча ответить без файла нельзя: агент выдумает его содержимое."""
    h, _ = _auth()
    _configure(h)
    _script_provider(monkeypatch)
    r = client.post("/api/ai/chat", headers=h,
                    json={"prompt": "?", "upload_ids": ["0" * 32]})
    assert r.status_code == 400
    assert "не найден" in r.json()["detail"]


def test_upload_is_per_account(monkeypatch):
    """Чужая загрузка не видна: id угадать нельзя, но и подставить тоже."""
    h1, _ = _auth()
    h2, _ = _auth()
    _configure(h2)
    _script_provider(monkeypatch)
    up = _upload(h1, "a.txt", "text/plain", b"secret").json()
    r = client.post("/api/ai/chat", headers=h2,
                    json={"prompt": "?", "upload_ids": [up["upload_id"]]})
    assert r.status_code == 400


def test_chat_without_files_unchanged(monkeypatch):
    """Главное требование: текст без файлов ходит ровно как раньше."""
    h, _ = _auth()
    _configure(h)
    _script_provider(monkeypatch, "Привет!")
    r = client.post("/api/ai/chat", headers=h, json={"prompt": "привет"})
    assert r.status_code == 200
    events = [json.loads(ln) for ln in r.text.splitlines() if ln.strip()]
    assert any(e["type"] == "text" and e["delta"] == "Привет!" for e in events)
    assert events[-1]["type"] == "done"


def test_chat_legacy_inline_attachment_still_works(monkeypatch):
    """Старый путь (base64 в теле) не сломан — на нём сидят маленькие файлы и
    любой не обновившийся клиент."""
    h, _ = _auth()
    _configure(h)
    seen: dict = {}

    async def fake(config, key, messages, with_tools=True, system="", **kw):
        seen["messages"] = messages
        return {"text": "ок", "tool_calls": [], "raw": {}}
    monkeypatch.setattr(ai_agent, "_provider_turn", fake)

    r = client.post("/api/ai/chat", headers=h, json={
        "prompt": "смотри", "session_id": "leg",
        "attachments": [{"name": "a.log", "mime": "text/plain",
                         "text": "ERROR unique-marker", "data_b64": ""}],
    })
    assert r.status_code == 200
    assert "unique-marker" in json.dumps(seen["messages"], ensure_ascii=False)


def test_chat_too_many_files_rejected(monkeypatch):
    h, _ = _auth()
    _configure(h)
    _script_provider(monkeypatch)
    ids = [_upload(h, f"f{i}.txt", "text/plain", b"data").json()["upload_id"]
           for i in range(ai_agent.MAX_ATTACHMENTS)]
    r = client.post("/api/ai/chat", headers=h, json={
        "prompt": "?", "upload_ids": ids,
        "attachments": [{"name": "x.log", "mime": "text/plain", "text": "t",
                         "data_b64": ""}],
    })
    assert r.status_code == 400


# ── хранение: путь, TTL, вытеснение ──────────────────────────
def test_stored_under_account_ai_uploads_dir():
    from app.services import accounts
    h, aid = _auth()
    up = _upload(h, "a.txt", "text/plain", b"payload").json()
    d = accounts.data_dir(aid) / "ai_uploads"
    assert (d / f"{up['upload_id']}.bin").read_bytes() == b"payload"
    meta = json.loads((d / f"{up['upload_id']}.json").read_text(encoding="utf-8"))
    assert meta["name"] == "a.txt" and meta["size"] == 7


def test_expired_upload_is_gone(monkeypatch):
    from app.services import accounts
    h, aid = _auth()
    up = _upload(h, "a.txt", "text/plain", b"payload").json()
    d = accounts.data_dir(aid) / "ai_uploads"
    p = d / f"{up['upload_id']}.json"
    info = json.loads(p.read_text(encoding="utf-8"))
    info["ts"] = time.time() - ai_uploads.TTL_SECONDS - 60
    p.write_text(json.dumps(info), encoding="utf-8")
    assert ai_uploads.get(up["upload_id"], aid) is None
    assert not (d / f"{up['upload_id']}.bin").exists()


def test_purge_drops_expired_on_next_upload(monkeypatch):
    from app.services import accounts
    h, aid = _auth()
    old = _upload(h, "old.txt", "text/plain", b"old").json()
    d = accounts.data_dir(aid) / "ai_uploads"
    p = d / f"{old['upload_id']}.json"
    info = json.loads(p.read_text(encoding="utf-8"))
    info["ts"] = time.time() - ai_uploads.TTL_SECONDS - 60
    p.write_text(json.dumps(info), encoding="utf-8")
    _upload(h, "new.txt", "text/plain", b"new")
    assert not (d / f"{old['upload_id']}.bin").exists()


def test_per_account_cap_evicts_oldest(monkeypatch):
    from app.services import accounts
    h, aid = _auth()
    monkeypatch.setattr(ai_uploads, "MAX_FILES_PER_ACCOUNT", 3)
    ids = []
    for i in range(5):
        ids.append(_upload(h, f"f{i}.txt", "text/plain", f"d{i}".encode()).json()["upload_id"])
        # Метки времени должны различаться, иначе «самый старый» не определён.
        p = accounts.data_dir(aid) / "ai_uploads" / f"{ids[-1]}.json"
        info = json.loads(p.read_text(encoding="utf-8"))
        info["ts"] = time.time() - (100 - i)
        p.write_text(json.dumps(info), encoding="utf-8")
    ai_uploads.purge(aid)
    assert ai_uploads.get(ids[0], aid) is None
    assert ai_uploads.get(ids[-1], aid) is not None


def test_traversal_id_rejected():
    h, aid = _auth()
    assert ai_uploads.get("../../../etc/passwd", aid) is None
    assert ai_uploads.get("", aid) is None
