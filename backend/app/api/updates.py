"""Wave-8 §3 — «Обновления» routes (global self-update, under `require_account`).

`GET /api/updates/status`  — current branch/commit + whether the remote is ahead.
`POST /api/updates/config` — auto-update toggle + tracked branch/image (global).
`POST /api/updates/apply`  — launch the detached DooD self-update sidecar.

⚠️ Global/host-level: ANY authenticated account can trigger a host-wide rebuild
+ restart (like the other DooD singletons). Docker/git absent → 200 with a
warning, never 500.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.services import updater

router = APIRouter(prefix="/api/updates")


class UpdatesConfig(BaseModel):
    auto_update: bool = False
    branch: str = ""
    image: str = ""


@router.get("/status")
async def status():
    st = await updater.check()
    st["progress"] = updater.read_status()
    return st


@router.post("/config")
async def save_config(body: UpdatesConfig):
    cfg = updater.save_config(body.auto_update, body.branch, body.image)
    return {"ok": True, **cfg}


@router.post("/apply")
async def apply():
    res = await updater.apply()
    # Warning (Docker absent / start failed) is a 200 with a warning, matching
    # the MCP/nodeflow singletons — not a 500.
    return res
