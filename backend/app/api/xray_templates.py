"""Прокси к шаблонам подписки Remnawave для раздела «Авто» (Wave-4 PR-7).

Список/чтение/создание/запись XRAY_JSON-шаблонов. Создание — двухшаговое у
панели (POST создаёт пустой, PATCH пишет контент) — клиент это уже умеет.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.downstream import downstream_exception
from app.services import panel_registry
from app.services.remnawave_client import RemnavaveClient, RemnavaveError

router = APIRouter(prefix="/api/xray-templates")


def _client(panel_id: str = "") -> RemnavaveClient:
    try:
        return panel_registry.client_for(panel_id)
    except panel_registry.PanelNotFound:
        raise HTTPException(404, "Панель не найдена")
    except panel_registry.PanelNotConfigured:
        raise HTTPException(400, "Remnawave не настроен")


@router.get("")
async def list_templates(panel_id: str = ""):
    client = _client(panel_id)
    try:
        tpls = await client.list_subscription_templates()
    except RemnavaveError as exc:
        raise downstream_exception(exc.status, exc.detail, "Панель Remnawave")
    return {"templates": [
        {"uuid": t.get("uuid"), "name": t.get("name"), "templateType": t.get("templateType")}
        for t in tpls if t.get("templateType") == "XRAY_JSON"
    ]}


@router.get("/{uuid}")
async def get_template(uuid: str, panel_id: str = ""):
    client = _client(panel_id)
    try:
        tpl = await client.get_subscription_template(uuid)
    except RemnavaveError as exc:
        raise downstream_exception(exc.status, exc.detail, "Панель Remnawave")
    return tpl


class CreateBody(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    panel_id: str = ""


@router.post("", status_code=201)
async def create_template(body: CreateBody):
    client = _client(body.panel_id)
    try:
        tpl = await client.create_subscription_template(body.name, "XRAY_JSON")
    except RemnavaveError as exc:
        raise downstream_exception(exc.status, exc.detail, "Панель Remnawave")
    return tpl


class UpdateBody(BaseModel):
    template_json: dict = Field(default_factory=dict)
    panel_id: str = ""


@router.put("/{uuid}")
async def update_template(uuid: str, body: UpdateBody):
    client = _client(body.panel_id)
    try:
        tpl = await client.update_subscription_template(uuid, template_json=body.template_json)
    except RemnavaveError as exc:
        raise downstream_exception(exc.status, exc.detail, "Панель Remnawave")
    return tpl
