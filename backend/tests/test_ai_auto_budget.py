"""Авто-бюджет шагов и токенов встроенного AI-агента (Ф4).

`max_steps=0` — «авто»: агент идёт, пока есть прогресс (модель зовёт
инструменты и они срабатывают), и останавливается по одному из четырёх поводов:
финальный ответ без вызовов, 3 подряд безрезультатных шага, 3 подряд повтора
одного и того же вызова, предохранитель `AUTO_MAX_STEPS`.

`max_tokens=0` — «авто»: на обрыве по длине агент сам поднимает потолок ×1.5.

Провайдер всюду замокан (`_provider_turn`), как и в остальных ai-тестах.
"""

import json
import uuid

from fastapi.testclient import TestClient

from app.services import ai_agent
from app.main import app

client = TestClient(app)


def _auth():
    login = f"aib-{uuid.uuid4().hex[:8]}"
    r = client.post("/api/auth/register", json={"login": login, "password": "pw-1"})
    body = r.json()
    return {"Authorization": f"Bearer {body['token']}"}, body["id"]


def _configure(h, **over):
    body = {
        "enabled": True,
        "provider": "openai",
        "base_url": "https://mock.example/v1",
        "model": "gpt-x",
        "api_key": "sk-test",
    }
    body.update(over)
    return client.post("/api/ai/config", headers=h, json=body)


def _stream(h, prompt="сделай"):
    r = client.post("/api/ai/chat", headers=h, json={"prompt": prompt})
    assert r.status_code == 200
    return [json.loads(ln) for ln in r.text.splitlines() if ln.strip()]


def _script(monkeypatch, turns, sink=None):
    """`_provider_turn` отдаёт заготовленные тёрны по очереди (последний липнет)."""
    state = {"i": 0}

    async def fake(config, key, messages, with_tools=True, system="", **kw):
        if sink is not None:
            sink.append({"max_tokens": getattr(config, "max_tokens", None),
                         "with_tools": with_tools})
        t = turns[min(state["i"], len(turns) - 1)]
        state["i"] += 1
        return t

    monkeypatch.setattr(ai_agent, "_provider_turn", fake)
    return state


def _call(name="list_rules", args=None, tid="t"):
    return {"id": tid, "name": name, "args": args or {}}


def _turn(text="", calls=(), stop="", usage=0):
    return {"text": text, "tool_calls": list(calls), "raw": {"role": "assistant"},
            "usage": usage, "stop": stop}


def _texts(events):
    return " ".join(e["delta"] for e in events if e["type"] == "text")


# ── авто: нормальное завершение ───────────────────────────────
def test_auto_finishes_on_answer_without_tool_calls(monkeypatch):
    h, _ = _auth()
    _configure(h, max_steps=0)
    _script(monkeypatch, [_turn(text="Готово.")])
    events = _stream(h)
    assert _texts(events) == "Готово."
    # Никаких «предел исчерпан» — это штатное завершение.
    assert "предел" not in _texts(events) and "предохранитель" not in _texts(events)
    assert events[-1]["type"] == "done"
    # `steps=0` в статусе = авто; UI не должен врать про фиксированный бюджет.
    st = [e for e in events if e["type"] == "status"]
    assert st and all(e["steps"] == 0 and e["auto"] is True for e in st)


def test_auto_runs_many_steps_while_tools_progress(monkeypatch):
    h, _ = _auth()
    _configure(h, max_steps=0)
    # 5 разных (не повторяющихся!) вызовов, затем финальный ответ.
    turns = [_turn(calls=[_call(args={"n": i}, tid=f"t{i}")]) for i in range(5)]
    turns.append(_turn(text="Все 5 шагов сделаны."))
    _script(monkeypatch, turns)
    events = _stream(h)
    calls = [e for e in events if e["type"] == "tool_call"]
    assert len(calls) == 5, "авто-режим должен пройти все 5 продуктивных шагов"
    assert "Все 5 шагов сделаны." in _texts(events)
    assert events[-1]["type"] == "done"


# ── авто: остановка по отсутствию прогресса ───────────────────
def test_auto_stops_after_three_failing_steps(monkeypatch):
    h, _ = _auth()
    _configure(h, max_steps=0)
    # Несуществующий инструмент → ok=false на каждом шаге; аргументы РАЗНЫЕ,
    # чтобы сработал именно счётчик ошибок, а не детектор повторов.
    _script(monkeypatch, [
        _turn(calls=[_call(name="nope", args={"n": i}, tid=f"e{i}")])
        for i in range(10)
    ])
    events = _stream(h)
    assert all(e["ok"] is False for e in events if e["type"] == "tool_result")
    assert len([e for e in events if e["type"] == "tool_call"]) == ai_agent.AUTO_FAIL_STREAK
    assert "не продвигается" in _texts(events) and "ошибкой" in _texts(events)
    assert events[-1]["type"] == "done"


def test_auto_stops_on_repeated_identical_call(monkeypatch):
    h, _ = _auth()
    _configure(h, max_steps=0)
    # Один и тот же успешный вызов с теми же аргументами — цикл без продвижения.
    _script(monkeypatch, [_turn(calls=[_call(args={"same": 1}, tid="r")])])
    events = _stream(h)
    assert len([e for e in events if e["type"] == "tool_call"]) == \
        ai_agent.AUTO_REPEAT_STREAK - 1, "третий повтор не должен исполняться"
    assert "не продвигается" in _texts(events) and "повторил" in _texts(events)
    assert events[-1]["type"] == "done"


def test_auto_hits_absolute_step_fuse(monkeypatch):
    h, _ = _auth()
    _configure(h, max_steps=0)
    monkeypatch.setattr(ai_agent, "AUTO_MAX_STEPS", 4)
    # Успешные и КАЖДЫЙ РАЗ РАЗНЫЕ вызовы: ни ошибок, ни повторов — упирается
    # ровно в предохранитель.
    state = {"n": 0}

    async def fake(config, key, messages, with_tools=True, system="", **kw):
        state["n"] += 1
        return _turn(calls=[_call(args={"n": state["n"]}, tid=f"f{state['n']}")])

    monkeypatch.setattr(ai_agent, "_provider_turn", fake)
    events = _stream(h)
    assert len([e for e in events if e["type"] == "tool_call"]) == 4
    assert "предохранитель" in _texts(events) and "4 шагов" in _texts(events)
    assert events[-1]["type"] == "done"


# ── авто-токены ───────────────────────────────────────────────
def test_auto_tokens_grows_ceiling_and_continues(monkeypatch):
    h, _ = _auth()
    _configure(h, max_steps=0, max_tokens=0)
    seen: list[dict] = []
    # Первый тёрн обрывается по длине, второй — нормальный ответ.
    _script(monkeypatch, [
        _turn(stop="length"),
        _turn(text="Поместилось."),
    ], sink=seen)
    events = _stream(h)
    assert [s["max_tokens"] for s in seen] == [
        ai_agent.AUTO_TOKENS_START,
        int(ai_agent.AUTO_TOKENS_START * ai_agent.AUTO_TOKENS_GROWTH),
    ]
    # Пользователя НЕ просят лезть в настройки — агент справился сам.
    assert "Поместилось." in _texts(events)
    assert "поднимите" not in _texts(events)
    assert events[-1]["type"] == "done"


def test_auto_tokens_stops_at_ceiling(monkeypatch):
    h, _ = _auth()
    _configure(h, max_steps=0, max_tokens=0)
    seen: list[dict] = []
    _script(monkeypatch, [_turn(stop="length")], sink=seen)
    events = _stream(h)
    caps = [s["max_tokens"] for s in seen]
    assert caps[-1] == ai_agent.AUTO_TOKENS_CEILING, "рост должен упереться в потолок"
    assert caps == sorted(caps) and len(set(caps)) == len(caps), "строго вверх"
    assert "не поместился" in _texts(events)
    assert events[-1]["type"] == "done"


# ── ручной режим не изменился ─────────────────────────────────
def test_manual_mode_reserves_last_step_and_stops(monkeypatch):
    h, _ = _auth()
    _configure(h, max_steps=3, max_tokens=8192)
    seen: list[dict] = []
    # Провайдер ВСЕГДА зовёт инструмент → прежнее поведение: 3 шага, из них
    # последний с with_tools=False, затем сообщение о пределе.
    _script(monkeypatch, [_turn(calls=[_call(args={"same": 1}, tid="m")])],
            sink=seen)
    events = _stream(h)
    assert [s["with_tools"] for s in seen] == [True, True, False]
    # Повтор одного и того же вызова в ручном режиме НЕ прерывает цикл.
    assert len([e for e in events if e["type"] == "tool_call"]) == 3
    assert "достигнут предел в 3 шагов" in _texts(events)
    st = [e for e in events if e["type"] == "status"]
    assert all(e["steps"] == 3 and e["auto"] is False for e in st)
    assert events[-1]["type"] == "done"


def test_manual_tokens_still_asks_user(monkeypatch):
    h, _ = _auth()
    _configure(h, max_steps=2, max_tokens=8192)
    seen: list[dict] = []
    _script(monkeypatch, [_turn(stop="length")], sink=seen)
    events = _stream(h)
    assert len(seen) == 1, "ручной режим НЕ повторяет тёрн с бо́льшим потолком"
    assert "8192" in _texts(events) and "поднимите" in _texts(events)


# ── конфиг ────────────────────────────────────────────────────
def test_config_accepts_zero_and_reports_auto():
    h, _ = _auth()
    assert _configure(h, max_steps=0, max_tokens=0).status_code == 200
    cfg = client.get("/api/ai/config", headers=h).json()
    assert cfg["max_steps"] == 0 and cfg["max_tokens"] == 0
    assert cfg["auto_steps"] is True and cfg["auto_tokens"] is True
    assert cfg["auto_max_steps"] == ai_agent.AUTO_MAX_STEPS
    assert cfg["auto_token_budget"] == ai_agent.AUTO_TOKEN_BUDGET

    # Ручные значения продолжают отдаваться как раньше, с auto=false.
    assert _configure(h, max_steps=12, max_tokens=8192).status_code == 200
    cfg = client.get("/api/ai/config", headers=h).json()
    assert cfg["max_steps"] == 12 and cfg["max_tokens"] == 8192
    assert cfg["auto_steps"] is False and cfg["auto_tokens"] is False


def test_config_rejects_junk_token_ceiling():
    h, _ = _auth()
    # 0 — авто, 256..64000 — ручной коридор; между ними ответ обрывался бы
    # на каждом шаге, поэтому это по-прежнему ошибка.
    assert _configure(h, max_tokens=100).status_code == 422
    assert _configure(h, max_steps=-1).status_code == 422
    assert _configure(h, max_steps=61).status_code == 422
