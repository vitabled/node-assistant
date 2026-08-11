"""API мостов (Wave-4 PR-6). Оркестрация — в services/bridges.py."""
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.downstream import downstream_exception
from app.services import bridges as svc
from app.services import panel_registry
from app.services.remnawave_client import RemnavaveClient, RemnavaveError

router = APIRouter(prefix="/api/bridges")


def _client(panel_id: str = "") -> RemnavaveClient:
    try:
        return panel_registry.client_for(panel_id)
    except panel_registry.PanelNotFound:
        raise HTTPException(404, "Панель не найдена")
    except panel_registry.PanelNotConfigured:
        raise HTTPException(400, "Remnawave не настроен")


@router.get("")
async def list_bridges():
    return {"bridges": svc.list_bridges()}


@router.get("/options")
async def bridge_options(panel_id: str = ""):
    """Данные для селекторов формы: ноды (с их инбаундами) и конфиг-профили."""
    client = _client(panel_id)
    try:
        nodes = await client.list_nodes()
        profiles = await client.list_config_profiles()
    except RemnavaveError as exc:
        raise downstream_exception(exc.status, exc.detail, "Панель Remnawave")
    out_nodes = []
    for n in nodes:
        cp = n.get("configProfile") or {}
        inbounds = [
            {"uuid": i.get("uuid"), "tag": i.get("tag"), "type": i.get("type"),
             "network": i.get("network"), "security": i.get("security"), "port": i.get("port")}
            for i in (cp.get("activeInbounds") or [])
        ]
        out_nodes.append({
            "uuid": n.get("uuid"), "name": n.get("name"), "address": n.get("address"),
            "countryCode": n.get("countryCode"), "isDisabled": n.get("isDisabled"),
            "profileUuid": cp.get("activeConfigProfileUuid"),
            "inbounds": inbounds,
        })
    out_profiles = [{"uuid": p.get("uuid"), "name": p.get("name")} for p in profiles]
    return {"nodes": out_nodes, "profiles": out_profiles}


class BridgeMatchers(BaseModel):
    domain: list[str] = []
    ip: list[str] = []
    protocol: list[str] = []
    port: str = ""
    network: str = ""


class CreateBridgeBody(BaseModel):
    name: str = ""
    exit_node_uuid: str = Field(..., min_length=1)
    inbound_tags: list[str] = []
    profile_uuids: list[str] = []
    matchers: BridgeMatchers = BridgeMatchers()
    panel_id: str = ""


@router.post("", status_code=201)
async def create_bridge(body: CreateBridgeBody):
    client = _client(body.panel_id)
    try:
        nodes = await client.list_nodes()
    except RemnavaveError as exc:
        raise downstream_exception(exc.status, exc.detail, "Панель Remnawave")
    exit_node = next((n for n in nodes if n.get("uuid") == body.exit_node_uuid), None)
    if not exit_node:
        raise HTTPException(404, "Нода-выход не найдена")
    try:
        record = await svc.create_bridge(
            client,
            name=body.name,
            exit_node=exit_node,
            inbound_tags=body.inbound_tags,
            profile_uuids=body.profile_uuids,
            matchers=body.matchers.model_dump(),
        )
    except RemnavaveError as exc:
        raise downstream_exception(exc.status, exc.detail, "Панель Remnawave")
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return record


@router.delete("/{bridge_id}")
async def delete_bridge(bridge_id: str, panel_id: str = ""):
    client = _client(panel_id)
    try:
        return await svc.delete_bridge(client, bridge_id)
    except KeyError:
        raise HTTPException(404, "Мост не найден")
    except RemnavaveError as exc:
        raise downstream_exception(exc.status, exc.detail, "Панель Remnawave")
