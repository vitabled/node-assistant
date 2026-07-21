"""
Shared (cross-process) task store + worker leases — Plan M Ф1.

The point of these tests is the SPLIT contract: a task written by one holder of
the store must be fully readable through a different store instance (that is what
a separate worker container does), credentials must never sit in the clear, and a
job must be claimable exactly once.
"""
import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path

from app.services import shared_task_store as sts
from app.services import worker_lease
from app.services.task_store import TaskStatus, task_store


def _store():
    return sts.SharedTaskStore()


# ── default wiring ───────────────────────────────────────────────
def test_default_store_is_in_process():
    """Without TASK_STORE=shared the monolith keeps the in-memory store."""
    assert task_store.mode == "memory"


def test_importable_from_a_worker_process_with_the_shared_store():
    """Regression: the split worker imports `worker_lease` FIRST, which pulls in
    `shared_task_store` before `task_store`. That order used to explode with a
    circular ImportError while the gateway's order happened to work — so it has
    to be checked in a fresh interpreter, not in this already-imported one."""
    env = dict(os.environ, TASK_STORE="shared", PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    r = subprocess.run(
        [sys.executable, "-c",
         "from app.services import worker_lease\n"
         "from app.services.task_store import task_store\n"
         "assert task_store.mode == 'shared', task_store.mode\n"
         "print('ok')"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert r.returncode == 0, f"stdout={r.stdout} stderr={r.stderr}"
    assert "ok" in r.stdout


def test_in_process_store_has_shared_parity_methods():
    assert task_store.request_cancel(str(uuid.uuid4())) is False
    assert task_store.cancel_requested(str(uuid.uuid4())) is False
    assert task_store.stats()["mode"] == "memory"


# ── round-trip across store instances (the "other process" case) ──
def test_task_state_round_trips_through_the_db():
    writer = _store()
    task = writer.create(total_steps=14)
    task.add_log("first")
    task.set_step(3, TaskStatus.RUNNING)
    task.add_log("second")

    reader = _store()                      # stands in for the gateway process
    seen = reader.get(task.task_id)
    assert seen is not None
    assert seen.total_steps == 14
    assert seen.current_step == 3
    assert seen.status == TaskStatus.RUNNING
    assert seen.logs == ["first", "second"]

    task.finish(TaskStatus.FAILED, "boom")
    assert seen.status == TaskStatus.FAILED
    assert seen.error == "boom"


def test_get_returns_none_for_unknown_task():
    assert _store().get(str(uuid.uuid4())) is None


def test_finish_without_any_step_is_tolerated():
    """replace_domain._run_panel finishes a task that never called set_step."""
    task = _store().create(total_steps=3)
    task.finish(TaskStatus.FAILED, "no new domain")
    assert task.status == TaskStatus.FAILED
    assert task.current_step == 0


# ── WS bridge ────────────────────────────────────────────────────
def test_subscribe_preloads_state_then_history():
    store = _store()
    task = store.create(total_steps=2)
    task.set_step(1, TaskStatus.RUNNING)
    task.add_log("hello")

    async def go():
        return _store().get(task.task_id).subscribe()

    q = asyncio.run(go())
    assert q.get_nowait() == ("step", 1, "running", 2)
    assert q.get_nowait() == ("log", "hello")
    assert q.empty()


def test_subscribe_emits_done_when_already_terminal():
    store = _store()
    task = store.create(total_steps=1)
    task.set_step(1, TaskStatus.RUNNING)
    task.finish(TaskStatus.SUCCESS)

    async def go():
        return _store().get(task.task_id).subscribe()

    q = asyncio.run(go())
    assert q.get_nowait()[0] == "step"
    assert q.get_nowait() == ("done",)


def test_tailer_streams_writes_from_another_store_instance():
    """The real split path: worker writes, gateway's subscriber sees it live."""
    store = _store()
    task = store.create(total_steps=1)
    task.set_step(1, TaskStatus.RUNNING)

    async def go():
        reader = _store().get(task.task_id)
        q = reader.subscribe()
        assert q.get_nowait()[0] == "step"
        task.add_log("from-worker")          # written by the "other process"
        task.finish(TaskStatus.SUCCESS)
        items = []
        for _ in range(3):
            items.append(await asyncio.wait_for(q.get(), timeout=5))
        reader.unsubscribe(q)
        return items

    items = asyncio.run(go())
    assert ("log", "from-worker") in items
    assert ("done",) in items


# ── job queue ────────────────────────────────────────────────────
def test_enqueue_claim_round_trip():
    store = _store()
    task = store.create(total_steps=14)
    store.enqueue(task.task_id, "deploy", {"ip": "10.0.0.1", "ssh_password": "s3cret"})

    claimed = store.claim_next(["deploy"], "worker-1")
    assert claimed is not None
    got, kind, payload = claimed
    assert got.task_id == task.task_id
    assert kind == "deploy"
    assert payload["ssh_password"] == "s3cret"


def test_job_is_claimed_exactly_once():
    store = _store()
    task = store.create(total_steps=1)
    store.enqueue(task.task_id, "node-op", {"x": 1})

    first = store.claim_next(["node-op"], "worker-1")
    second = store.claim_next(["node-op"], "worker-2")
    assert first is not None and first[0].task_id == task.task_id
    assert second is None or second[0].task_id != task.task_id


def test_claim_ignores_other_kinds():
    store = _store()
    task = store.create(total_steps=1)
    store.enqueue(task.task_id, "deploy", {"x": 1})
    assert store.claim_next(["node-op"], "w") is None


def test_payload_is_encrypted_at_rest_and_wiped_on_finish():
    store = _store()
    task = store.create(total_steps=1)
    store.enqueue(task.task_id, "deploy", {"ssh_password": "plaintext-please-no"})

    raw = sts._one("SELECT payload_enc FROM tasks WHERE task_id=?", (task.task_id,))
    assert raw["payload_enc"] is not None
    assert b"plaintext-please-no" not in bytes(raw["payload_enc"])

    task.finish(TaskStatus.SUCCESS)
    raw2 = sts._one("SELECT payload_enc FROM tasks WHERE task_id=?", (task.task_id,))
    assert raw2["payload_enc"] is None


# ── cancellation across processes ────────────────────────────────
def test_request_cancel_flags_a_running_task():
    store = _store()
    task = store.create(total_steps=1)
    task.set_step(1, TaskStatus.RUNNING)
    assert store.cancel_requested(task.task_id) is False
    assert store.request_cancel(task.task_id) is True
    assert store.cancel_requested(task.task_id) is True


def test_request_cancel_is_false_for_finished_or_unknown():
    store = _store()
    task = store.create(total_steps=1)
    task.finish(TaskStatus.SUCCESS)
    assert store.request_cancel(task.task_id) is False
    assert store.request_cancel(str(uuid.uuid4())) is False


# ── leases ───────────────────────────────────────────────────────
def test_lease_is_reentrant_for_the_same_holder():
    name = f"duty-{uuid.uuid4().hex[:8]}"
    assert worker_lease.acquire(name, ttl=60) is True
    assert worker_lease.acquire(name, ttl=60) is True      # renew
    st = worker_lease.status(name)
    assert st["self"] is True and st["fresh"] is True
    assert worker_lease.held_elsewhere(name) is False


def test_lease_blocks_a_second_holder_until_it_expires():
    name = f"duty-{uuid.uuid4().hex[:8]}"
    other = "some-other-container:1"
    sts._exec("INSERT INTO leases (name, holder, expires_at) VALUES (?,?,?)",
              (name, other, 2 ** 31))                       # far future
    assert worker_lease.acquire(name, ttl=60) is False
    assert worker_lease.held_elsewhere(name) is True

    sts._exec("UPDATE leases SET expires_at=1 WHERE name=?", (name,))   # expired
    assert worker_lease.held_elsewhere(name) is False
    assert worker_lease.acquire(name, ttl=60) is True       # taken over


def test_lease_release_frees_the_duty():
    name = f"duty-{uuid.uuid4().hex[:8]}"
    worker_lease.acquire(name, ttl=60)
    worker_lease.release(name)
    assert worker_lease.status(name)["holder"] is None


def test_lease_fails_open_when_the_db_is_unreachable(monkeypatch):
    """A broken lease table must never silence a monolith's monitoring."""
    def boom(*a, **k):
        raise RuntimeError("db gone")
    monkeypatch.setattr(sts, "_exec", boom)
    assert worker_lease.acquire("anything", ttl=60) is True
