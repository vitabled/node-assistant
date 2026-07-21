"""Wave-5 Plan I — system-prompt presets router.

Built-in presets are read-only; user presets are per-account. The ACTIVE preset
is selected via POST /api/ai/config (active_preset_id), not here.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import prompt_presets_store as presets

router = APIRouter(prefix="/api/ai/prompts")


class PresetBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    text: str = Field(..., min_length=1, max_length=20000)


@router.get("")
async def list_presets():
    return presets.list_presets()


@router.get("/{preset_id}")
async def get_preset(preset_id: str):
    p = presets.get_preset(preset_id)
    if not p:
        raise HTTPException(404, "Пресет не найден")
    return p


@router.post("", status_code=201)
async def create_preset(body: PresetBody):
    return presets.create_preset(body.name.strip(), body.text)


@router.put("/{preset_id}")
async def update_preset(preset_id: str, body: PresetBody):
    try:
        updated = presets.update_preset(preset_id, body.name.strip(), body.text)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not updated:
        raise HTTPException(404, "Пресет не найден")
    return updated


@router.delete("/{preset_id}", status_code=204)
async def delete_preset(preset_id: str):
    try:
        ok = presets.delete_preset(preset_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(404, "Пресет не найден")


@router.post("/{preset_id}/fork", status_code=201)
async def fork_preset(preset_id: str):
    try:
        return presets.fork_preset(preset_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
