"""Сервер САМ сохраняет результат ответа в durable-переписку.

Зачем. `ai_chat_store` появился, чтобы переписка пережила чистку браузера, но
писал в него ТОЛЬКО браузер (`aiRunner.ts`, `finally` → `pushAppend`). Отсюда
дыра ровно в том сценарии, ради которого фоновая задача и заводилась:

    отправил вопрос → закрыл вкладку → через сутки открыл

Работа-то продолжалась на сервере (`ai_runs`), но записать её результат было
некому: клиент, который должен был это сделать, уже ушёл. Через сутки человек
видел пустую историю — притом что агент честно всё сделал.

Здесь эта запись переезжает на сервер:

  * вопрос сохраняется В МОМЕНТ ЗАПУСКА (`api/ai.py`, до `ai_runs.start`) —
    раньше, чем клиент вообще успеет что-либо отправить;
  * ответ сохраняется ПО ЗАВЕРШЕНИИ (`Run.finish` → `on_done`), из фоновой
    задачи, которой закрытая вкладка не мешает.

⚠️ Клиента при этом НЕ трогаем: он продолжает писать то же самое. Убрать его
запись было бы соблазнительно (одна сторона — нет дублей), но тогда любой
клиент старой версии, который остался открытым после обновления бэкенда, терял
бы отображаемый им огрызок; да и запись клиента точнее в одном месте — она
содержит то, что человек ВИДЕЛ. Поэтому обе стороны пишут, а дубли снимает
`ai_chat_store.append_once` (см. его док: сравнение с последней репликой по
роли и префиксу).

⚠️ Ошибки сохраняются наравне с ответами. «В истории пусто» и «в истории
написано, почему не вышло» — это разница между «панель потеряла мою работу» и
«агент не смог, вот причина».
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from app.services import ai_chat_store

log = logging.getLogger("ai_chat_persist")

#: Ошибки без текста ответа помечаем значком — тем же, что рисует клиент
#: (`aiRunner.ts`: `a.text += "\n⚠️ " + ev.message`), чтобы восстановленная с
#: сервера переписка выглядела ровно так же, как выглядела в браузере.
ERR_PREFIX = "⚠️ "


def assistant_text(events: Iterable[dict]) -> str:
    """Собрать финальную реплику из буфера событий.

    Ровно так же, как её собирает клиент: конкатенация `delta` у событий
    `text`, а `error` дописывается в конец с тем же значком. Разойдись эти две
    сборки — переписка после перезагрузки отличалась бы от той, что человек
    видел живьём.
    """
    parts: list[str] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        kind = ev.get("type")
        if kind == "text":
            delta = ev.get("delta")
            if isinstance(delta, str):
                parts.append(delta)
        elif kind == "error":
            msg = ev.get("message")
            if isinstance(msg, str) and msg.strip():
                # Перевод строки только если что-то уже написано: иначе реплика
                # начиналась бы с пустой строки.
                parts.append(("\n" if parts else "") + ERR_PREFIX + msg)
    return "".join(parts)


def tool_chips(events: Iterable[dict]) -> list[dict]:
    """Значки инструментов — довесок для ГЛАЗА, как в `ai_chat_store._norm_msg`.

    Без них восстановленная с сервера реплика выглядела бы беднее той, что была
    на экране: пропали бы отметки «сходил в панель», «поискал в вебе».
    """
    chips: list[dict] = []
    by_id: dict[str, dict] = {}
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "tool_call" and isinstance(ev.get("name"), str):
            chip: dict = {"name": ev["name"]}
            if isinstance(ev.get("id"), str):
                chip["id"] = ev["id"]
                by_id[ev["id"]] = chip
            chips.append(chip)
        elif ev.get("type") == "tool_result":
            target = by_id.get(ev.get("id") or "")
            if target is None:
                target = next((c for c in reversed(chips)
                               if c["name"] == ev.get("name") and "ok" not in c),
                              None)
            if target is not None and isinstance(ev.get("ok"), bool):
                target["ok"] = ev["ok"]
    return chips[:50]


def save_question(account_id: str, session_id: str, prompt: str,
                  files: list[str] | None = None) -> bool:
    """Сохранить вопрос при СТАРТЕ ответа.

    Именно при старте, а не по завершении: долгий ответ — это и есть тот
    момент, когда вкладку закрывают, и вопрос обязан пережить его независимо от
    того, чем кончится сам ответ.
    """
    if not account_id:
        # Без аккаунта писать некуда, а класть переписку в чужой каталог нельзя.
        return False
    extra: dict[str, Any] = {}
    if files:
        extra["files"] = files
    return _guard(ai_chat_store.append_once, account_id, session_id,
                  "user", prompt, **extra)


def save_answer(account_id: str, session_id: str, events: Iterable[dict]) -> bool:
    """Сохранить результат по завершении ответа. Вызывается из `Run.finish`."""
    if not account_id:
        return False
    evs = list(events)
    text = assistant_text(evs)
    if not text.strip():
        # Пустой ответ (например, отмена до первого токена) не сообщает ничего,
        # а место под лимитом в 200 реплик занимал бы.
        return False
    extra: dict[str, Any] = {}
    chips = tool_chips(evs)
    if chips:
        extra["tools"] = chips
    return _guard(ai_chat_store.append_once, account_id, session_id,
                  "assistant", text, **extra)


def _guard(fn, *a: Any, **kw: Any) -> bool:
    """Запись истории НИКОГДА не роняет ответ.

    Мы вызываемся из `finally` фоновой задачи: исключение отсюда сорвало бы
    завершение разговора и оставило бы его «идущим» навсегда — цена куда выше,
    чем несохранённая реплика.
    """
    try:
        return bool(fn(*a, **kw))
    except Exception as exc:  # noqa: BLE001
        log.info("ai_chat_persist.failed", extra={"err": str(exc)[:200]})
        return False
