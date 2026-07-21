"""
Task vocabulary shared by both task-store implementations.

A leaf module on purpose: `task_store` picks its implementation at import time
and therefore imports `shared_task_store`, so `shared_task_store` must NOT import
back from `task_store`. Keeping the enum and the step labels here breaks that
cycle. Everything historically imported these from `task_store`, which still
re-exports them.
"""
from enum import Enum


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
    "Тест-инструменты",
    "Добавление порта SSH",
    "Перезагрузка",
    "Проверка нового порта SSH",
    "Удаление старого порта SSH",
    "Cloudflare DNS + SSL",
    "Установка Remnanode",
    "Уникализация маскировочного сайта",
    "WARP Native",
    "Hysteria2",
]
