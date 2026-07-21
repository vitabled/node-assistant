"""«Хостинги» catalogue routes (Wave-4 Plan A) — per-account CRUD.

An independent reference catalogue (not infra-billing): hosting cards with
tariffs/specs/locations. Locations carry lat/lng for the «Карта» section
(geocoding is client-side; the backend just persists the coords).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.models.hostings import HostingBody
from app.services import hostings_store as store

router = APIRouter(prefix="/api/hostings")


@router.get("")
async def list_hostings() -> list[dict[str, Any]]:
    return store.list_hostings()


@router.post("", status_code=201)
async def create_hosting(body: HostingBody) -> dict[str, Any]:
    try:
        return store.add_hosting(body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.put("/{hosting_id}")
async def update_hosting(hosting_id: str, body: HostingBody) -> dict[str, Any]:
    updated = store.update_hosting(hosting_id, body.model_dump())
    if updated is None:
        raise HTTPException(404, "Хостинг не найден")
    return updated


@router.delete("/{hosting_id}", status_code=204)
async def delete_hosting(hosting_id: str):
    if not store.delete_hosting(hosting_id):
        raise HTTPException(404, "Хостинг не найден")
