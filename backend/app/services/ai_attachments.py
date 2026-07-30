"""Вложения чата, живущие ДОЛЬШЕ одного сообщения.

Симптом, ради которого модуль появился: пользователь прикладывает каталог,
ассистент упирается в лимит шагов, пользователь пишет «Продолжи» — и файла уже
нет. Агент честно идёт искать данные там, где их нет (в заметках), и отвечает
чепухой. Вложение относилось к ОДНОМУ запросу, а работа с ним по своей природе
занимает несколько.

Почему в памяти, а не на диске:

* вложение эфемерно по смыслу — это не документ аккаунта, а материал разговора,
  и складывать 22 МБ в данные пользователя (да ещё переживать перезапуск) значит
  превращать чат в файловое хранилище, которого у него нет;
* чат всегда выполняется в gateway-процессе (в очередь уходят только деплои,
  см. §10d), поэтому общая память здесь достижима;
* перезапуск бэкенда теряет вложение — и это приемлемо: пользователь приложит
  файл заново, а вот вечно растущий каталог мусора приемлем не был бы.

Границы: TTL, потолок на пользователя и общий потолок памяти. Без них один
человек с несколькими вкладками съедает всю память процесса.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional

#: Сколько живёт набор вложений разговора без обращений.
TTL_SECONDS = 2 * 60 * 60

#: Сколько разговоров с вложениями помним одному пользователю. Дальше вытесняем
#: самый давний: держать все — значит расти без предела.
MAX_SESSIONS_PER_USER = 3

#: Общий потолок памяти под вложения. 22-мегабайтный каталог с картинками весит
#: ~45 МБ в base64, поэтому запас нужен ощутимый, но не бесконечный.
MAX_TOTAL_BYTES = 400 * 1024 * 1024

_LOCK = threading.Lock()
#: (user_id, session_id) → {"items": [...], "at": ts, "bytes": n}
_STORE: dict[tuple[str, str], dict[str, Any]] = {}


def _weigh(items: list[dict]) -> int:
    total = 0
    for it in items:
        total += len(it.get("text") or "")
        for img in it.get("images") or []:
            total += len(img.get("data_b64") or "")
    return total


def _total_bytes() -> int:
    return sum(int(v.get("bytes") or 0) for v in _STORE.values())


def _evict_locked(now: float) -> None:
    """Чистка под замком: протухшее, лишние разговоры пользователя, перебор по
    памяти. Порядок именно такой — сперва бесплатное, потом болезненное."""
    for key, val in list(_STORE.items()):
        if now - float(val.get("at") or 0) > TTL_SECONDS:
            _STORE.pop(key, None)

    by_user: dict[str, list[tuple[float, tuple[str, str]]]] = {}
    for key, val in _STORE.items():
        by_user.setdefault(key[0], []).append((float(val.get("at") or 0), key))
    for _uid, rows in by_user.items():
        rows.sort()
        for _at, key in rows[:-MAX_SESSIONS_PER_USER] if len(rows) > MAX_SESSIONS_PER_USER else []:
            _STORE.pop(key, None)

    while _total_bytes() > MAX_TOTAL_BYTES and _STORE:
        oldest = min(_STORE, key=lambda k: float(_STORE[k].get("at") or 0))
        _STORE.pop(oldest, None)


def remember(user_id: str, session_id: str, items: list[dict]) -> None:
    """Запомнить вложения разговора. Пустой список НЕ стирает запомненное —
    иначе следующее сообщение без файла обнуляло бы работу над ним."""
    if not user_id or not session_id or not items:
        return
    now = time.time()
    with _LOCK:
        _STORE[(user_id, session_id)] = {"items": items, "at": now,
                                         "bytes": _weigh(items)}
        _evict_locked(now)


def recall(user_id: str, session_id: str) -> list[dict]:
    """Вложения разговора, если они ещё живы. Обращение продлевает жизнь."""
    if not user_id or not session_id:
        return []
    now = time.time()
    with _LOCK:
        rec = _STORE.get((user_id, session_id))
        if rec is None:
            return []
        if now - float(rec.get("at") or 0) > TTL_SECONDS:
            _STORE.pop((user_id, session_id), None)
            return []
        rec["at"] = now
        return list(rec.get("items") or [])


def forget(user_id: str, session_id: str) -> None:
    with _LOCK:
        _STORE.pop((user_id, session_id), None)


def stats() -> dict:
    with _LOCK:
        return {"sessions": len(_STORE), "bytes": _total_bytes()}
