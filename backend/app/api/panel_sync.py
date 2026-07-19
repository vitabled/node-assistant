"""
Panel sync API (Ф5). Account-gated.

- GET/POST   /api/sync/groups            — list / create a sync group.
- PATCH/DELETE /api/sync/groups/{id}     — edit / delete.
- POST       /api/sync/groups/{id}/run   — DESTRUCTIVE manual standby sync
  (confirm required). Streamed Task (backup on primary → restore on standby).

SSH creds for both panels are supplied per-request (from the client's panel_jobs)
and never persisted.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from app.services import accounts, panel_sync, sync_store
from app.services.task_store import task_store

router = APIRouter(prefix="/api/sync")


class Member(BaseModel):
    panel_key: str = Field(..., min_length=1)
    priority: int = 0
    role: str = "standby"


class GroupBody(BaseModel):
    name: str = Field("Группа", max_length=100)
    auto_sync: bool = False
    interval_hours: int = Field(24, ge=1, le=720)
    members: list[Member] = []


class GroupPatch(BaseModel):
    name: str | None = None
    auto_sync: bool | None = None
    interval_hours: int | None = Field(default=None, ge=1, le=720)
    members: list[Member] | None = None


class Creds(BaseModel):
    ip: str = Field(..., min_length=1)
    ssh_port: int = Field(22, ge=1, le=65535)
    ssh_user: str = "root"
    ssh_password: str = ""


class RunBody(BaseModel):
    standby_key: str = Field(..., min_length=1)
    primary_creds: Creds
    standby_creds: Creds
    confirm: bool = False


@router.get("/groups")
async def list_groups() -> list[dict]:
    return sync_store.load_groups()


@router.post("/groups", status_code=201)
async def create_group(body: GroupBody) -> dict:
    try:
        return sync_store.add_group(body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.patch("/groups/{group_id}")
async def patch_group(group_id: str, body: GroupPatch) -> dict:
    if not sync_store.get_group(group_id):
        raise HTTPException(404, "Группа не найдена")
    try:
        updated = sync_store.update_group(group_id, body.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return updated  # type: ignore[return-value]


@router.delete("/groups/{group_id}", status_code=204)
async def delete_group(group_id: str):
    if not sync_store.remove_group(group_id):
        raise HTTPException(404, "Группа не найдена")


@router.post("/groups/{group_id}/run")
async def run_group_sync(
    group_id: str, body: RunBody, background_tasks: BackgroundTasks
) -> dict:
    group = sync_store.get_group(group_id)
    if not group:
        raise HTTPException(404, "Группа не найдена")
    if not body.confirm:
        raise HTTPException(
            400,
            "Синхронизация ДЕСТРУКТИВНА для standby и требует подтверждения (confirm=true).",
        )
    # Validate the plan up front so an obvious misconfig fails fast (not mid-stream).
    try:
        panel_sync.plan_sync(group, body.standby_key)
    except panel_sync.SyncError as exc:
        raise HTTPException(422, str(exc))

    account_id = accounts.current_account.get() or ""
    task = task_store.create(total_steps=1)
    background_tasks.add_task(
        panel_sync.run_sync,
        task,
        group,
        body.standby_key,
        body.primary_creds.model_dump(),
        body.standby_creds.model_dump(),
        account_id,
    )
    return {"task_id": task.task_id, "task_type": "panel-sync"}
