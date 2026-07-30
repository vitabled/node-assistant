"""Вложения чата, живущие дольше одного сообщения.

Регрессия на настоящую поломку: пользователь приложил каталог, агент упёрся в
лимит шагов, пользователь написал «Продолжи» — и файла уже не было. Агент шёл
искать данные в заметках и отвечал чепухой.
"""
import asyncio
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.settings import AiConfig
from app.services import ai_agent, ai_attachments, users

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean():
    ai_attachments._STORE.clear()
    # ⚠️ `current_user` — ContextVar, и оставленное значение течёт в СЛЕДУЮЩИЕ
    # тесты процесса: на полном прогоне из-за этого падал `test_api_tokens`
    # (список токенов фильтруется по личности). Возвращаем как было.
    token = users.current_user.set(None)
    yield
    users.current_user.reset(token)
    ai_attachments._STORE.clear()


def _new_user() -> dict:
    """Пользователь через тестовый шим (см. conftest): `bootstrap` работает
    только на пустом реестре, а к этому моменту его уже наполнили соседи."""
    login = f"att-{uuid.uuid4().hex[:8]}"
    r = client.post("/api/auth/register", json={"login": login, "password": "pw"})
    assert r.status_code == 201, r.text
    return users.get(r.json()["id"]) or {}


def _items(name: str = "catalog.html", text: str = "данные"):
    return [{"name": name, "text": text, "images": []}]


def test_recall_returns_what_was_remembered_and_is_scoped():
    ai_attachments.remember("u1", "s1", _items())
    assert [a["name"] for a in ai_attachments.recall("u1", "s1")] == ["catalog.html"]
    # Другой разговор и другой человек — чужого файла не видят.
    assert ai_attachments.recall("u1", "s2") == []
    assert ai_attachments.recall("u2", "s1") == []


def test_empty_items_do_not_erase_what_is_remembered():
    """Следующее сообщение приходит БЕЗ файла — и не должно его обнулять."""
    ai_attachments.remember("u1", "s1", _items())
    ai_attachments.remember("u1", "s1", [])
    assert len(ai_attachments.recall("u1", "s1")) == 1


def test_stale_sessions_expire():
    ai_attachments.remember("u1", "s1", _items())
    ai_attachments._STORE[("u1", "s1")]["at"] = time.time() - ai_attachments.TTL_SECONDS - 1
    assert ai_attachments.recall("u1", "s1") == []
    assert ("u1", "s1") not in ai_attachments._STORE, "протухшее вычищается"


def test_a_user_keeps_only_the_last_few_conversations():
    """Иначе один человек с вкладками съедает память процесса."""
    now = time.time()
    for i in range(ai_attachments.MAX_SESSIONS_PER_USER + 2):
        ai_attachments.remember("u1", f"s{i}", _items())
        # Явный порядок вместо часов: одинаковая метка времени сделала бы
        # вытеснение зависящим от их разрешения. Метки берём БЛИЗКИЕ к сейчас —
        # далёкое прошлое вычистилось бы раньше как протухшее, и проверялось бы
        # не то правило.
        ai_attachments._STORE[("u1", f"s{i}")]["at"] = now - 100 + i
        ai_attachments._evict_locked(now)
    alive = [k[1] for k in ai_attachments._STORE if k[0] == "u1"]
    assert len(alive) == ai_attachments.MAX_SESSIONS_PER_USER
    assert "s0" not in alive and f"s{ai_attachments.MAX_SESSIONS_PER_USER + 1}" in alive


def test_attachment_survives_the_next_message_of_the_same_conversation(monkeypatch):
    """Сквозная проверка через run_agent: второе сообщение приходит без файла."""
    u = _new_user()
    users.current_user.set(u)
    seen: list[list[str]] = []

    async def fake_turn(config, key, messages, with_tools=True, system="",
                        mcp=None, ctx=None):
        seen.append([a["name"] for a in (ctx.attachments if ctx else [])])
        return {"text": "ок", "tool_calls": [], "raw": {}, "usage": 10}

    monkeypatch.setattr(ai_agent, "_provider_turn", fake_turn)
    cfg = AiConfig(enabled=True, api_key_enc=ai_agent.encrypt_key("sk"), max_steps=2)
    ws = u["workspace_id"]
    att = [{"name": "catalog.html", "mime": "text/html", "text": "D=[…]", "images": []}]

    async def main():
        async for _ in ai_agent.run_agent("перенеси", cfg, ws, attachments=att,
                                          session_id="s1"):
            pass
        async for _ in ai_agent.run_agent("Продолжи", cfg, ws, attachments=[],
                                          session_id="s1"):
            pass
        async for _ in ai_agent.run_agent("привет", cfg, ws, attachments=[],
                                          session_id="другой"):
            pass

    asyncio.run(main())
    assert seen[0] == ["catalog.html"]
    assert seen[1] == ["catalog.html"], "«Продолжи» обязано видеть тот же файл"
    assert seen[2] == [], "в другом разговоре файла быть не должно"


def test_empty_model_answer_is_named_not_silent(monkeypatch):
    """Пустой пузырь не отличить от зависшего агента — молчание надо назвать."""
    u = _new_user()
    users.current_user.set(u)

    async def mute(config, key, messages, with_tools=True, system="", mcp=None, ctx=None):
        return {"text": "", "tool_calls": [], "raw": {}, "usage": 0}

    monkeypatch.setattr(ai_agent, "_provider_turn", mute)
    cfg = AiConfig(enabled=True, api_key_enc=ai_agent.encrypt_key("sk"), max_steps=2)

    async def main():
        return [e async for e in ai_agent.run_agent("вопрос", cfg,
                                                    u["workspace_id"])]

    events = asyncio.run(main())
    texts = [e["delta"] for e in events if e["type"] == "text"]
    assert texts and "пустой ответ" in texts[0]
    assert events[-1]["type"] == "done"
