"""Wave-5 Plan D — user config templates CRUD + optional Remnawave export/import.

Local source of truth (accounts/<id>/config_templates.json); the Remnawave
subscription-templates export/import is optional and gated on a configured panel.
"""
from __future__ import annotations

import base64

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.models.config_templates import (
    ConfigTemplateBody, KIND_TO_ENUM, ENUM_TO_KIND, YAML_KINDS,
)
from app.services import config_templates_store as store
from app.services import panel_registry
from app.services.remnawave_client import RemnavaveClient, RemnavaveError

router = APIRouter(prefix="/api/config-templates")


def _client(panel_id: str = "") -> RemnavaveClient:
    """Empty `panel_id` keeps the pre-Wave-7 behaviour (the active panel)."""
    try:
        return panel_registry.client_for(panel_id)
    except panel_registry.PanelNotFound:
        raise HTTPException(404, "Панель не найдена")
    except panel_registry.PanelNotConfigured:
        raise HTTPException(400, "Remnawave не настроен")


class ReorderBody(BaseModel):
    ids: list[str]


# ── local CRUD ────────────────────────────────────────────────
@router.get("")
async def list_templates():
    return store.list_templates()


@router.post("", status_code=201)
async def create_template(body: ConfigTemplateBody):
    try:
        return store.add_template(body.model_dump())
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.put("/{template_id}")
async def update_template(template_id: str, body: ConfigTemplateBody):
    updated = store.update_template(template_id, body.model_dump())
    if not updated:
        raise HTTPException(404, "Шаблон не найден")
    return updated


@router.delete("/{template_id}", status_code=204)
async def delete_template(template_id: str):
    if not store.delete_template(template_id):
        raise HTTPException(404, "Шаблон не найден")


@router.post("/reorder")
async def reorder(body: ReorderBody):
    return store.reorder_templates(body.ids)


# ── Remnawave export/import (optional; needs a configured panel) ──
@router.post("/{template_id}/export")
async def export_to_panel(template_id: str, panel_id: str = ""):
    tpl = store.get_template(template_id)
    if not tpl:
        raise HTTPException(404, "Шаблон не найден")
    kind = tpl.get("kind")
    client = _client(panel_id)
    try:
        created = await client.create_subscription_template(tpl["name"], KIND_TO_ENUM[kind])
        uuid = created.get("uuid")
        if kind in YAML_KINDS:
            enc = base64.b64encode((tpl.get("content_yaml") or "").encode("utf-8")).decode("ascii")
            await client.update_subscription_template(uuid, encoded_template_yaml=enc)
        else:
            await client.update_subscription_template(uuid, template_json=tpl.get("content_json") or {})
        return {"uuid": uuid}
    except RemnavaveError as e:
        raise HTTPException(502, f"Remnawave: {e}")


@router.get("/import/panel")
async def list_panel_templates(panel_id: str = ""):
    client = _client(panel_id)
    try:
        data = await client.list_subscription_templates()
    except RemnavaveError as e:
        raise HTTPException(502, f"Remnawave: {e}")
    # Echo which panel answered so the UI never has to guess whose list it shows.
    resolved = panel_registry.resolve(panel_id).id
    if isinstance(data, dict):
        return {**data, "panel_id": resolved}
    return {"templates": data, "panel_id": resolved}


@router.post("/import/panel/{uuid}", status_code=201)
async def import_from_panel(uuid: str, panel_id: str = ""):
    client = _client(panel_id)
    try:
        tpl = await client.get_subscription_template(uuid)
    except RemnavaveError as e:
        raise HTTPException(502, f"Remnawave: {e}")
    kind = ENUM_TO_KIND.get(tpl.get("templateType"))
    if not kind:
        raise HTTPException(422, f"Неизвестный тип шаблона: {tpl.get('templateType')}")
    body = {"name": tpl.get("name") or "imported", "kind": kind, "note": None,
            "content_json": None, "content_yaml": None}
    if kind in YAML_KINDS:
        enc = tpl.get("encodedTemplateYaml") or ""
        try:
            body["content_yaml"] = base64.b64decode(enc).decode("utf-8") if enc else ""
        except Exception:
            body["content_yaml"] = ""
    else:
        body["content_json"] = tpl.get("templateJson") or {}
    return store.add_template(body)
