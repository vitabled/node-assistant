"""
In-memory task state store with typed pub/sub queue.

Queue item shapes:
  ("log",  text: str)                         — log line
  ("step", step: int, status: str, total: int) — step/status changed
  ("done",)                                   — stream finished (success or fail)
"""
import asyncio
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED  = "failed"


STEP_LABELS = [
    "Подключение к серверу",
    "Обновление системы",
    "Node Accelerator (оптимизация ОС)",
    "TrafficGuard (защита от сканеров)",
    "Оптимизация + dual-port SSH и перезагрузка",
    "Проверка SSH после перезагрузки (откат при сбое)",
    "Cloudflare DNS + Wildcard SSL",
    "Установка Remnanode",
    "WARP Native",
    "SSL Certbot (standalone)",
    "Уникализация маскировочного сайта",
]


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


task_store = TaskStore()
