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
import logging
from typing import Any, Awaitable, Callable, Optional

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
async def _watch_cancel(task: Any, runner: asyncio.Task) -> None:
    """Bridge `POST /api/deploy/stop` (gateway process) to a cancel here."""
    while not runner.done():
        await asyncio.sleep(_CANCEL_POLL)
        try:
            if task_store.cancel_requested(task.task_id):
                runner.cancel()
                return
        except Exception:
            log.exception("job_runner.cancel_poll_failed task_id=%s", task.task_id)
            return


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
    account_id = getattr(task, "account_id", "") or ""
    token = accounts.current_account.set(account_id) if account_id else None

    runner = asyncio.create_task(handler(payload, task))
    watcher = asyncio.create_task(_watch_cancel(task, runner))
    try:
        await runner
    except asyncio.CancelledError:
        # The pipeline already marked the task FAILED on its way out; this is the
        # worker absorbing the cancel, NOT the worker itself being shut down.
        pass
    except Exception as exc:
        log.exception("job_runner.job_failed kind=%s", kind)
        if task.status not in (TaskStatus.SUCCESS, TaskStatus.FAILED):
            task.finish(TaskStatus.FAILED, str(exc))
    finally:
        watcher.cancel()
        # A handler that returned without a verdict would leave the card spinning
        # forever, so close it out explicitly.
        if task.status not in (TaskStatus.SUCCESS, TaskStatus.FAILED):
            task.finish(TaskStatus.FAILED, "Задача завершилась без результата")
        if token is not None:
            accounts.current_account.reset(token)


async def run_forever(idle_poll: float = _IDLE_POLL) -> None:
    """The `deploy-worker` container's main loop: hold the lease, claim, run."""
    holder = worker_lease.holder_id()
    worker_lease.acquire(worker_lease.DEPLOY_WORKER)
    beat = asyncio.create_task(_heartbeat())
    log.info("job_runner.started holder=%s kinds=%s", holder, kinds())
    try:
        while True:
            claimed: Optional[tuple] = None
            try:
                worker_lease.acquire(worker_lease.DEPLOY_WORKER)
                claimed = task_store.claim_next(kinds(), holder)
            except Exception:
                log.exception("job_runner.claim_failed")
            if claimed is None:
                await asyncio.sleep(idle_poll)
                continue
            task, kind, payload = claimed
            log.info("job_runner.claimed kind=%s task_id=%s", kind, task.task_id)
            await execute(kind, payload, task)
    finally:
        beat.cancel()
