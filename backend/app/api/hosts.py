"""Per-account local Remnawave-host templates (Ф11).

CRUD over a per-account `hosts.json` store. No Remnawave API — templates are just
persisted (applied later at deploy time). Session-gated per-account.
"""
import uuid

from fastapi import APIRouter, HTTPException

from app.models.hosts import HostTemplateBody
from app.services import storage

router = APIRouter(prefix="/api/hosts")


@router.get("")
async def list_hosts():
    return storage.load_hosts()


@router.post("", status_code=201)
async def create_host(body: HostTemplateBody):
    hosts = storage.load_hosts()
    entry = {"id": uuid.uuid4().hex[:12], **body.model_dump()}
    hosts.append(entry)
    storage.save_hosts(hosts)
    return entry


@router.put("/{host_id}")
async def update_host(host_id: str, body: HostTemplateBody):
    hosts = storage.load_hosts()
    idx = next((i for i, h in enumerate(hosts) if h["id"] == host_id), None)
    if idx is None:
        raise HTTPException(404, "Шаблон хоста не найден")
    hosts[idx] = {"id": host_id, **body.model_dump()}
    storage.save_hosts(hosts)
    return hosts[idx]


@router.delete("/{host_id}", status_code=204)
async def delete_host(host_id: str):
    hosts = storage.load_hosts()
    storage.save_hosts([h for h in hosts if h["id"] != host_id])
