"""API «Копия сайта» (Wave-4 PR-11): POST /api/sitecopy → фоновая задача.

Прогресс — через общий task-stream (/ws/logs). По завершении zip лежит в
Библиотеке (library_store) — пользователь просто открывает раздел «Библиотека».
"""
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.services import accounts, net_guard, sitecopy
from app.services.task_store import task_store

router = APIRouter(prefix="/api/sitecopy")

_MAX_DEPTH = 5
_MAX_MB = 200


class SiteCopyBody(BaseModel):
    url: str
    depth: int = Field(default=2, ge=1, le=_MAX_DEPTH)
    max_mb: int = Field(default=80, ge=1, le=_MAX_MB)

    @field_validator("url")
    @classmethod
    def _v_url(cls, v: str) -> str:
        v = v.strip()
        if not v.lower().startswith(("http://", "https://")):
            raise ValueError("Нужен http(s) URL")
        if not net_guard.is_safe_url(v):
            raise ValueError("URL не разрешён: нужен http(s) с публичным хостом")
        return v


@router.post("")
async def start_sitecopy(body: SiteCopyBody, background_tasks: BackgroundTasks):
    task = task_store.create(total_steps=len(sitecopy.SITE_COPY_STEPS))
    account_id = accounts.current_account.get() or ""
    background_tasks.add_task(
        sitecopy.run_sitecopy, body.url,
        depth=body.depth, max_bytes=body.max_mb * 1024 * 1024,
        task=task, account_id=account_id,
    )
    return {"task_id": task.task_id, "task_type": "sitecopy"}
