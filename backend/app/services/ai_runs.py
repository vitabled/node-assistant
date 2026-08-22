"""Ответ ассистента, живущий на СЕРВЕРЕ и переживающий перезагрузку страницы.

Прежде запрос выполнял браузер: поток читался в компоненте, и обновление
страницы обрывало его вместе со всей работой. Уход в другой раздел мы уже
пережили (исполнитель-синглтон на клиенте), но F5 убивает и его — вместе с
самим соединением.

Поэтому цикл агента запускается фоновой задачей на сервере и складывает события
в буфер разговора. HTTP-поток — это просто ЧТЕНИЕ буфера: оборвался он или нет,
работа продолжается. Клиент после перезагрузки переподключается и получает всё
с начала, восстанавливая последнюю реплику.

⚠️ В памяти процесса, а не в БД: ответ живёт минуты, чат всегда выполняется в
gateway-процессе (в очередь уходят только деплои, §10d), а переживать
перезапуск бэкенда ему незачем — тогда уж проще переспросить. Границы: TTL,
потолок событий и числа разговоров, иначе брошенные вкладки копят мусор.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncIterator, Callable, Optional

log = logging.getLogger("ai_runs")

#: Сколько живёт ЗАВЕРШЁННЫЙ ответ: столько есть у человека, чтобы перезагрузить
#: страницу и увидеть результат.
DONE_TTL = 15 * 60
#: Предохранитель от зависшей задачи.
MAX_RUNTIME = 30 * 60
#: Потолок событий на ответ. Превью обрезаны, событие мелкое, но бесконечно
#: расти оно не должно.
MAX_EVENTS = 4000
MAX_RUNS = 50


class Run:
    """Один идущий (или недавно завершённый) ответ."""

    def __init__(self, key: tuple[str, str],
                 on_done: Optional[Callable[[list[dict]], None]] = None) -> None:
        self.key = key
        self.events: list[dict] = []
        self.done = False
        self.started = time.time()
        self.task: Optional[asyncio.Task] = None
        #: Что сделать с результатом, когда ответ закончится. Через него сервер
        #: САМ сохраняет реплику в durable-переписку — см.
        #: `services/ai_chat_persist`. Раньше это делал только браузер, и при
        #: закрытой вкладке результат работы пропадал.
        self._on_done = on_done
        # Будим читателей на каждое событие. `Event` вместо очереди на читателя:
        # читателей может быть сколько угодно (две вкладки), и каждый идёт по
        # общему списку со своим индексом.
        self._tick = asyncio.Event()

    def append(self, event: dict) -> None:
        if len(self.events) < MAX_EVENTS:
            self.events.append(event)
        self._tick.set()

    def finish(self) -> None:
        # ⚠️ Ровно один раз. `finish` зовут ДВОЕ: `finally` фоновой задачи и
        # `stop()` (он не ждёт, пока задача заметит отмену). Без этой защиты
        # результат сохранялся бы дважды — а `stop` вдобавок сохранял бы огрызок
        # ПОСЛЕ полного текста.
        if self.done:
            return
        self.done = True
        self.finished_at = time.time()
        self._tick.set()
        if self._on_done is not None:
            cb, self._on_done = self._on_done, None
            try:
                cb(list(self.events))
            except Exception as exc:  # noqa: BLE001
                # Сохранение истории не вправе сорвать завершение разговора:
                # иначе он навсегда остался бы «идущим».
                log.info("ai_runs.on_done_failed", extra={"err": str(exc)[:200]})

    async def wait(self) -> None:
        self._tick.clear()
        try:
            await asyncio.wait_for(self._tick.wait(), timeout=25.0)
        except asyncio.TimeoutError:
            # Тайм-аут не ошибка: он даёт читателю шанс проверить `done` и не
            # висеть вечно, если задача умерла молча.
            pass


_RUNS: dict[tuple[str, str], Run] = {}


def _sweep() -> None:
    now = time.time()
    for key, run in list(_RUNS.items()):
        age = now - run.started
        if run.done and now - getattr(run, "finished_at", run.started) > DONE_TTL:
            _RUNS.pop(key, None)
        elif not run.done and age > MAX_RUNTIME:
            if run.task and not run.task.done():
                run.task.cancel()
            _RUNS.pop(key, None)
    while len(_RUNS) > MAX_RUNS:
        oldest = min(_RUNS, key=lambda k: _RUNS[k].started)
        _RUNS.pop(oldest, None)


def get(user_id: str, session_id: str) -> Optional[Run]:
    _sweep()
    return _RUNS.get((user_id, session_id))


def active(user_id: str, session_id: str) -> bool:
    run = get(user_id, session_id)
    return bool(run and not run.done)


def start(user_id: str, session_id: str,
          make_events: Callable[[], AsyncIterator[dict]],
          on_done: Optional[Callable[[list[dict]], None]] = None) -> Run:
    """Запустить ответ фоновой задачей. Если ответ уже идёт — вернуть его:
    второе нажатие «Отправить» не должно плодить параллельных агентов.

    `on_done(events)` вызывается РОВНО ОДИН раз по завершении — из фоновой
    задачи, а значит и при закрытой вкладке. Через него результат уезжает в
    durable-переписку (`services/ai_chat_persist`).
    """
    _sweep()
    key = (user_id, session_id)
    existing = _RUNS.get(key)
    if existing and not existing.done:
        return existing

    run = Run(key, on_done)
    _RUNS[key] = run

    async def _pump() -> None:
        try:
            async for event in make_events():
                run.append(event)
        except asyncio.CancelledError:
            run.append({"type": "error", "message": "Ответ остановлен."})
            raise
        except Exception as exc:  # noqa: BLE001 — контракт «стрим не падает»
            log.info("ai_runs.failed", extra={"err": str(exc)[:200]})
            run.append({"type": "error",
                        "message": f"Внутренняя ошибка агента: {str(exc)[:200]}"})
        finally:
            run.finish()

    # ⚠️ `create_task` копирует текущий контекст, поэтому фоновая задача видит
    # `current_user`/`current_account` запроса — от них зависят и права моста, и
    # выбор каталога данных.
    run.task = asyncio.create_task(_pump())
    return run


async def follow(run: Run, start_index: int = 0) -> AsyncIterator[dict]:
    """События с указанного места и дальше — до конца ответа.

    Обрыв этого потока работу НЕ прекращает: он лишь читатель буфера.
    """
    i = max(0, start_index)
    while True:
        while i < len(run.events):
            yield run.events[i]
            i += 1
        if run.done:
            return
        await run.wait()


def stop(user_id: str, session_id: str) -> bool:
    run = get(user_id, session_id)
    if run is None or run.done:
        return False
    if run.task and not run.task.done():
        run.task.cancel()
    # ⚠️ Завершаем САМИ, а не надеемся на `finally` в задаче. Отмена сразу после
    # запуска приходит раньше, чем задача успела начать выполняться: тогда её
    # тело не отработает вовсе, `finally` не случится, и разговор навсегда
    # останется «идущим» — кнопка «Остановить» ничего бы не меняла.
    if not run.done:
        run.append({"type": "error", "message": "Ответ остановлен."})
        run.finish()
    return True
