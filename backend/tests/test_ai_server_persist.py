"""Сервер САМ сохраняет результат ответа — «отправил → закрыл сайт → вернулся».

Предмет файла — дыра, из-за которой durable-переписка не работала ровно в том
сценарии, ради которого заводилась фоновая задача: писал в `ai_chat_store`
ТОЛЬКО браузер (`aiRunner.ts`, `finally`). Закрыл вкладку до конца ответа —
агент доработал, а записать результат оказалось некому.

Проверяем три свойства:

  * вопрос сохранён В МОМЕНТ ЗАПУСКА, ответ — ПО ЗАВЕРШЕНИИ, и обе записи
    делает сервер, без единого запроса от клиента;
  * ошибка — тоже результат: в истории должно быть видно, ПОЧЕМУ не вышло, а
    не пусто;
  * запись ИДЕМПОТЕНТНА: клиент пишет то же самое, и второй экземпляр реплики
    появиться не должен.
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import (ai_agent, ai_chat_persist, ai_chat_store, ai_runs,
                          storage)

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean():
    ai_runs._RUNS.clear()
    yield
    ai_runs._RUNS.clear()


def _auth() -> tuple[dict, str]:
    login = f"persist_{uuid.uuid4().hex[:8]}"
    r = client.post("/api/auth/register", json={"login": login, "password": "pw-1"})
    assert r.status_code == 201, r.text
    body = r.json()
    return {"Authorization": f"Bearer {body['token']}"}, body["id"]


def _configure(h) -> None:
    r = client.post("/api/ai/config", headers=h, json={
        "enabled": True, "provider": "openai",
        "base_url": "https://mock.example/v1", "model": "gpt-x",
        "api_key": "sk-test", "max_steps": 4,
    })
    assert r.status_code == 200, r.text


def _script(monkeypatch, turns) -> None:
    """Тот же приём, что в test_ai.py: провайдер отвечает по сценарию."""
    state = {"i": 0}

    async def fake(config, key, messages, with_tools=True, system="", **kw):
        t = turns[min(state["i"], len(turns) - 1)]
        state["i"] += 1
        return t

    monkeypatch.setattr(ai_agent, "_provider_turn", fake)


def _history(h, session_id="default") -> list[dict]:
    r = client.get(f"/api/ai/chat/history?session_id={session_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["messages"]


# ── сборка реплики из буфера событий ───────────────────────────

def test_answer_is_assembled_exactly_like_the_browser_assembles_it():
    # Разойдись эти две сборки — восстановленная переписка отличалась бы от
    # той, что человек видел живьём.
    events = [
        {"type": "status", "phase": "thinking", "step": 1},
        {"type": "text", "delta": "У вас "},
        {"type": "tool_call", "id": "t1", "name": "list_rules"},
        {"type": "tool_result", "id": "t1", "name": "list_rules", "ok": True},
        {"type": "text", "delta": "12 нод."},
        {"type": "done"},
    ]
    assert ai_chat_persist.assistant_text(events) == "У вас 12 нод."
    assert ai_chat_persist.tool_chips(events) == [
        {"name": "list_rules", "id": "t1", "ok": True}]


def test_error_becomes_a_visible_part_of_the_reply():
    # «Пусто» и «написано, почему не вышло» — это разница между «панель потеряла
    # мою работу» и «агент не смог, вот причина».
    text = ai_chat_persist.assistant_text([
        {"type": "text", "delta": "начал"},
        {"type": "error", "message": "Провайдер отклонил ключ."},
    ])
    assert text == "начал\n⚠️ Провайдер отклонил ключ."

    # Ошибка без единого токена текста — реплика ровно из неё.
    assert ai_chat_persist.assistant_text([
        {"type": "error", "message": "ИИ-агент выключен."},
    ]) == "⚠️ ИИ-агент выключен."


# ── ai_runs: колбэк завершения ─────────────────────────────────

def test_run_saves_the_result_once_even_though_finish_is_called_twice():
    """`finish` зовут и `finally` задачи, и `stop()` — колбэк обязан сработать
    ровно один раз, иначе в истории оказались бы две реплики."""
    calls: list[list[dict]] = []

    async def main():
        async def events():
            yield {"type": "text", "delta": "готово"}
            yield {"type": "done"}

        run = ai_runs.start("u1", "s1", events, calls.append)
        await asyncio.sleep(0.1)
        assert run.done
        run.finish()  # повторно — как это делает stop() после задачи
    asyncio.run(main())

    assert len(calls) == 1
    assert ai_chat_persist.assistant_text(calls[0]) == "готово"


def test_stop_still_saves_the_partial_answer():
    """Огрызок тоже результат: человек его уже видел."""
    calls: list[list[dict]] = []

    async def main():
        async def events():
            yield {"type": "text", "delta": "успел написать"}
            await asyncio.sleep(5)
            yield {"type": "done"}

        ai_runs.start("u1", "s1", events, calls.append)
        await asyncio.sleep(0.05)
        assert ai_runs.stop("u1", "s1") is True
        await asyncio.sleep(0.05)
    asyncio.run(main())

    assert len(calls) == 1
    assert "успел написать" in ai_chat_persist.assistant_text(calls[0])


def test_a_failing_callback_never_breaks_the_conversation():
    """Иначе разговор навсегда остался бы «идущим» — цена куда выше, чем
    несохранённая реплика."""
    async def main():
        async def events():
            yield {"type": "done"}

        def boom(_evs):
            raise RuntimeError("диск полон")

        run = ai_runs.start("u1", "s1", events, boom)
        await asyncio.sleep(0.1)
        assert run.done
        assert ai_runs.active("u1", "s1") is False
    asyncio.run(main())


def test_runs_without_a_callback_work_as_before():
    """Регрессия: `on_done` необязателен (его нет у резюме и у тестов)."""
    async def main():
        async def events():
            yield {"type": "text", "delta": "x"}
            yield {"type": "done"}

        run = ai_runs.start("u1", "s1", events)
        await asyncio.sleep(0.1)
        assert run.done and len(run.events) == 2
    asyncio.run(main())


# ── идемпотентность стора ──────────────────────────────────────

def test_append_once_ignores_the_duplicate_from_the_other_writer():
    h, aid = _auth()
    assert ai_chat_store.append_once(aid, "default", "assistant", "12 нод.") is True
    # Ровно то же самое пишет браузер.
    assert ai_chat_store.append_once(aid, "default", "assistant", "12 нод.") is False
    msgs = ai_chat_store.get_session(aid, "default")["messages"]
    assert [m["content"] for m in msgs] == ["12 нод."]


def test_append_once_upgrades_a_partial_reply_instead_of_adding_a_second():
    """Браузер успел сохранить огрызок раньше, чем сервер дописал ответ."""
    h, aid = _auth()
    ai_chat_store.append_once(aid, "default", "assistant", "У вас ")
    assert ai_chat_store.append_once(aid, "default", "assistant", "У вас 12 нод.") is True

    msgs = ai_chat_store.get_session(aid, "default")["messages"]
    assert [m["content"] for m in msgs] == ["У вас 12 нод."]


def test_append_once_keeps_a_genuine_repeat_of_the_same_question():
    """«Продолжи» дважды — это ДВА вопроса: их разделяет ответ ассистента."""
    h, aid = _auth()
    ai_chat_store.append_once(aid, "default", "user", "Продолжи")
    ai_chat_store.append_once(aid, "default", "assistant", "продолжил")
    ai_chat_store.append_once(aid, "default", "user", "Продолжи")

    msgs = ai_chat_store.get_session(aid, "default")["messages"]
    assert [m["content"] for m in msgs] == ["Продолжи", "продолжил", "Продолжи"]


def test_append_once_drops_empty_replies():
    h, aid = _auth()
    assert ai_chat_store.append_once(aid, "default", "assistant", "") is False
    assert ai_chat_store.append_once(aid, "default", "assistant", "   \n") is False
    assert ai_chat_store.get_session(aid, "default")["messages"] == []


def test_append_once_is_isolated_per_account():
    _, a = _auth()
    _, b = _auth()
    ai_chat_store.append_once(a, "default", "user", "секрет аккаунта A")
    assert ai_chat_store.get_session(b, "default")["messages"] == []


# ── сквозной сценарий: без браузера ────────────────────────────

def test_the_whole_conversation_is_saved_without_a_single_client_write(monkeypatch):
    """ГЛАВНЫЙ тест: отправил → закрыл сайт → через сутки открыл.

    Ни одного POST /api/ai/chat/history здесь нет: всё, что окажется в истории,
    записал сервер сам.
    """
    h, _ = _auth()
    _configure(h)
    _script(monkeypatch, [
        {"text": "У вас 12 нод.", "tool_calls": [], "raw": {"role": "assistant"}}])

    r = client.post("/api/ai/chat", headers=h,
                    json={"prompt": "сколько нод?", "session_id": "default"})
    assert r.status_code == 200
    assert [json.loads(x) for x in r.text.splitlines() if x.strip()][-1]["type"] == "done"

    # ...сутки спустя, свежая вкладка: GET /api/ai/chat/history.
    msgs = _history(h)
    assert [(m["role"], m["content"]) for m in msgs] == [
        ("user", "сколько нод?"), ("assistant", "У вас 12 нод."),
    ]


def test_the_question_is_saved_before_the_answer_even_starts(monkeypatch):
    """Долгий ответ — это и есть тот момент, когда вкладку закрывают."""
    h, aid = _auth()
    _configure(h)
    seen: dict = {}

    async def slow(config, key, messages, with_tools=True, system="", **kw):
        # К этому моменту вопрос обязан уже лежать на диске.
        seen["at_start"] = [m["content"] for m in
                            ai_chat_store.get_session(aid, "default")["messages"]]
        return {"text": "ответ", "tool_calls": [], "raw": {}}

    monkeypatch.setattr(ai_agent, "_provider_turn", slow)
    client.post("/api/ai/chat", headers=h,
                json={"prompt": "долгий вопрос", "session_id": "default"})
    assert seen["at_start"] == ["долгий вопрос"]


def test_a_failed_run_is_saved_too_instead_of_leaving_an_empty_history(monkeypatch):
    h, _ = _auth()
    _configure(h)

    async def boom(config, key, messages, with_tools=True, system="", **kw):
        raise ai_agent.AgentError("Провайдер отклонил ключ (401/403).")

    monkeypatch.setattr(ai_agent, "_provider_turn", boom)
    client.post("/api/ai/chat", headers=h,
                json={"prompt": "сломай", "session_id": "default"})

    msgs = _history(h)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert "⚠️" in msgs[1]["content"] and "ключ" in msgs[1]["content"]


def test_a_disabled_agent_does_not_write_anything(monkeypatch):
    """Агент выключен — работы не было, и истории браться неоткуда."""
    h, _ = _auth()
    client.post("/api/ai/chat", headers=h,
                json={"prompt": "привет", "session_id": "default"})
    assert _history(h) == []


def test_the_client_writing_the_same_reply_does_not_duplicate_it(monkeypatch):
    """Так ведёт себя ОТКРЫТАЯ вкладка: сервер уже записал, и клиент пишет то
    же самое из своего `finally`."""
    h, _ = _auth()
    _configure(h)
    _script(monkeypatch, [
        {"text": "У вас 12 нод.", "tool_calls": [], "raw": {"role": "assistant"}}])

    client.post("/api/ai/chat", headers=h,
                json={"prompt": "сколько нод?", "session_id": "default"})

    # Клиент дописывает ровно то же (append=true, как pushAppend).
    client.post("/api/ai/chat/history", headers=h, json={
        "session_id": "default", "append": True,
        "messages": [{"role": "assistant", "content": "У вас 12 нод."}]})

    msgs = _history(h)
    assert [(m["role"], m["content"]) for m in msgs] == [
        ("user", "сколько нод?"), ("assistant", "У вас 12 нод."),
    ]


def test_client_replace_after_the_server_write_stays_consistent(monkeypatch):
    """`pushReplace` (переподключение, `/compact`) перезаписывает разговор
    целиком — серверная запись при этом не удваивается, а замещается."""
    h, _ = _auth()
    _configure(h)
    _script(monkeypatch, [{"text": "ответ", "tool_calls": [], "raw": {}}])

    client.post("/api/ai/chat", headers=h,
                json={"prompt": "вопрос", "session_id": "default"})
    client.post("/api/ai/chat/history", headers=h, json={
        "session_id": "default",
        "messages": [{"role": "user", "content": "вопрос"},
                     {"role": "assistant", "content": "ответ"}]})

    msgs = _history(h)
    assert [(m["role"], m["content"]) for m in msgs] == [
        ("user", "вопрос"), ("assistant", "ответ")]


def test_two_sessions_do_not_mix(monkeypatch):
    h, _ = _auth()
    _configure(h)
    _script(monkeypatch, [{"text": "ага", "tool_calls": [], "raw": {}}])

    client.post("/api/ai/chat", headers=h, json={"prompt": "в A", "session_id": "a"})
    client.post("/api/ai/chat", headers=h, json={"prompt": "в B", "session_id": "b"})

    assert [m["content"] for m in _history(h, "a")] == ["в A", "ага"]
    assert [m["content"] for m in _history(h, "b")] == ["в B", "ага"]


def test_tool_chips_survive_into_the_saved_reply(monkeypatch):
    """Значки инструментов нужны ГЛАЗУ: без них восстановленная переписка
    выглядела бы беднее той, что была на экране."""
    h, _ = _auth()
    _configure(h)
    _script(monkeypatch, [
        {"text": "", "tool_calls": [{"id": "t1", "name": "list_rules", "args": {}}],
         "raw": {"role": "assistant"}},
        {"text": "У вас 0 правил.", "tool_calls": [], "raw": {"role": "assistant"}},
    ])

    client.post("/api/ai/chat", headers=h,
                json={"prompt": "правила?", "session_id": "default"})

    reply = _history(h)[-1]
    assert reply["role"] == "assistant"
    assert any(t["name"] == "list_rules" for t in reply.get("tools", []))
