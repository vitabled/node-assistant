"""Параллельные записи в РАЗНЫЕ ресурсы панели (ускорение импорта).

Гонка возможна только за ОДИН файл стора, поэтому:
  * `/api/subnets` + `/api/hostings` за один тёрн — параллельно;
  * две записи в `/api/subnets` — строго по очереди (иначе теряются записи);
  * запись без внятного пути — в одиночку, ни с чем не параллелится.

Провайдер и `_run_tool` замоканы, как и в остальных ai-тестах; параллельность
меряем по времени (`asyncio.sleep`) и по перекрытию интервалов выполнения.
"""

import asyncio
import json
import time
import uuid

from fastapi.testclient import TestClient

from app.services import ai_agent
from app.main import app

client = TestClient(app)

SLEEP = 0.15


def _auth():
    login = f"aipw-{uuid.uuid4().hex[:8]}"
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
        "readonly": False,
    }
    body.update(over)
    return client.post("/api/ai/config", headers=h, json=body)


def _call(tid, name="panel_write", path=None, **args):
    a = dict(args)
    if path is not None:
        a["path"] = path
    return {"id": tid, "name": name, "args": a}


def _turn(text="", calls=()):
    return {"text": text, "tool_calls": list(calls), "raw": {"role": "assistant"},
            "usage": 0, "stop": ""}


def _script(monkeypatch, turns):
    state = {"i": 0}

    async def fake(config, key, messages, with_tools=True, system="", **kw):
        t = turns[min(state["i"], len(turns) - 1)]
        state["i"] += 1
        return t

    monkeypatch.setattr(ai_agent, "_provider_turn", fake)


def _slow_tool(monkeypatch, log):
    """`_run_tool` спит SLEEP и пишет интервалы выполнения по имени/пути."""

    async def fake(name, args, account_id, config=None, ctx=None):
        key = (args or {}).get("path") or name
        start = time.monotonic()
        await asyncio.sleep(SLEEP)
        log.append((key, start, time.monotonic()))
        return True, {"ok": key}

    monkeypatch.setattr(ai_agent, "_run_tool", fake)


def _run(h, monkeypatch, calls):
    _script(monkeypatch, [_turn(calls=calls), _turn(text="Готово.")])
    log = []
    _slow_tool(monkeypatch, log)
    t0 = time.monotonic()
    r = client.post("/api/ai/chat", headers=h, json={"prompt": "импортируй"})
    assert r.status_code == 200
    events = [json.loads(ln) for ln in r.text.splitlines() if ln.strip()]
    return events, log, time.monotonic() - t0


def _overlap(log):
    """Пересекаются ли по времени хоть какие-то два вызова."""
    for i in range(len(log)):
        for j in range(i + 1, len(log)):
            a, b = log[i], log[j]
            if a[1] < b[2] and b[1] < a[2]:
                return True
    return False


def _results(events):
    return {e["id"]: e["ok"] for e in events if e["type"] == "tool_result"}


# ── разные ресурсы: параллельно ───────────────────────────────
def test_writes_to_different_resources_run_in_parallel(monkeypatch):
    h, _ = _auth()
    _configure(h)
    events, log, elapsed = _run(h, monkeypatch, [
        _call("w1", path="/api/subnets", method="POST", body={}),
        _call("w2", path="/api/hostings", method="POST", body={}),
    ])
    assert len(log) == 2
    assert _overlap(log), "записи в разные ресурсы должны идти одновременно"
    assert elapsed < SLEEP * 2, f"суммарно {elapsed:.3f}s — похоже на очередь"
    # Результаты по-прежнему разложены по tc.id.
    assert _results(events) == {"w1": True, "w2": True}


def test_write_many_parallel_with_other_resource(monkeypatch):
    h, _ = _auth()
    _configure(h)
    events, log, elapsed = _run(h, monkeypatch, [
        _call("w1", name="panel_write_many", path="/api/subnets",
              method="POST", items=[{}, {}]),
        _call("w2", path="/api/hostings", method="POST", body={}),
    ])
    assert _overlap(log)
    assert elapsed < SLEEP * 2
    assert _results(events) == {"w1": True, "w2": True}


# ── один ресурс: по очереди ───────────────────────────────────
def test_writes_to_same_resource_run_sequentially(monkeypatch):
    h, _ = _auth()
    _configure(h)
    events, log, elapsed = _run(h, monkeypatch, [
        _call("w1", path="/api/subnets", method="POST", body={"a": 1}),
        _call("w2", path="/api/subnets/42", method="PATCH", body={"a": 2}),
    ])
    assert len(log) == 2
    assert not _overlap(log), "две записи в один стор — это гонка за файлом"
    assert elapsed >= SLEEP * 2 * 0.9
    assert _results(events) == {"w1": True, "w2": True}


# ── без пути: консервативно, в одиночку ───────────────────────
def test_write_without_path_is_not_parallelised(monkeypatch):
    h, _ = _auth()
    _configure(h)
    events, log, elapsed = _run(h, monkeypatch, [
        _call("w1", name="save_attachment_image", indices=[1]),
        _call("w2", path="/api/subnets", method="POST", body={}),
    ])
    assert len(log) == 2
    assert not _overlap(log)
    assert elapsed >= SLEEP * 2 * 0.9
    assert _results(events) == {"w1": True, "w2": True}


# ── чтения по-прежнему параллельны (регрессия) ────────────────
def test_reads_still_parallel(monkeypatch):
    h, _ = _auth()
    _configure(h)
    events, log, elapsed = _run(h, monkeypatch, [
        _call("r1", name="panel_get", path="/api/subnets"),
        _call("r2", name="panel_get", path="/api/subnets"),
    ])
    assert _overlap(log)
    assert elapsed < SLEEP * 2
    assert _results(events) == {"r1": True, "r2": True}


# ── раскладка волн (юнит) ─────────────────────────────────────
def test_write_waves_grouping():
    calls = [
        _call("a", path="/api/subnets"),
        _call("b", path="/api/hostings"),
        _call("c", path="/api/subnets/7"),
        _call("d", name="save_attachment_image"),
        _call("e", path="/api/rules"),
    ]
    waves = ai_agent._write_waves(calls)
    ids = [[[tc["id"] for tc in chain] for chain in wave] for wave in waves]
    # subnets-цепочка сохраняет порядок; hostings идёт рядом; барьер без пути
    # закрывает волну, всё после него — новая волна.
    assert ids == [[["a", "c"], ["b"]], [["d"]], [["e"]]]


def test_write_resource_parsing():
    assert ai_agent._write_resource(_call("x", path="/api/subnets")) == "subnets"
    assert ai_agent._write_resource(
        _call("x", path="/api/subnets/42?dry=1")) == "subnets"
    assert ai_agent._write_resource(_call("x", path="/api")) is None
    assert ai_agent._write_resource(_call("x", path="/subnets")) is None
    assert ai_agent._write_resource(_call("x")) is None
    assert ai_agent._write_resource({"id": "x", "name": "n", "args": None}) is None
