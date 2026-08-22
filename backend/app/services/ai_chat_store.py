"""Durable-переписка ассистента на СЕРВЕРЕ, per-account.

Зачем вообще. До этого разговор жил ровно в двух местах, и оба временные:

  * `localStorage` браузера (`components/automation/aiSessions.ts`) — его чистит
    сам браузер. Safari в режиме «предотвращение отслеживания» стирает хранилище
    сайта, куда не заходили 7 дней; приватное окно не переживает закрытия
    вкладки; «очистить данные сайта» уносит его вместе с кэшем. Отсюда и жалоба
    «долго не заходил — история пропала»: терялась она НЕ у нас;
  * `services/ai_runs.py` — буфер идущего ответа в ПАМЯТИ процесса. Он лечит
    F5 и уход в другой раздел, но рестарт бэкенда его обнуляет, да и живёт он
    минуты (`DONE_TTL`).

Здесь — третье место, которое переживает и то, и другое: файл в каталоге
аккаунта. `ai_runs` остаётся как был (быстрый буфер живого ответа), этот модуль
хранит РЕЗУЛЬТАТ.

⚠️ JSON, а не sqlite — осознанно. Объём ограничен потолками ниже: 20 сессий ×
200 сообщений × 40 000 символов — это единицы мегабайт в худшем случае, а
типичная переписка (сообщение ≈ 1 КБ) укладывается в сотни килобайт. Запросов к
хранилищу два вида, и оба берут файл целиком (показать разговор / дописать
реплику) — индексы и частичное чтение sqlite тут не дают ничего, а стоят схемы,
миграций и отдельного соединения на поток. Пишем целиком под `Lock`, как
`accounts.py`: конкурируют максимум фоновая задача ответа и HTTP-запрос
пользователя, и им нужна не пропускная способность, а отсутствие гонки на
read-modify-write.

⚠️ Переписка — это ПЕРСОНАЛЬНЫЕ данные пользователя панели: в ней и содержимое
заметок, и ответы ручек. Поэтому файл лежит строго в `accounts/<id>/`, а не
глобально: изоляция аккаунтов здесь — не удобство, а требование (см. §1b
CLAUDE.md).
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Optional

from app.services import accounts

#: Имя файла в каталоге аккаунта. Рядом с settings.json и прочими — общий
#: паттерн `storage.py`.
FILE_NAME = "ai_chat.json"

#: Потолки. Совпадают с клиентскими (`aiSessions.ts`) НАМЕРЕННО: разойдись они —
#: сервер молча резал бы то, что клиент считает сохранённым, и «пропажа хвоста»
#: выглядела бы как баг синхронизации.
MAX_MESSAGES = 200
MAX_SESSIONS = 20
#: Потолок на одну реплику. Вложения в переписку не попадают (они эфемерны, см.
#: api/ai.py::Attachment), поэтому длинной бывает только простыня вывода
#: инструмента, которую ассистент пересказал в ответе.
MAX_CONTENT_CHARS = 40_000
MAX_SESSION_ID = 64

ROLES = ("user", "assistant")

# Одна блокировка на процесс, а не на аккаунт: запись — это чтение файла,
# правка списка и его сброс на диск, то есть микросекунды. Городить словарь
# блокировок по account_id значит завести ещё и гонку на его пополнение.
_LOCK = threading.Lock()


def _path(account_id: str) -> Path:
    # `account_dir`, а не `data_dir`: переписка принадлежит ЧЕЛОВЕКУ, а не
    # рабочей области внутри аккаунта. Иначе переключение инстанса выглядело бы
    # как потеря истории — ровно та беда, которую модуль и лечит.
    return accounts.account_dir(account_id) / FILE_NAME


def _read(account_id: str) -> dict:
    """Прочитать файл. Никогда не бросает: битый JSON (правка руками, обрыв
    записи на полном диске) не должен запирать чат навсегда — это всего лишь
    история, и пустая лучше пятисотки на каждой загрузке страницы."""
    try:
        p = _path(account_id)
        if p.exists():
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("sessions"), list):
                return raw
    except Exception:
        pass
    return {"sessions": []}


def _write(account_id: str, data: dict) -> None:
    p = _path(account_id)
    # Пишем через временный файл и переименовываем: `rename` в пределах одной
    # ФС атомарен, поэтому обрыв (kill -9, кончилось место) оставляет СТАРУЮ
    # переписку целой, а не обрубок, который потом не разберётся.
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def _clean_id(session_id: str) -> str:
    sid = (session_id or "").strip()[:MAX_SESSION_ID]
    # Пусто = «разговор по умолчанию»: у клиента до первого `/newsession`
    # никакого id может и не быть, а ветка «сессии нет» не нужна никому.
    return sid or "default"


def _norm_msg(raw: Any) -> Optional[dict]:
    """Одна реплика. Проверяем форму, а не доверяем ей: сюда приезжает тело
    HTTP-запроса, и файл правится руками."""
    if not isinstance(raw, dict):
        return None
    role = raw.get("role")
    if role not in ROLES:
        return None
    content = raw.get("content")
    if not isinstance(content, str):
        return None
    out: dict = {
        "role": role,
        "content": content[:MAX_CONTENT_CHARS],
        "ts": int(raw.get("ts") or time.time()),
    }
    # Довески UI сохраняем, если пришли. Они не нужны модели, но нужны ГЛАЗУ:
    # без них восстановленная с сервера переписка теряла бы имена вложений и
    # значки инструментов, то есть выглядела бы беднее той, что была до чистки
    # браузера — и человек решил бы, что история восстановилась не полностью.
    files = raw.get("files")
    if isinstance(files, list):
        names = [str(f)[:200] for f in files if isinstance(f, (str, int, float))]
        if names:
            out["files"] = names[:20]
    tools = raw.get("tools")
    if isinstance(tools, list):
        chips = []
        for t in tools[:50]:
            if isinstance(t, dict) and isinstance(t.get("name"), str):
                chip: dict = {"name": t["name"][:80]}
                if isinstance(t.get("id"), str):
                    chip["id"] = t["id"][:80]
                if isinstance(t.get("ok"), bool):
                    chip["ok"] = t["ok"]
                chips.append(chip)
        if chips:
            out["tools"] = chips
    return out


def _norm_session(raw: Any) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    sid = raw.get("session_id")
    if not isinstance(sid, str) or not sid.strip():
        return None
    msgs = [m for m in (_norm_msg(x) for x in (raw.get("messages") or [])) if m]
    return {
        "session_id": _clean_id(sid),
        # Режем С ГОЛОВЫ: свежие реплики нужнее — именно их читает и человек, и
        # модель в следующем ходе.
        "messages": msgs[-MAX_MESSAGES:],
        "updated_at": int(raw.get("updated_at") or time.time()),
    }


def _trim_sessions(sessions: list[dict], keep: str = "") -> list[dict]:
    """Вытеснить самые давно не тронутые. `keep` — разговор, который трогают
    прямо сейчас: вытеснить его значило бы потерять реплику в момент записи."""
    if len(sessions) <= MAX_SESSIONS:
        return sessions
    doomed = sorted((s for s in sessions if s["session_id"] != keep),
                    key=lambda s: s["updated_at"])[:len(sessions) - MAX_SESSIONS]
    dead = {id(s) for s in doomed}
    return [s for s in sessions if id(s) not in dead]


def _find(sessions: list[dict], sid: str) -> Optional[dict]:
    return next((s for s in sessions if s["session_id"] == sid), None)


# ── публичный интерфейс ────────────────────────────────────────

def list_sessions(account_id: str) -> list[dict]:
    """Оглавление БЕЗ реплик: список разговоров рисуется на каждой загрузке
    страницы, и тащить в него всю переписку незачем."""
    with _LOCK:
        data = _read(account_id)
    out = []
    for raw in data["sessions"]:
        s = _norm_session(raw)
        if s:
            out.append({"session_id": s["session_id"],
                        "updated_at": s["updated_at"],
                        "count": len(s["messages"])})
    out.sort(key=lambda s: s["updated_at"], reverse=True)
    return out


def all_sessions(account_id: str) -> list[dict]:
    """Всё целиком — для восстановления после чистки браузера. Порядок: свежие
    первыми, как и в оглавлении."""
    with _LOCK:
        data = _read(account_id)
    out = [s for s in (_norm_session(x) for x in data["sessions"]) if s]
    out.sort(key=lambda s: s["updated_at"], reverse=True)
    return out


def get_session(account_id: str, session_id: str) -> dict:
    """Одна переписка. Несуществующая — это ПУСТАЯ, а не ошибка: клиент
    спрашивает про свой активный разговор ещё до первого сообщения в нём."""
    sid = _clean_id(session_id)
    with _LOCK:
        data = _read(account_id)
    found = None
    for raw in data["sessions"]:
        s = _norm_session(raw)
        if s and s["session_id"] == sid:
            found = s
            break
    return found or {"session_id": sid, "messages": [], "updated_at": 0}


def append_message(account_id: str, session_id: str, role: str,
                   content: str, **extra: Any) -> dict:
    """Дописать реплику. Возвращает обновлённую сессию."""
    return append_messages(account_id, session_id,
                           [{"role": role, "content": content, **extra}])


def append_messages(account_id: str, session_id: str,
                    messages: list[dict]) -> dict:
    sid = _clean_id(session_id)
    incoming = [m for m in (_norm_msg(x) for x in messages) if m]
    with _LOCK:
        data = _read(account_id)
        sessions = [s for s in (_norm_session(x) for x in data["sessions"]) if s]
        cur = _find(sessions, sid)
        if cur is None:
            cur = {"session_id": sid, "messages": [], "updated_at": 0}
            sessions.append(cur)
        cur["messages"] = (cur["messages"] + incoming)[-MAX_MESSAGES:]
        cur["updated_at"] = int(time.time())
        sessions = _trim_sessions(sessions, keep=sid)
        _write(account_id, {"sessions": sessions})
        return dict(cur, messages=list(cur["messages"]))


def replace_session(account_id: str, session_id: str,
                    messages: list[dict]) -> dict:
    """Перезаписать переписку целиком. Этим фронт заливает то, что нашёл в
    localStorage, при первой миграции — и им же чинит расхождение после
    работы в двух вкладках."""
    sid = _clean_id(session_id)
    incoming = [m for m in (_norm_msg(x) for x in messages) if m][-MAX_MESSAGES:]
    with _LOCK:
        data = _read(account_id)
        sessions = [s for s in (_norm_session(x) for x in data["sessions"]) if s]
        cur = _find(sessions, sid)
        if cur is None:
            cur = {"session_id": sid, "messages": [], "updated_at": 0}
            sessions.append(cur)
        cur["messages"] = incoming
        cur["updated_at"] = int(time.time())
        sessions = _trim_sessions(sessions, keep=sid)
        _write(account_id, {"sessions": sessions})
        return dict(cur, messages=list(cur["messages"]))


def clear_session(account_id: str, session_id: str) -> bool:
    """Убрать разговор целиком. `True`, если он там был.

    Удаляем ЗАПИСЬ, а не оставляем пустую: пустая сессия вечно висела бы в
    оглавлении и занимала место под лимитом в 20 разговоров.
    """
    sid = _clean_id(session_id)
    with _LOCK:
        data = _read(account_id)
        sessions = [s for s in (_norm_session(x) for x in data["sessions"]) if s]
        rest = [s for s in sessions if s["session_id"] != sid]
        if len(rest) == len(sessions):
            return False
        _write(account_id, {"sessions": rest})
        return True


def clear_all(account_id: str) -> None:
    with _LOCK:
        _write(account_id, {"sessions": []})
