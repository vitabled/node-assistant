"""API раздела «Обходы БС» (Wave-4 PR-9).

`GET  /api/obhod/hosts`          — хосты панели для пикера Beeline-инструмента.
`POST /api/obhod/beeline/apply`  — проставить CDN-домен в sni/host выбранных хостов.
REGRU-инструмент backend'а не требует: работает через существующий
`/api/replace-domain/node`.
"""
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.api.downstream import downstream_exception
from app.services import panel_registry
from app.services.remnawave_client import RemnavaveClient, RemnavaveError

router = APIRouter(prefix="/api/obhod")

_DOMAIN_RE = re.compile(
    r"^[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)*\.[A-Za-z]{2,}$"
)


def _client(panel_id: str = "") -> RemnavaveClient:
    try:
        return panel_registry.client_for(panel_id)
    except panel_registry.PanelNotFound:
        raise HTTPException(404, "Панель не найдена")
    except panel_registry.PanelNotConfigured:
        raise HTTPException(400, "Remnawave не настроен")


@router.get("/hosts")
async def list_hosts(panel_id: str = ""):
    client = _client(panel_id)
    try:
        hosts = await client.list_hosts()
    except RemnavaveError as exc:
        raise downstream_exception(exc.status, exc.detail, "Панель Remnawave")
    return {"hosts": [
        {
            "uuid": h.get("uuid"),
            "remark": h.get("remark"),
            "address": h.get("address"),
            "port": h.get("port"),
            "sni": h.get("sni"),
            "host": h.get("host"),
            "isDisabled": h.get("isDisabled"),
        }
        for h in hosts
    ]}


class BeelineApplyBody(BaseModel):
    host_uuids: list[str] = Field(..., min_length=1)
    domain: str
    panel_id: str = ""

    @field_validator("domain")
    @classmethod
    def _v_domain(cls, v: str) -> str:
        v = v.strip().lower()
        if not _DOMAIN_RE.match(v):
            raise ValueError("Некорректный CDN-домен (нужен FQDN, например cdn.example.ru)")
        return v


@router.post("/beeline/apply")
async def beeline_apply(body: BeelineApplyBody):
    """CDN-домен Beeline → sni + host выбранных хостов панели."""
    client = _client(body.panel_id)
    applied, errors = [], []
    for uuid in body.host_uuids:
        try:
            await client.update_host(uuid, {"sni": body.domain, "host": body.domain})
            applied.append(uuid)
        except RemnavaveError as exc:
            if exc.status == 404:
                errors.append({"uuid": uuid, "error": "Хост не найден"})
            else:
                raise downstream_exception(exc.status, exc.detail, "Панель Remnawave")
        except Exception as exc:
            errors.append({"uuid": uuid, "error": str(exc)})
    return {"ok": not errors, "applied": applied, "errors": errors, "domain": body.domain}
