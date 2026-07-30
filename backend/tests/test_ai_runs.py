"""Ответ ассистента живёт на СЕРВЕРЕ и переживает обрыв соединения.

Регрессия на реальную жалобу: перезагрузка страницы убивала запрос вместе со
всей проделанной работой, потому что цикл агента крутился внутри HTTP-ответа.
"""
import asyncio

import pytest

from app.services import ai_runs


@pytest.fixture(autouse=True)
def _clean():
    ai_runs._RUNS.clear()
    yield
    ai_runs._RUNS.clear()


async def _events(n: int = 3, pause: float = 0.02):
    for i in range(n):
        await asyncio.sleep(pause)
        yield {"type": "text", "delta": f"часть{i} "}
    yield {"type": "done"}


def test_work_continues_after_the_reader_disconnects():
    """Главное свойство: поток — это ЧИТАТЕЛЬ буфера, а не сама работа."""
    async def main():
        run = ai_runs.start("u1", "s1", lambda: _events())

        # Читатель ушёл на первом же событии — так выглядит перезагрузка.
        async for _ev in ai_runs.follow(run):
            break
        assert ai_runs.active("u1", "s1"), "работа не должна прекращаться"

        await asyncio.sleep(0.3)
        assert run.done and len(run.events) == 4

        # Новый читатель получает ВСЁ с начала: после F5 клиент не знает, что
        # успел применить, и восстанавливает реплику по полному списку.
        got = [e async for e in ai_runs.follow(ai_runs.get("u1", "s1"))]
        assert "".join(e.get("delta", "") for e in got) == "часть0 часть1 часть2 "
    asyncio.run(main())


def test_second_send_joins_the_running_answer_instead_of_spawning_another():
    """Иначе два агента писали бы в один разговор наперегонки."""
    async def main():
        first = ai_runs.start("u1", "s1", lambda: _events(n=5, pause=0.05))
        again = ai_runs.start("u1", "s1", lambda: _events(n=5, pause=0.05))
        assert again is first
        # А в ДРУГОМ разговоре — свой ответ.
        assert ai_runs.start("u1", "s2", lambda: _events()) is not first
        ai_runs.stop("u1", "s1")
        await asyncio.sleep(0.05)
    asyncio.run(main())


def test_runs_are_scoped_to_the_user():
    async def main():
        ai_runs.start("u1", "s1", lambda: _events(n=1))
        await asyncio.sleep(0.1)
        assert ai_runs.get("другой", "s1") is None
    asyncio.run(main())


def test_stop_cancels_and_says_so():
    async def main():
        run = ai_runs.start("u1", "s1", lambda: _events(n=50, pause=0.05))
        assert ai_runs.stop("u1", "s1") is True
        # Разговор помечен завершённым СРАЗУ — не дожидаясь, пока задача заметит
        # отмену: она может не успеть даже начаться (на этом и поймали дефект).
        assert run.done
        assert any("остановлен" in (e.get("message") or "") for e in run.events)
        # Задача действительно снята, а не просто помечена.
        await asyncio.sleep(0.05)
        assert run.task.cancelled() or run.task.done()
        # Второй раз останавливать нечего.
        assert ai_runs.stop("u1", "s1") is False
    asyncio.run(main())


def test_finished_runs_are_swept_but_stay_available_for_a_while():
    """Человеку нужно время перезагрузить страницу и увидеть результат — но не
    вечно: брошенные вкладки иначе копят мусор."""
    async def main():
        ai_runs.start("u1", "s1", lambda: _events(n=1))
        await asyncio.sleep(0.15)
        assert ai_runs.get("u1", "s1") is not None

        ai_runs._RUNS[("u1", "s1")].finished_at = 0  # как будто давно
        assert ai_runs.get("u1", "s1") is None
    asyncio.run(main())


def test_a_failing_agent_is_reported_not_swallowed():
    async def main():
        async def boom():
            yield {"type": "text", "delta": "начал"}
            raise RuntimeError("внутри всё сломалось")

        run = ai_runs.start("u1", "s1", boom)
        await asyncio.sleep(0.1)
        assert run.done
        assert any(e["type"] == "error" for e in run.events)
    asyncio.run(main())
