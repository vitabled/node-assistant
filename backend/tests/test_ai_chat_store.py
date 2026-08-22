"""Durable-переписка ассистента: `services/ai_chat_store` + `api/ai_history`.

Главный предмет файла — не «функции возвращают что положили», а три свойства,
ради которых модуль и появился:

  * запись ПЕРЕЖИВАЕТ рестарт процесса (проверяем чтением с чистого модуля —
    состояния в памяти у стора нет по построению, поэтому достаточно убедиться,
    что данные лежат в каталоге аккаунта);
  * переписка одного аккаунта НЕ видна из другого (в ней содержимое заметок и
    ответы ручек — это персональные данные, а не кэш);
  * параллельные записи не теряют реплик: писать в один разговор могут разом
    фоновая задача ответа и HTTP-запрос пользователя.
"""
from __future__ import annotations

import json
import threading
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.services import accounts, ai_chat_store

client = TestClient(app)


def _auth() -> tuple[dict, str]:
    login = f"chat_{uuid.uuid4().hex[:8]}"
    r = client.post("/api/auth/register", json={"login": login, "password": "pw-1"})
    assert r.status_code == 201, r.text
    body = r.json()
    return {"Authorization": f"Bearer {body['token']}"}, body["id"]


def _acc() -> str:
    """Аккаунт без HTTP — для тестов самого стора."""
    return _auth()[1]


# ── стор ───────────────────────────────────────────────────────

def test_append_get_and_clear_round_trip():
    aid = _acc()
    assert ai_chat_store.get_session(aid, "default")["messages"] == []

    ai_chat_store.append_message(aid, "default", "user", "сколько нод?")
    ai_chat_store.append_message(aid, "default", "assistant", "12 нод.")

    s = ai_chat_store.get_session(aid, "default")
    assert [(m["role"], m["content"]) for m in s["messages"]] == [
        ("user", "сколько нод?"), ("assistant", "12 нод."),
    ]
    assert all(m["ts"] > 0 for m in s["messages"])

    assert ai_chat_store.clear_session(aid, "default") is True
    assert ai_chat_store.get_session(aid, "default")["messages"] == []
    # Повторная очистка — не ошибка, но и не «что-то удалили».
    assert ai_chat_store.clear_session(aid, "default") is False


def test_missing_session_reads_empty_instead_of_raising():
    # Клиент спрашивает про активный разговор ДО первого сообщения в нём:
    # ветка «сессии нет» не нужна ни ему, ни нам.
    aid = _acc()
    s = ai_chat_store.get_session(aid, "не было такой")
    assert s == {"session_id": "не было такой", "messages": [], "updated_at": 0}


def test_blank_session_id_means_default():
    aid = _acc()
    ai_chat_store.append_message(aid, "", "user", "привет")
    assert ai_chat_store.get_session(aid, "default")["messages"][0]["content"] == "привет"


def test_survives_a_restart_because_it_lives_in_the_account_dir():
    # Ровно та беда, ради которой всё делалось: `ai_runs` держит ответ в памяти
    # процесса, и рестарт бэкенда его обнуляет. Здесь данные обязаны лежать на
    # диске, в каталоге аккаунта.
    aid = _acc()
    ai_chat_store.append_message(aid, "default", "user", "переживи рестарт")

    path = accounts.account_dir(aid) / ai_chat_store.FILE_NAME
    assert path.exists()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["sessions"][0]["messages"][0]["content"] == "переживи рестарт"


def test_ui_extras_are_kept_but_junk_is_dropped():
    # Имена вложений и значки инструментов нужны ГЛАЗУ: без них восстановленная
    # переписка выглядела бы беднее той, что была до чистки браузера.
    aid = _acc()
    junk: list = [
        {"role": "user", "content": "разбери лог", "files": ["server.log"]},
        {"role": "assistant", "content": "готово",
         "tools": [{"id": "1", "name": "read_attachment", "ok": True}]},
        {"role": "system", "content": "не наша роль"},   # роль не из ROLES
        {"role": "user", "content": 42},                  # не строка
        "мусор",                                          # вообще не запись
    ]
    ai_chat_store.append_messages(aid, "default", junk)
    msgs = ai_chat_store.get_session(aid, "default")["messages"]
    assert len(msgs) == 2
    assert msgs[0]["files"] == ["server.log"]
    assert msgs[1]["tools"] == [{"name": "read_attachment", "id": "1", "ok": True}]


def test_message_limit_keeps_the_recent_tail():
    aid = _acc()
    ai_chat_store.append_messages(aid, "default", [
        {"role": "user", "content": f"q{i}"} for i in range(ai_chat_store.MAX_MESSAGES + 50)
    ])
    msgs = ai_chat_store.get_session(aid, "default")["messages"]
    assert len(msgs) == ai_chat_store.MAX_MESSAGES
    assert msgs[0]["content"] == "q50"
    assert msgs[-1]["content"] == f"q{ai_chat_store.MAX_MESSAGES + 49}"


def test_long_content_is_truncated_not_rejected():
    # Отказ означал бы потерю реплики целиком; обрезка теряет только хвост.
    aid = _acc()
    ai_chat_store.append_message(aid, "default", "assistant", "я" * 100_000)
    got = ai_chat_store.get_session(aid, "default")["messages"][0]["content"]
    assert len(got) == ai_chat_store.MAX_CONTENT_CHARS


def test_session_limit_evicts_the_stalest_and_never_the_open_one():
    aid = _acc()
    for i in range(ai_chat_store.MAX_SESSIONS + 4):
        ai_chat_store.append_message(aid, f"s{i}", "user", f"в разговоре {i}")

    ids = [s["session_id"] for s in ai_chat_store.list_sessions(aid)]
    assert len(ids) == ai_chat_store.MAX_SESSIONS
    # Тот, куда писали последним, обязан выжить: вытеснить его значило бы
    # потерять реплику ровно в момент её записи.
    assert f"s{ai_chat_store.MAX_SESSIONS + 3}" in ids
    assert "s0" not in ids


def test_replace_session_overwrites_and_obeys_the_cap():
    aid = _acc()
    ai_chat_store.append_message(aid, "default", "user", "старое")
    ai_chat_store.replace_session(aid, "default", [
        {"role": "user", "content": f"n{i}"} for i in range(ai_chat_store.MAX_MESSAGES + 10)
    ])
    msgs = ai_chat_store.get_session(aid, "default")["messages"]
    assert len(msgs) == ai_chat_store.MAX_MESSAGES
    assert all(m["content"] != "старое" for m in msgs)


def test_append_keeps_duplicates_by_default_and_folds_them_on_demand():
    # Дозапись — примитив: молча терять реплику она не вправе. Снятие повтора
    # включается явно и нужно ровно там, где одну реплику пишут двое (сервер по
    # завершении ответа и браузер) — см. `ai_chat_persist`.
    aid = _acc()
    ai_chat_store.append_message(aid, "default", "assistant", "ответ")
    ai_chat_store.append_message(aid, "default", "assistant", "ответ")
    assert len(ai_chat_store.get_session(aid, "default")["messages"]) == 2

    aid2 = _acc()
    ai_chat_store.append_messages(aid2, "default",
                                  [{"role": "assistant", "content": "ответ"}])
    ai_chat_store.append_messages(aid2, "default",
                                  [{"role": "assistant", "content": "ответ"}],
                                  dedup=True)
    assert len(ai_chat_store.get_session(aid2, "default")["messages"]) == 1


def test_accounts_are_isolated():
    a, b = _acc(), _acc()
    ai_chat_store.append_message(a, "default", "user", "секрет аккаунта A")
    assert ai_chat_store.get_session(b, "default")["messages"] == []
    assert ai_chat_store.list_sessions(b) == []


def test_concurrent_appends_lose_nothing():
    # Писать в один разговор могут разом фоновая задача ответа и HTTP-запрос
    # пользователя. Без блокировки read-modify-write терял бы реплики.
    aid = _acc()
    threads = [
        threading.Thread(target=ai_chat_store.append_message,
                         args=(aid, "default", "user", f"m{i}"))
        for i in range(24)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    msgs = ai_chat_store.get_session(aid, "default")["messages"]
    assert len(msgs) == 24
    assert {m["content"] for m in msgs} == {f"m{i}" for i in range(24)}


def test_corrupt_file_reads_as_empty_instead_of_locking_the_chat():
    aid = _acc()
    ai_chat_store.append_message(aid, "default", "user", "было")
    (accounts.account_dir(aid) / ai_chat_store.FILE_NAME).write_text(
        "{не json", encoding="utf-8")

    assert ai_chat_store.get_session(aid, "default")["messages"] == []
    # И запись после этого чинит файл, а не падает.
    ai_chat_store.append_message(aid, "default", "user", "стало")
    assert ai_chat_store.get_session(aid, "default")["messages"][0]["content"] == "стало"


# ── ручки ──────────────────────────────────────────────────────

def test_history_endpoints_round_trip():
    h, _ = _auth()

    r = client.get("/api/ai/chat/history?session_id=default", headers=h)
    assert r.status_code == 200
    assert r.json() == {"session_id": "default", "messages": [], "updated_at": 0}

    r = client.post("/api/ai/chat/history", headers=h, json={
        "session_id": "default",
        "messages": [{"role": "user", "content": "привет"},
                     {"role": "assistant", "content": "здравствуйте"}],
    })
    assert r.status_code == 200
    assert len(r.json()["messages"]) == 2

    r = client.get("/api/ai/chat/history?session_id=default", headers=h)
    assert [m["content"] for m in r.json()["messages"]] == ["привет", "здравствуйте"]

    r = client.delete("/api/ai/chat/history?session_id=default", headers=h)
    assert r.json() == {"cleared": True}
    assert client.get("/api/ai/chat/history?session_id=default",
                      headers=h).json()["messages"] == []


def test_post_replaces_by_default_and_appends_on_demand():
    h, _ = _auth()
    client.post("/api/ai/chat/history", headers=h, json={
        "session_id": "s1", "messages": [{"role": "user", "content": "первое"}]})

    # append=true — дописать (так фронт сохраняет каждую реплику по ходу).
    client.post("/api/ai/chat/history", headers=h, json={
        "session_id": "s1", "append": True,
        "messages": [{"role": "assistant", "content": "второе"}]})
    r = client.get("/api/ai/chat/history?session_id=s1", headers=h)
    assert [m["content"] for m in r.json()["messages"]] == ["первое", "второе"]

    # без append — перезаписать целиком (так фронт заливает миграцию).
    client.post("/api/ai/chat/history", headers=h, json={
        "session_id": "s1", "messages": [{"role": "user", "content": "только это"}]})
    r = client.get("/api/ai/chat/history?session_id=s1", headers=h)
    assert [m["content"] for m in r.json()["messages"]] == ["только это"]


def test_all_sessions_returns_every_conversation():
    # Этим клиент восстанавливается после чистки браузера: идентификаторов
    # разговоров он тогда не знает вовсе.
    h, _ = _auth()
    for sid in ("a", "b"):
        client.post("/api/ai/chat/history", headers=h, json={
            "session_id": sid, "messages": [{"role": "user", "content": f"в {sid}"}]})

    r = client.get("/api/ai/chat/history?all_sessions=true", headers=h)
    assert {s["session_id"] for s in r.json()["sessions"]} == {"a", "b"}

    r = client.get("/api/ai/chat/sessions", headers=h)
    assert {s["session_id"] for s in r.json()["sessions"]} == {"a", "b"}
    assert all(s["count"] == 1 for s in r.json()["sessions"])


def test_history_is_not_visible_across_accounts():
    h1, _ = _auth()
    h2, _ = _auth()
    client.post("/api/ai/chat/history", headers=h1, json={
        "session_id": "default", "messages": [{"role": "user", "content": "секрет"}]})

    assert client.get("/api/ai/chat/history?session_id=default",
                      headers=h2).json()["messages"] == []
    assert client.get("/api/ai/chat/sessions", headers=h2).json()["sessions"] == []


def test_history_requires_auth():
    assert client.get("/api/ai/chat/history").status_code == 401
    assert client.post("/api/ai/chat/history", json={"messages": []}).status_code == 401
    assert client.delete("/api/ai/chat/history").status_code == 401


def test_bad_role_is_rejected_by_the_schema():
    h, _ = _auth()
    r = client.post("/api/ai/chat/history", headers=h, json={
        "session_id": "default", "messages": [{"role": "system", "content": "x"}]})
    assert r.status_code == 422


def test_delete_all_needs_an_explicit_flag():
    # Стереть всю историю опечаткой в запросе должно быть НЕЛЬЗЯ.
    h, _ = _auth()
    for sid in ("a", "b"):
        client.post("/api/ai/chat/history", headers=h, json={
            "session_id": sid, "messages": [{"role": "user", "content": "x"}]})

    client.delete("/api/ai/chat/history", headers=h)  # без session_id → default
    assert len(client.get("/api/ai/chat/sessions", headers=h).json()["sessions"]) == 2

    client.delete("/api/ai/chat/history?all_sessions=true", headers=h)
    assert client.get("/api/ai/chat/sessions", headers=h).json()["sessions"] == []
