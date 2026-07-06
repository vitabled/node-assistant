"""Per-account manual-domain store for the «Управление SSL» domains window (Ф10).

The window auto-fills from the account's deploy_jobs (client-side localStorage);
these routes persist the MANUAL domains the user adds on top, server-side under
`accounts/<id>/domains.json`. Session-gated per-account.
"""
import re
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from app.services import storage

router = APIRouter(prefix="/api/domains")

_HOSTNAME_RE = re.compile(
    r"^[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)*\.[A-Za-z]{2,}$"
)


class DomainCreate(BaseModel):
    domain: str

    @field_validator("domain")
    @classmethod
    def _v(cls, v: str) -> str:
        v = v.strip().lower()
        if not _HOSTNAME_RE.match(v):
            raise ValueError("Некорректный домен")
        return v


@router.get("")
async def list_domains():
    return storage.load_domains()


@router.post("", status_code=201)
async def create_domain(body: DomainCreate):
    domains = storage.load_domains()
    if any(d["domain"] == body.domain for d in domains):
        raise HTTPException(409, "Домен уже добавлен")
    entry = {"id": uuid.uuid4().hex[:12], "domain": body.domain}
    domains.append(entry)
    storage.save_domains(domains)
    return entry


@router.delete("/{domain_id}", status_code=204)
async def delete_domain(domain_id: str):
    domains = storage.load_domains()
    storage.save_domains([d for d in domains if d["id"] != domain_id])
