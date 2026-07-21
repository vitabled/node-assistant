"""
Cross-process task/log store (SQLite) — the shared alternative to the in-memory
`task_store.TaskStore`.

Why it exists: long-running work (the 14-step deploy pipeline) streams its
progress into a `Task`, and the WS endpoint `/ws/logs/{task_id}` reads from that
same object. In one process a plain dict works. To run the pipeline in a
SEPARATE process (Plan M, `--profile split`) the producer and the reader need a
shared medium — this module is that medium, and it also carries the job queue the
worker pulls from.

Selected by env `TASK_STORE=shared` (default `memory` → the in-process store).
Everything here is duck-type compatible with `task_store.Task`/`TaskStore`, so no
caller changes: mutators stay SYNCHRONOUS (they are called from `SSHSession._drain`
and ~250 pipeline sites), and `subscribe()` still returns an `asyncio.Queue`
carrying the same three tuple shapes.

Job payloads carry SSH credentials, so they are Fernet-encrypted at rest (key =
SHA-256 of `settings.encryption_key`, the same derivation as the infra-billing and
rules vaults) and wiped the moment the job reaches a terminal state.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
import uuid as _uuid
from pathlib import Path
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings
from app.services import accounts
from app.services.task_types import STEP_LABELS, TaskStatus

log = logging.getLogger(__name__)

# Retention: finished tasks (and their logs) are dropped after this long. The
# in-memory store never cleaned up at all (`cleanup()` had zero callers), so any
# retention is an improvement; a day is far longer than a deploy card is watched.
_RETENTION_S = 24 * 3600
_LOG_TAIL_CAP = 2000        # parity with the in-memory deque(maxlen=2000)
_POLL_INTERVAL = 0.4        # seconds between tail polls for a WS subscriber


def db_path() -> Path:
    return accounts.DATA_DIR / "tasks.db"


_conn: Optional[sqlite3.Connection] = None
_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id          TEXT PRIMARY KEY,
    account_id       TEXT NOT NULL DEFAULT '',
    kind             TEXT NOT NULL DEFAULT '',
    total_steps      INTEGER NOT NULL,
    current_step     INTEGER NOT NULL DEFAULT 0,
    status           TEXT    NOT NULL,
    error            TEXT,
    created_at       INTEGER NOT NULL,
    updated_at       INTEGER NOT NULL,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    payload_enc      BLOB,
    claimed_by       TEXT,
    claimed_at       INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tasks_queue   ON tasks(kind, claimed_at);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at);

CREATE TABLE IF NOT EXISTS task_logs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT    NOT NULL,
    ts      INTEGER NOT NULL,
    line    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_logs ON task_logs(task_id, id);

CREATE TABLE IF NOT EXISTS leases (
    name       TEXT PRIMARY KEY,
    holder     TEXT    NOT NULL,
    expires_at INTEGER NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    """One shared connection guarded by `_lock`.

    WAL is what makes concurrent readers/writers across CONTAINERS work on a
    shared volume; `busy_timeout` absorbs the cross-process write contention.
    """
    global _conn
    if _conn is not None:
        return _conn
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    conn.commit()
    _conn = conn
    return conn


def _exec(sql: str, args: tuple = ()) -> sqlite3.Cursor:
    with _lock:
        conn = _connect()
        cur = conn.execute(sql, args)
        conn.commit()
        return cur


def _rows(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    with _lock:
        return _connect().execute(sql, args).fetchall()


def _one(sql: str, args: tuple = ()) -> Optional[sqlite3.Row]:
    with _lock:
        return _connect().execute(sql, args).fetchone()


def reset_for_tests() -> None:
    """Drop the cached connection so a test can point DATA_DIR somewhere else."""
    global _conn
    with _lock:
        if _conn is not None:
            with contextlib.suppress(Exception):
                _conn.close()
        _conn = None


# ── Payload vault (SSH creds must not sit in the clear) ──────────
def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.encryption_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt(payload: dict) -> bytes:
    return _fernet().encrypt(json.dumps(payload).encode())


def _decrypt(blob: bytes) -> Optional[dict]:
    try:
        return json.loads(_fernet().decrypt(blob).decode())
    except (InvalidToken, ValueError, TypeError):
        log.warning("shared_task_store.payload_decrypt_failed")
        return None


class SharedTask:
    """Duck-type twin of `task_store.Task` backed by SQLite.

    Mutators are synchronous and each is a single short write. Reads of
    `status`/`error`/`current_step` hit the DB so a reader in ANOTHER process
    observes the worker's progress.
    """

    def __init__(self, task_id: str, total_steps: int) -> None:
        self.task_id = task_id
        self.total_steps = total_steps          # immutable → safe to cache
        self._tailers: dict[int, asyncio.Task] = {}

    # ── reads ──
    def _row(self) -> dict:
        r = _one("SELECT current_step, status, error FROM tasks WHERE task_id=?", (self.task_id,))
        if r is None:
            return {"current_step": 0, "status": TaskStatus.PENDING.value, "error": None}
        return dict(r)

    @property
    def account_id(self) -> str:
        """Account that created the task. The worker republishes this on the
        `current_account` ContextVar before running a job — the pipeline reads
        per-account settings/templates/hosts through it, and a worker process has
        no request to inherit it from."""
        r = _one("SELECT account_id FROM tasks WHERE task_id=?", (self.task_id,))
        return (r["account_id"] if r else "") or ""

    @property
    def current_step(self) -> int:
        return int(self._row()["current_step"])

    @property
    def status(self) -> TaskStatus:
        try:
            return TaskStatus(self._row()["status"])
        except ValueError:
            return TaskStatus.PENDING

    @property
    def error(self) -> Optional[str]:
        return self._row()["error"]

    @property
    def logs(self) -> list[str]:
        rows = _rows(
            "SELECT line FROM (SELECT id, line FROM task_logs WHERE task_id=? "
            "ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
            (self.task_id, _LOG_TAIL_CAP),
        )
        return [r["line"] for r in rows]

    # ── mutations (mirror Task exactly) ──
    def add_log(self, line: str) -> None:
        _exec("INSERT INTO task_logs (task_id, ts, line) VALUES (?,?,?)",
              (self.task_id, int(time.time()), line))

    def set_step(self, step: int, status: TaskStatus = TaskStatus.RUNNING) -> None:
        _exec("UPDATE tasks SET current_step=?, status=?, updated_at=? WHERE task_id=?",
              (step, status.value, int(time.time()), self.task_id))

    def finish(self, status: TaskStatus, error: Optional[str] = None) -> None:
        # Wipe the credential payload the instant the job is terminal.
        _exec("UPDATE tasks SET status=?, error=?, updated_at=?, payload_enc=NULL WHERE task_id=?",
              (status.value, error, int(time.time()), self.task_id))

    # ── pub/sub (WS bridge) ──
    def subscribe(self) -> asyncio.Queue:
        """Queue pre-loaded with current state + log history, then tailed.

        Same contract as the in-memory Task: one ('step', …) frame, every buffered
        log line, and ('done',) when already terminal.
        """
        q: asyncio.Queue = asyncio.Queue()
        st = self._row()
        q.put_nowait(("step", int(st["current_step"]), st["status"], self.total_steps))

        last_id = 0
        for r in _rows(
            "SELECT id, line FROM (SELECT id, line FROM task_logs WHERE task_id=? "
            "ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
            (self.task_id, _LOG_TAIL_CAP),
        ):
            q.put_nowait(("log", r["line"]))
            last_id = r["id"]

        if st["status"] in (TaskStatus.SUCCESS.value, TaskStatus.FAILED.value):
            q.put_nowait(("done",))
            return q

        try:
            self._tailers[id(q)] = asyncio.get_running_loop().create_task(
                self._tail(q, last_id, int(st["current_step"]), st["status"])
            )
        except RuntimeError:
            # No running loop (sync caller) — the pre-loaded snapshot is all we
            # can offer; better than raising into a WS handshake.
            pass
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        t = self._tailers.pop(id(q), None)
        if t is not None:
            t.cancel()

    async def _tail(self, q: asyncio.Queue, last_id: int, step: int, status: str) -> None:
        while True:
            await asyncio.sleep(_POLL_INTERVAL)
            try:
                rows = _rows(
                    "SELECT id, line FROM task_logs WHERE task_id=? AND id>? ORDER BY id ASC LIMIT 500",
                    (self.task_id, last_id),
                )
                for r in rows:
                    q.put_nowait(("log", r["line"]))
                    last_id = r["id"]

                st = self._row()
                if int(st["current_step"]) != step or st["status"] != status:
                    step, status = int(st["current_step"]), st["status"]
                    q.put_nowait(("step", step, status, self.total_steps))
                if status in (TaskStatus.SUCCESS.value, TaskStatus.FAILED.value):
                    # Drain before closing. The log SELECT above is capped at 500
                    # rows and ran BEFORE the status read, so a final burst bigger
                    # than that — or lines committed in between — would otherwise
                    # be lost: ws.py breaks out of its loop on ('done',).
                    while True:
                        tail = _rows(
                            "SELECT id, line FROM task_logs WHERE task_id=? AND id>? "
                            "ORDER BY id ASC LIMIT 500",
                            (self.task_id, last_id),
                        )
                        if not tail:
                            break
                        for r in tail:
                            q.put_nowait(("log", r["line"]))
                            last_id = r["id"]
                    q.put_nowait(("done",))
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("shared_task_store.tail_failed task_id=%s", self.task_id)
                return


class SharedTaskStore:
    """Duck-type twin of `task_store.TaskStore`, plus the worker job queue."""

    mode = "shared"

    def create(self, total_steps: int = len(STEP_LABELS)) -> SharedTask:
        task_id = str(_uuid.uuid4())
        now = int(time.time())
        _exec(
            "INSERT INTO tasks (task_id, account_id, total_steps, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (task_id, accounts.current_account.get() or "", total_steps,
             TaskStatus.PENDING.value, now, now),
        )
        self._gc(now)
        return SharedTask(task_id, total_steps)

    def get(self, task_id: str) -> Optional[SharedTask]:
        r = _one("SELECT total_steps FROM tasks WHERE task_id=?", (task_id,))
        return SharedTask(task_id, int(r["total_steps"])) if r else None

    def cleanup(self, task_id: str) -> None:
        _exec("DELETE FROM task_logs WHERE task_id=?", (task_id,))
        _exec("DELETE FROM tasks WHERE task_id=?", (task_id,))

    def _gc(self, now: int) -> None:
        cutoff = now - _RETENTION_S
        try:
            _exec("DELETE FROM task_logs WHERE task_id IN "
                  "(SELECT task_id FROM tasks WHERE created_at < ?)", (cutoff,))
            _exec("DELETE FROM tasks WHERE created_at < ?", (cutoff,))
        except Exception:
            log.exception("shared_task_store.gc_failed")

    # ── job queue ──
    def enqueue(self, task_id: str, kind: str, payload: dict) -> None:
        """Mark an existing task as a queued job for the worker to claim."""
        _exec("UPDATE tasks SET kind=?, payload_enc=? WHERE task_id=?",
              (kind, _encrypt(payload), task_id))

    def claim_next(self, kinds: list[str], holder: str) -> Optional[tuple[SharedTask, str, dict]]:
        """Atomically claim the oldest unclaimed job of one of `kinds`.

        The UPDATE ... WHERE claimed_at IS NULL is the atomic bit — two workers
        racing on the same row, only one gets rowcount 1.
        """
        if not kinds:
            return None
        placeholders = ",".join("?" for _ in kinds)
        # cancel_requested is honoured HERE: a job cancelled while still queued
        # must never start. `POST /api/deploy/stop` already answered ok for it.
        row = _one(
            f"SELECT task_id, kind, total_steps FROM tasks WHERE kind IN ({placeholders}) "
            "AND claimed_at IS NULL AND status=? AND cancel_requested=0 "
            "ORDER BY created_at ASC LIMIT 1",
            (*kinds, TaskStatus.PENDING.value),
        )
        if row is None:
            self._finish_cancelled_queued(kinds)
            return None
        cur = _exec("UPDATE tasks SET claimed_by=?, claimed_at=? WHERE task_id=? AND claimed_at IS NULL",
                    (holder, int(time.time()), row["task_id"]))
        if cur.rowcount != 1:
            return None                      # lost the race
        task = SharedTask(row["task_id"], int(row["total_steps"]))
        got = _one("SELECT payload_enc FROM tasks WHERE task_id=?", (row["task_id"],))
        payload = _decrypt(got["payload_enc"]) if got and got["payload_enc"] else None
        if payload is None:
            # The row is already claimed, so nobody else can ever advance it —
            # give it a verdict instead of leaving the card spinning forever.
            # Usually means ENCRYPTION_KEY differs between gateway and worker.
            task.add_log("\x1b[1;31m[СИСТЕМА] Не удалось расшифровать задание "
                         "(ENCRYPTION_KEY отличается?).\x1b[0m")
            task.finish(TaskStatus.FAILED, "Не удалось прочитать задание")
            return None
        return task, row["kind"], payload

    def _finish_cancelled_queued(self, kinds: list[str]) -> None:
        """Close out jobs cancelled before anyone claimed them."""
        placeholders = ",".join("?" for _ in kinds)
        for r in _rows(
            f"SELECT task_id FROM tasks WHERE kind IN ({placeholders}) AND claimed_at IS NULL "
            "AND status=? AND cancel_requested=1",
            (*kinds, TaskStatus.PENDING.value),
        ):
            _exec("UPDATE tasks SET status=?, error=?, updated_at=?, payload_enc=NULL WHERE task_id=?",
                  (TaskStatus.FAILED.value, "Остановлено пользователем",
                   int(time.time()), r["task_id"]))

    def reap_orphans(self, alive_holders: set) -> int:
        """Fail rows claimed by a process that no longer exists (see
        job_runner.reap_orphans for how 'no longer exists' is decided)."""
        rows = _rows(
            "SELECT task_id, claimed_by FROM tasks WHERE claimed_by IS NOT NULL AND status IN (?,?)",
            (TaskStatus.PENDING.value, TaskStatus.RUNNING.value),
        )
        n = 0
        for r in rows:
            if r["claimed_by"] in alive_holders:
                continue
            _exec("INSERT INTO task_logs (task_id, ts, line) VALUES (?,?,?)",
                  (r["task_id"], int(time.time()),
                   "\n\x1b[1;31m[СИСТЕМА] Рабочий процесс, выполнявший задачу, "
                   "недоступен — задача прервана.\x1b[0m"))
            _exec("UPDATE tasks SET status=?, error=?, updated_at=?, payload_enc=NULL WHERE task_id=?",
                  (TaskStatus.FAILED.value, "Рабочий процесс недоступен",
                   int(time.time()), r["task_id"]))
            n += 1
        if n:
            log.warning("shared_task_store.reaped_orphans n=%d", n)
        return n

    def request_cancel(self, task_id: str) -> bool:
        """Ask a worker in another process to stop. True when the task exists
        and is still running (so the caller can answer 404 otherwise)."""
        cur = _exec("UPDATE tasks SET cancel_requested=1 WHERE task_id=? AND status IN (?,?)",
                    (task_id, TaskStatus.PENDING.value, TaskStatus.RUNNING.value))
        return cur.rowcount == 1

    def cancel_requested(self, task_id: str) -> bool:
        r = _one("SELECT cancel_requested FROM tasks WHERE task_id=?", (task_id,))
        return bool(r and r["cancel_requested"])

    def stats(self) -> dict[str, Any]:
        r = _one("SELECT COUNT(*) AS n FROM tasks WHERE status IN (?,?)",
                 (TaskStatus.PENDING.value, TaskStatus.RUNNING.value))
        q = _one("SELECT COUNT(*) AS n FROM tasks WHERE kind!='' AND claimed_at IS NULL AND status=?",
                 (TaskStatus.PENDING.value,))
        return {"mode": self.mode, "active": int(r["n"]) if r else 0,
                "queued": int(q["n"]) if q else 0}


def enabled() -> bool:
    return os.getenv("TASK_STORE", "memory").strip().lower() == "shared"
