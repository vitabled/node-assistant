"""
Job hand-off between the gateway and the split `deploy-worker` container.

The gateway never talks HTTP to the worker — it writes an encrypted job row into
the shared task store (`shared_task_store`) and the worker claims it. Progress
flows back the same way: the worker streams into `task_logs`, the gateway's
existing `/ws/logs/{task_id}` tails it. The browser contract (`{task_id,
task_type}` then a WS subscribe) is completely unchanged.

FALLBACK IS THE DEFAULT. `offload()` only hands work off when BOTH
  * the shared store is active (`TASK_STORE=shared`), and
  * a live worker currently holds the `deploy-worker` lease,
otherwise it returns False and the caller runs the job in-process exactly as
before. So `docker compose up` without the split profile — or a split deployment
whose worker container is down — keeps working with zero behavioural change.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, Awaitable, Callable, Optional

from app.config import settings
from app.services import accounts, worker_lease
from app.services.task_store import TaskStatus, task_store

log = logging.getLogger(__name__)

# kind → coroutine(payload, task). The handler rehydrates its own request model,
# which keeps this module free of any knowledge about individual job types.
Handler = Callable[[dict, Any], Awaitable[None]]
_HANDLERS: dict[str, Handler] = {}

_CANCEL_POLL = 1.0      # how often the worker checks for a cross-process cancel
_IDLE_POLL = 2.0        # how often an idle worker looks for new work
_HEARTBEAT = 30         # lease renewal cadence while a long job runs


def register(kind: str, handler: Handler) -> None:
    _HANDLERS[kind] = handler


def kinds() -> list[str]:
    return sorted(_HANDLERS)


def offload_available(kind: str) -> bool:
    """True when a live worker in another process can take `kind` off our hands."""
    return (
        kind in _HANDLERS
        and getattr(task_store, "mode", "memory") == "shared"
        and worker_lease.held_elsewhere(worker_lease.DEPLOY_WORKER)
    )


def offload(task: Any, kind: str, payload: dict) -> bool:
    """Queue the job for the worker. False → the caller must run it itself."""
    if not offload_available(kind):
        return False
    try:
        # ORDER MATTERS. `enqueue` is the commit point that makes the row
        # claimable, so everything that can fail must happen BEFORE it. With the
        # writes the other way round, a failing add_log (e.g. SQLITE_BUSY past
        # busy_timeout) returns False while the job is already queued — the
        # caller then runs the pipeline in-process AND the worker claims the same
        # row, i.e. two deploys racing on one box, both rewriting sshd/UFW and
        # rebooting it. Failing before the enqueue leaves no row and falls back
        # cleanly.
        task.add_log(f"\x1b[2m[СИСТЕМА] Задача передана рабочему процессу ({kind}).\x1b[0m")
        task_store.enqueue(task.task_id, kind, payload)
        return True
    except Exception:
        log.exception("job_runner.enqueue_failed kind=%s", kind)
        return False


# ── worker side ──────────────────────────────────────────────────
async def _watch_cancel(task: Any, runner: asyncio.Task, state: dict) -> None:
    """Bridge `POST /api/deploy/stop` (gateway process) to a cancel here.

    Records that the cancel was ours in `state` so `execute` can tell a
    user-cancelled job (keep working) from this whole process being shut down
    (stop working) — both surface as the same CancelledError."""
    while not runner.done():
        await asyncio.sleep(_CANCEL_POLL)
        try:
            if task_store.cancel_requested(task.task_id):
                state["user_cancel"] = True
                runner.cancel()
                return
        except Exception:
            log.exception("job_runner.cancel_poll_failed task_id=%s", task.task_id)
            return


def reap_orphans() -> int:
    """Fail jobs claimed by a process that is demonstrably gone.

    A claimed row can only be advanced by its claimer, so if that process no
    longer exists the row would sit RUNNING forever and the deploy card would
    spin with no verdict. "Gone" is decided by the lease, not by a timeout: the
    claimer is dead once it neither holds the duty nor is us. A long-but-healthy
    job is therefore never touched, because its worker keeps renewing the lease.
    """
    alive = {worker_lease.holder_id()}
    st = worker_lease.status(worker_lease.DEPLOY_WORKER)
    if st["fresh"] and st["holder"]:
        alive.add(st["holder"])
    try:
        return task_store.reap_orphans(alive)
    except Exception:
        log.exception("job_runner.reap_failed")
        return 0


async def _heartbeat() -> None:
    while True:
        await asyncio.sleep(_HEARTBEAT)
        worker_lease.acquire(worker_lease.DEPLOY_WORKER)


async def execute(kind: str, payload: dict, task: Any) -> None:
    """Run one claimed job. Never raises — a worker must survive any job."""
    handler = _HANDLERS.get(kind)
    if handler is None:
        task.finish(TaskStatus.FAILED, f"Неизвестный тип задачи: {kind}")
        return

    # Re-establish the account context the originating REQUEST had. The pipeline
    # reads per-account settings/templates/hosts/traffic-rules through
    # `storage`'s `current_account` fallback, and this process has no request to
    # inherit it from — without this, any Remnawave-registering deploy would die
    # with "No active account in context". Set BEFORE create_task: a task copies
    # the context at creation time.
    try:
        account_id = getattr(task, "account_id", "") or ""
    except Exception:                       # a DB hiccup must not skip the job
        log.exception("job_runner.account_lookup_failed task_id=%s", task.task_id)
        account_id = ""
    token = accounts.current_account.set(account_id) if account_id else None

    state = {"user_cancel": False}
    runner = asyncio.create_task(handler(payload, task))
    watcher = asyncio.create_task(_watch_cancel(task, runner, state))
    shutting_down = False
    try:
        await runner
    except asyncio.CancelledError:
        # `await runner` raises CancelledError for TWO different reasons and they
        # need opposite handling:
        #   * the watcher cancelled the job (user pressed stop) → the pipeline has
        #     already marked itself FAILED; absorb it and keep serving jobs.
        #   * THIS coroutine was cancelled (SIGTERM / worker shutdown) → the child
        #     is still running and must be stopped, and the cancellation has to
        #     keep propagating or run_forever would loop forever on a dead worker.
        if state["user_cancel"]:
            pass
        else:
            shutting_down = True
            runner.cancel()
            with contextlib.suppress(BaseException):
                await runner
    except Exception as exc:
        log.exception("job_runner.job_failed kind=%s", kind)
        if task.status not in (TaskStatus.SUCCESS, TaskStatus.FAILED):
            task.finish(TaskStatus.FAILED, str(exc))
    finally:
        watcher.cancel()
        # A handler that returned without a verdict would leave the card spinning
        # forever, so close it out explicitly. On shutdown we leave the verdict to
        # run_forever, which reports it as an interrupted job rather than a
        # mysterious "no result".
        if not shutting_down and task.status not in (TaskStatus.SUCCESS, TaskStatus.FAILED):
            task.finish(TaskStatus.FAILED, "Задача завершилась без результата")
        if token is not None:
            accounts.current_account.reset(token)
    if shutting_down:
        raise asyncio.CancelledError()


async def _run_one(kind: str, payload: dict, task: Any) -> None:
    """Run one claimed job and GUARANTEE it ends with a verdict.

    `execute` is meant never to raise, but its own bookkeeping reads task state
    from SQLite, so a transient DB error can escape it. Without this wrapper that
    would kill the claim loop with the row already claimed — unreachable to every
    other process, i.e. a card that spins forever."""
    try:
        await execute(kind, payload, task)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            if task.status not in (TaskStatus.SUCCESS, TaskStatus.FAILED):
                task.add_log("\n\x1b[1;33m[СИСТЕМА] Рабочий процесс остановлен — "
                             "задача прервана.\x1b[0m")
                task.finish(TaskStatus.FAILED, "Рабочий процесс остановлен")
        raise
    except Exception:
        log.exception("job_runner.execute_escaped kind=%s task_id=%s", kind, task.task_id)
        with contextlib.suppress(Exception):
            if task.status not in (TaskStatus.SUCCESS, TaskStatus.FAILED):
                task.finish(TaskStatus.FAILED, "Внутренняя ошибка рабочего процесса")


async def run_forever(idle_poll: float = _IDLE_POLL, hold_lease: bool = True) -> None:
    """Claim loop. Run by the `deploy-worker` container (hold_lease=True) and, as
    the fallback, by the gateway whenever no worker holds the duty.

    On shutdown it marks any in-flight job FAILED and releases the lease, so the
    deploy card ends with a real verdict instead of spinning forever and the
    gateway can take over immediately rather than after the 180 s TTL."""
    holder = worker_lease.holder_id()
    if hold_lease:
        worker_lease.acquire(worker_lease.DEPLOY_WORKER)
    beat = asyncio.create_task(_heartbeat()) if hold_lease else None
    log.info("job_runner.started holder=%s hold_lease=%s kinds=%s", holder, hold_lease, kinds())
    running: set[asyncio.Task] = set()
    try:
        while True:
            claimed: Optional[tuple] = None
            try:
                if hold_lease:
                    worker_lease.acquire(worker_lease.DEPLOY_WORKER)
                elif worker_lease.held_elsewhere(worker_lease.DEPLOY_WORKER):
                    # A real worker is alive — stay out of its way.
                    await asyncio.sleep(idle_poll)
                    continue
                # Match the monolith's concurrency instead of serialising every
                # deploy behind one slot (settings.max_ssh_sessions, same cap the
                # gateway applies in-process).
                if len(running) < max(1, settings.max_ssh_sessions):
                    reap_orphans()
                    claimed = task_store.claim_next(kinds(), holder)
            except Exception:
                log.exception("job_runner.claim_failed")
            if claimed is None:
                await asyncio.sleep(idle_poll)
                continue
            task, kind, payload = claimed
            log.info("job_runner.claimed kind=%s task_id=%s", kind, task.task_id)
            job = asyncio.create_task(_run_one(kind, payload, task))
            running.add(job)
            job.add_done_callback(running.discard)
    finally:
        # Shutdown (SIGTERM, cancellation). Every in-flight job is claimed by US,
        # so nobody else can close it out — cancel them and let `_run_one` give
        # each one a verdict.
        for job in list(running):
            job.cancel()
        for job in list(running):
            with contextlib.suppress(BaseException):
                await job
        if beat is not None:
            beat.cancel()
        if hold_lease:
            worker_lease.release(worker_lease.DEPLOY_WORKER)
