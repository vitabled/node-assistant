"""Subscription-page configs of the Remnawave panel (Wave-7 Plan G Ф2).

A THIN proxy: the panel is the source of truth. It already offers clone and
reorder, so a local store would only add a second copy to keep in sync.

Contract verified against api-1.json (Remnawave v2.8.0); the traps are listed on
`RemnavaveClient`'s page-config block. The one worth repeating here: `config` is
an untyped field in both the OpenAPI dump and the TS contract, so we neither
validate nor default it — we pass through what the caller sent and let the panel
be the judge.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import panel_registry
from app.services.remnawave_client import RemnavaveClient, RemnavaveError

router = APIRouter(prefix="/api/subpage-configs")


def _client(panel_id: str = "") -> RemnavaveClient:
    """Empty `panel_id` → the account's main panel (Wave-7 Plan C).

    ⚠️ Not the `AppSettings(...).remnawave` factory the plan referenced: that
    read-the-active-panel shortcut was replaced by the registry resolver, which
    fails loudly on an unknown id instead of silently using the main panel."""
    try:
        return panel_registry.client_for(panel_id)
    except panel_registry.PanelNotFound:
        raise HTTPException(404, "Панель не найдена")
    except panel_registry.PanelNotConfigured:
        raise HTTPException(400, "Remnawave не настроен")


class CreateBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)


class UpdateBody(BaseModel):
    # Both optional: an omitted key is the only shape guaranteed not to touch the
    # panel's stored value (merge-vs-replace semantics are unspecified).
    name: Optional[str] = Field(default=None, max_length=60)
    config: Optional[Any] = None


class ReorderItem(BaseModel):
    uuid: str
    view_position: int = Field(..., ge=0, alias="viewPosition")

    model_config = {"populate_by_name": True}


class ReorderBody(BaseModel):
    items: list[ReorderItem]


async def _call(coro):
    try:
        return await coro
    except RemnavaveError as e:
        raise HTTPException(502, f"Remnawave: {e}")
    except httpx.HTTPError:
        # Panel unreachable / timeout / DNS — a gateway problem, not our 500.
        raise HTTPException(502, "Панель Remnawave недоступна")


@router.get("")
async def list_configs(panel_id: str = "") -> dict[str, Any]:
    # ⚠️ The panel's LIST returns `config: null` for EVERY entry (verified on a
    # live 2.x panel) — only the DETAIL endpoint below returns the real `config`.
    # An editor MUST fetch `GET /{uuid}` to get something to edit; the list is
    # names + viewPosition only. We pass through faithfully rather than N+1 the
    # detail on every list.
    data = await _call(_client(panel_id).list_subscription_page_configs())
    # Echo the panel that answered — the UI must never have to guess whose
    # designs it is showing (same contract as /api/config-templates/import/panel).
    return {**data, "panel_id": panel_registry.resolve(panel_id).id}


@router.get("/{uuid}")
async def get_config(uuid: str, panel_id: str = "") -> dict[str, Any]:
    # This is the ONLY place `config` comes back populated (see list_configs).
    return await _call(_client(panel_id).get_subscription_page_config(uuid))


@router.post("", status_code=201)
async def create_config(body: CreateBody, panel_id: str = "") -> dict[str, Any]:
    """Creates an EMPTY config — the panel's POST accepts only `name`.
    Content is written by a follow-up PUT."""
    return await _call(_client(panel_id).create_subscription_page_config(body.name))


@router.put("/{uuid}")
async def update_config(uuid: str, body: UpdateBody, panel_id: str = "") -> dict[str, Any]:
    # An empty/whitespace name is "clear the field", NOT a rename to the coerced
    # fallback "config" — treat it as absent so it can't silently overwrite the
    # design's name. (Wave-7 review, subpage_configs:96.)
    name = body.name.strip() if isinstance(body.name, str) else None
    if not name and body.config is None:
        raise HTTPException(422, "Нечего обновлять: укажите name и/или config")
    body.name = name or None
    return await _call(_client(panel_id).update_subscription_page_config(
        uuid, name=body.name, config=body.config,
    ))


@router.post("/{uuid}/clone", status_code=201)
async def clone_config(uuid: str, panel_id: str = "") -> dict[str, Any]:
    return await _call(_client(panel_id).clone_subscription_page_config(uuid))


@router.post("/reorder")
async def reorder_configs(body: ReorderBody, panel_id: str = "") -> dict[str, Any]:
    return await _call(_client(panel_id).reorder_subscription_page_configs(
        [{"uuid": i.uuid, "viewPosition": i.view_position} for i in body.items],
    ))


@router.delete("/{uuid}")
async def delete_config(uuid: str, panel_id: str = "") -> dict[str, Any]:
    return await _call(_client(panel_id).delete_subscription_page_config(uuid))
