"""
Gateway ⇄ deploy-worker job hand-off — Plan M Ф3.

The contract under test is the whole point of the split: the gateway enqueues,
a worker in another process runs the job, and the gateway's WS subscriber sees
the logs live — with an in-process fallback whenever no worker is alive.
"""
import asyncio
import contextlib
import uuid

import pytest

# Importing the routers is what registers their job handlers with job_runner —
# the worker container relies on exactly this side effect (see app/worker.py).
# NOTE: no `asyncssh` stub here on purpose; these modules pull in the real
# ssh_manager, whose annotations need the actual package.
from app.api import deploy, node_ops  # noqa: F401
from app.services import accounts, job_runner, shared_task_store as sts, storage, worker_lease
from app.services.task_store import TaskStatus


@pytest.fixture(autouse=True)
def _empty_queue():
    """The job queue is one real FIFO table for the whole session, so a job left
    unclaimed by an earlier test would be handed to this one instead. Start clean."""
    sts._exec("UPDATE tasks SET kind='' WHERE kind!='' AND claimed_at IS NULL")
    yield


@pytest.fixture
def shared(monkeypatch):
    """Point job_runner at the shared store, as the split deployment does."""
    store = sts.SharedTaskStore()
    monkeypatch.setattr(job_runner, "task_store", store)
    return store


def _foreign_worker_lease():
    """Pretend a deploy-worker container is alive and holds the duty."""
    sts._exec(
        "INSERT INTO leases (name, holder, expires_at) VALUES (?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET holder=excluded.holder, expires_at=excluded.expires_at",
        (worker_lease.DEPLOY_WORKER, "deploy-worker-container:1", 2 ** 31),
    )


def _no_worker():
    sts._exec("DELETE FROM leases WHERE name=?", (worker_lease.DEPLOY_WORKER,))


# ── fallback is the default ──────────────────────────────────────
def test_no_offload_with_the_in_process_store():
    """Default monolith: nothing is ever handed off."""
    _foreign_worker_lease()
    try:
        assert job_runner.offload_available("deploy") is False
    finally:
        _no_worker()


def test_no_offload_when_no_worker_holds_the_lease(shared):
    _no_worker()
    assert job_runner.offload_available("deploy") is False
    task = shared.create(total_steps=14)
    assert job_runner.offload(task, "deploy", {"ip": "10.0.0.1"}) is False


def test_no_offload_for_an_unregistered_kind(shared):
    _foreign_worker_lease()
    try:
        assert job_runner.offload_available("not-a-real-kind") is False
    finally:
        _no_worker()


def test_deploy_and_node_op_handlers_are_registered():
    assert "deploy" in job_runner.kinds()
    assert "node-op" in job_runner.kinds()


# ── hand-off ─────────────────────────────────────────────────────
def test_offload_queues_the_job_for_a_live_worker(shared):
    _foreign_worker_lease()
    try:
        task = shared.create(total_steps=14)
        assert job_runner.offload(task, "deploy", {"ip": "10.0.0.1"}) is True
        claimed = shared.claim_next(["deploy"], "worker-1")
        assert claimed is not None
        got, kind, payload = claimed
        assert got.task_id == task.task_id and kind == "deploy"
        assert payload == {"ip": "10.0.0.1"}
    finally:
        _no_worker()


def test_a_failed_offload_never_leaves_a_claimable_job(shared, monkeypatch):
    """If offload reports False the caller runs the pipeline itself, so a job must
    NOT be sitting in the queue — otherwise the worker claims it too and two
    14-step deploys race on the same box. The enqueue write must therefore be the
    last thing that can fail."""
    _foreign_worker_lease()
    try:
        task = shared.create(total_steps=14)

        def boom(line):
            raise RuntimeError("sqlite is busy")

        monkeypatch.setattr(task, "add_log", boom)
        assert job_runner.offload(task, "deploy", {"ip": "10.0.0.1"}) is False
        assert shared.claim_next(["deploy"], "worker-1") is None
    finally:
        _no_worker()


# ── worker-side execution ────────────────────────────────────────
def test_execute_runs_the_handler_and_streams_into_the_shared_store(shared, monkeypatch):
    async def handler(payload, task):
        task.set_step(1, TaskStatus.RUNNING)
        task.add_log(f"working on {payload['ip']}")
        task.finish(TaskStatus.SUCCESS)

    monkeypatch.setitem(job_runner._HANDLERS, "t-ok", handler)
    task = shared.create(total_steps=1)
    asyncio.run(job_runner.execute("t-ok", {"ip": "10.0.0.9"}, task))

    seen = sts.SharedTaskStore().get(task.task_id)      # the gateway's view
    assert seen.status == TaskStatus.SUCCESS
    assert "working on 10.0.0.9" in seen.logs


def test_account_context_survives_the_queue_hop(shared, monkeypatch):
    """The pipeline reads per-account settings/templates/hosts through the
    `current_account` ContextVar. A worker process has no request to inherit it
    from, so the job must republish it — otherwise every Remnawave-registering
    deploy dies with "No active account in context"."""
    aid = accounts.create_account(f"jr-{uuid.uuid4().hex[:8]}", "pw")["id"]
    seen = {}

    async def handler(payload, task):
        seen["ctx"] = accounts.current_account.get()
        # Exactly what pipeline.step_create_node does — must not raise here.
        seen["settings_ok"] = isinstance(storage.load_settings(), dict)
        task.finish(TaskStatus.SUCCESS)

    monkeypatch.setitem(job_runner._HANDLERS, "t-ctx", handler)

    token = accounts.current_account.set(aid)      # the originating request
    try:
        task = shared.create(total_steps=1)
    finally:
        accounts.current_account.reset(token)      # ...which then ends

    assert task.account_id == aid
    asyncio.run(job_runner.execute("t-ctx", {}, task))   # worker process
    assert seen["ctx"] == aid
    assert seen["settings_ok"] is True


def test_execute_marks_failed_when_the_handler_raises(shared, monkeypatch):
    async def handler(payload, task):
        raise RuntimeError("ssh exploded")

    monkeypatch.setitem(job_runner._HANDLERS, "t-boom", handler)
    task = shared.create(total_steps=1)
    asyncio.run(job_runner.execute("t-boom", {}, task))
    assert task.status == TaskStatus.FAILED
    assert "ssh exploded" in (task.error or "")


def test_execute_never_leaves_a_task_hanging(shared, monkeypatch):
    """A handler returning without a verdict would spin the deploy card forever."""
    async def handler(payload, task):
        return

    monkeypatch.setitem(job_runner._HANDLERS, "t-silent", handler)
    task = shared.create(total_steps=1)
    asyncio.run(job_runner.execute("t-silent", {}, task))
    assert task.status == TaskStatus.FAILED


def test_execute_rejects_an_unknown_kind(shared):
    task = shared.create(total_steps=1)
    asyncio.run(job_runner.execute("nope", {}, task))
    assert task.status == TaskStatus.FAILED
    assert "nope" in (task.error or "")


# ── cross-process cancellation ───────────────────────────────────
def test_cancel_flag_stops_a_running_job(shared, monkeypatch):
    """`POST /api/deploy/stop` in the gateway must reach a worker elsewhere."""
    started = asyncio.Event()

    async def handler(payload, task):
        task.set_step(1, TaskStatus.RUNNING)
        started.set()
        try:
            await asyncio.sleep(30)          # long-running SSH work
        except asyncio.CancelledError:
            task.finish(TaskStatus.FAILED, "Остановлено пользователем")
            raise

    monkeypatch.setitem(job_runner._HANDLERS, "t-slow", handler)
    monkeypatch.setattr(job_runner, "_CANCEL_POLL", 0.05)
    task = shared.create(total_steps=1)

    async def go():
        runner = asyncio.create_task(job_runner.execute("t-slow", {}, task))
        await asyncio.wait_for(started.wait(), timeout=5)
        sts.SharedTaskStore().request_cancel(task.task_id)   # the gateway's call
        await asyncio.wait_for(runner, timeout=10)

    asyncio.run(go())
    assert task.status == TaskStatus.FAILED
    assert task.error == "Остановлено пользователем"


# ── nothing may be stranded ──────────────────────────────────────
def test_a_job_claimed_by_a_dead_worker_is_failed_not_stranded(shared):
    """A claimed row can only be advanced by its claimer. If that process is gone
    the deploy card would spin forever, so it must be reaped."""
    _no_worker()
    task = shared.create(total_steps=14)
    shared.enqueue(task.task_id, "deploy", {"ip": "10.0.0.1"})
    claimed = shared.claim_next(["deploy"], "worker-that-then-died:1")
    assert claimed is not None
    claimed[0].set_step(3, TaskStatus.RUNNING)

    assert job_runner.reap_orphans() >= 1
    assert task.status == TaskStatus.FAILED
    assert "недоступен" in (task.error or "")


def test_reaping_never_touches_a_healthy_running_job(shared):
    """The live worker keeps renewing the lease — its job must survive reaping."""
    task = shared.create(total_steps=14)
    shared.enqueue(task.task_id, "deploy", {"ip": "10.0.0.1"})
    holder = "the-live-worker:7"
    sts._exec(
        "INSERT INTO leases (name, holder, expires_at) VALUES (?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET holder=excluded.holder, expires_at=excluded.expires_at",
        (worker_lease.DEPLOY_WORKER, holder, 2 ** 31),
    )
    try:
        shared.claim_next(["deploy"], holder)
        job_runner.reap_orphans()
        assert task.status != TaskStatus.FAILED
    finally:
        _no_worker()


def test_a_job_cancelled_while_queued_is_never_started(shared):
    """/api/deploy/stop already answered ok — the worker must not run it anyway."""
    _no_worker()
    task = shared.create(total_steps=14)
    shared.enqueue(task.task_id, "deploy", {"ip": "10.0.0.1"})
    assert shared.request_cancel(task.task_id) is True

    assert shared.claim_next(["deploy"], "worker-1") is None
    assert task.status == TaskStatus.FAILED
    assert task.error == "Остановлено пользователем"


def test_an_undecryptable_job_is_failed_not_orphaned(shared, monkeypatch):
    """Claiming happens before the payload can be read, so a decrypt failure
    (mismatched ENCRYPTION_KEY) must still end the task."""
    _no_worker()
    task = shared.create(total_steps=14)
    shared.enqueue(task.task_id, "deploy", {"ip": "10.0.0.1"})
    monkeypatch.setattr(sts, "_decrypt", lambda blob: None)

    assert shared.claim_next(["deploy"], "worker-1") is None
    assert task.status == TaskStatus.FAILED
    assert "прочитать" in (task.error or "")


def test_shutdown_fails_the_in_flight_job_and_releases_the_lease(shared, monkeypatch):
    """SIGTERM during a deploy: the card must get a verdict, and the gateway must
    be able to take the duty back immediately rather than after the TTL."""
    started = asyncio.Event()

    async def handler(payload, task):
        task.set_step(1, TaskStatus.RUNNING)
        started.set()
        await asyncio.sleep(30)

    monkeypatch.setitem(job_runner._HANDLERS, "t-shutdown", handler)
    monkeypatch.setattr(job_runner, "_HEARTBEAT", 1)
    task = shared.create(total_steps=1)
    shared.enqueue(task.task_id, "t-shutdown", {})

    async def go():
        loop = asyncio.create_task(job_runner.run_forever(idle_poll=0.05))
        await asyncio.wait_for(started.wait(), timeout=10)
        loop.cancel()                                  # what SIGTERM does
        with contextlib.suppress(asyncio.CancelledError):
            await loop

    asyncio.run(go())
    assert task.status == TaskStatus.FAILED
    assert task.error == "Рабочий процесс остановлен"
    assert worker_lease.status(worker_lease.DEPLOY_WORKER)["holder"] is None


# ── the whole loop, end to end ───────────────────────────────────
def test_worker_loop_claims_a_queued_job_and_the_gateway_sees_the_logs(shared, monkeypatch):
    async def handler(payload, task):
        task.set_step(1, TaskStatus.RUNNING)
        task.add_log("[1/1] Подключение к серверу")
        task.finish(TaskStatus.SUCCESS)

    monkeypatch.setitem(job_runner._HANDLERS, "t-e2e", handler)
    monkeypatch.setattr(job_runner, "_HEARTBEAT", 1)
    _foreign_worker_lease()
    try:
        gateway = shared                       # process A: the API
        task = gateway.create(total_steps=1)
        assert job_runner.offload(task, "t-e2e", {"ip": "10.0.0.1"}) is True
    finally:
        _no_worker()

    async def go():
        # Process A subscribes to the stream exactly as /ws/logs/{task_id} does.
        watcher = sts.SharedTaskStore().get(task.task_id)
        q = watcher.subscribe()

        # Process B: the worker loop claims and runs it, then we stop the loop.
        loop = asyncio.create_task(job_runner.run_forever(idle_poll=0.05))
        seen = []
        try:
            while ("done",) not in seen:
                seen.append(await asyncio.wait_for(q.get(), timeout=15))
        finally:
            loop.cancel()
            watcher.unsubscribe(q)
        return seen

    seen = asyncio.run(go())
    assert ("log", "[1/1] Подключение к серверу") in seen
    assert ("done",) in seen
    assert sts.SharedTaskStore().get(task.task_id).status == TaskStatus.SUCCESS
