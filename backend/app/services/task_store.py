"""
Task state store with typed pub/sub queue.

Queue item shapes:
  ("log",  text: str)                         — log line
  ("step", step: int, status: str, total: int) — step/status changed
  ("done",)                                   — stream finished (success or fail)

Two implementations sit behind the module-level `task_store` singleton:
  * `TaskStore` (default) — in-process dict, exactly as before.
  * `SharedTaskStore`     — SQLite under DATA_DIR, so a worker in a SEPARATE
                            process can stream into a task this process serves
                            over `/ws/logs/{task_id}` (Plan M, `--profile split`).
Selected by env `TASK_STORE=memory|shared`; `memory` keeps the monolith byte-for-byte.
"""
import asyncio
import os
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

# Defined in a leaf module so `shared_task_store` can use them without importing
# this one (which would be a cycle — see task_types). Re-exported here because
# ~250 call sites import them from `app.services.task_store`.
from app.services.task_types import STEP_LABELS, TaskStatus  # noqa: F401


@dataclass
class Task:
    task_id:      str
    total_steps:  int
    current_step: int        = 0
    status:       TaskStatus = TaskStatus.PENDING
    error:        Optional[str] = None
    # Ring-buffer of raw log lines for late subscribers
    logs: deque = field(default_factory=lambda: deque(maxlen=2000))
    _subscribers: list = field(default_factory=list)

    # ------------------------------------------------------------------
    # Public mutation API (all mutations go through here so queues stay
    # in sync with the authoritative fields)
    # ------------------------------------------------------------------

    def add_log(self, line: str) -> None:
        self.logs.append(line)
        self._broadcast(("log", line))

    def set_step(self, step: int, status: TaskStatus = TaskStatus.RUNNING) -> None:
        self.current_step = step
        self.status = status
        self._broadcast(("step", step, status.value, self.total_steps))

    def finish(self, status: TaskStatus, error: Optional[str] = None) -> None:
        self.status = status
        self.error  = error
        self._broadcast(("step", self.current_step, status.value, self.total_steps))
        self._broadcast(("done",))

    # ------------------------------------------------------------------
    # Subscriber management
    # ------------------------------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        """Return a queue pre-loaded with current state + full log history."""
        q: asyncio.Queue = asyncio.Queue()
        # Current step/status first so the frontend can restore stepper state
        q.put_nowait(("step", self.current_step, self.status.value, self.total_steps))
        for line in self.logs:
            q.put_nowait(("log", line))
        if self.status in (TaskStatus.SUCCESS, TaskStatus.FAILED):
            q.put_nowait(("done",))
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    def _broadcast(self, item: tuple) -> None:
        for q in self._subscribers:
            q.put_nowait(item)


class TaskStore:
    mode = "memory"

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}

    def create(self, total_steps: int = len(STEP_LABELS)) -> Task:
        task_id = str(uuid.uuid4())
        task = Task(task_id=task_id, total_steps=total_steps)
        self._tasks[task_id] = task
        return task

    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def cleanup(self, task_id: str) -> None:
        self._tasks.pop(task_id, None)

    # ── parity with SharedTaskStore ──
    # In-process work is cancelled through its asyncio handle (see api/deploy.py),
    # so there is never a cross-process task to flag here.
    def request_cancel(self, task_id: str) -> bool:
        return False

    def cancel_requested(self, task_id: str) -> bool:
        return False

    def stats(self) -> dict[str, Any]:
        active = sum(1 for t in self._tasks.values()
                     if t.status in (TaskStatus.PENDING, TaskStatus.RUNNING))
        return {"mode": self.mode, "active": active, "queued": 0}


def _build_store():
    """Pick the implementation from env. Import is deferred to here so
    `shared_task_store` can import TaskStatus/STEP_LABELS from this module."""
    if os.getenv("TASK_STORE", "memory").strip().lower() == "shared":
        from app.services.shared_task_store import SharedTaskStore
        return SharedTaskStore()
    return TaskStore()


task_store = _build_store()
