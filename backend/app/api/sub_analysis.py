"""Wave-8 §7 — «Анализ подписки» routes (per-account, session-gated).

`POST /api/subscription-analyze`           → analyse a URL/domain/IP (dry-run).
`POST /api/subscription-analyze/to-hostings` → one hosting per ASN (upsert).

Fetch + parse happen SERVER-side: share links carry credentials, and a browser
fetch would hit CORS. Never echoes the input on error.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.models.hostings import HostingBody
from app.services import subscription_analyze as analyzer
from app.services import hostings_store as store

router = APIRouter(prefix="/api/subscription-analyze")


class AnalyzeReq(BaseModel):
    input: str = ""
    # Фиксированный User-Agent из селектора UI (пусто → цепочка «Авто»).
    user_agent: str = ""


class AsnResult(BaseModel):
    number: int = 0
    name: str = ""
    website: str = ""
    website_source: str = ""   # rdap | peeringdb | "" — откуда сайт (диагностика)


class GeoActual(BaseModel):
    cc: str = ""
    city: str = ""


class GeoRegistry(BaseModel):
    cc: str = ""


class NetResult(BaseModel):
    org: str = ""
    isp: str = ""
    ptr: str = ""
    hosting: bool = False
    proxy: bool = False


class AnalyzeResult(BaseModel):
    host: str = ""
    hosts: list[str] = []          # all addresses that resolved to this IP
    names: list[str] = []          # subscription link names for this host
    ip: str = ""
    asn: AsnResult = AsnResult()
    geo_actual: GeoActual = GeoActual()
    geo_registry: GeoRegistry = GeoRegistry()
    net: NetResult = NetResult()


class ToHostingsReq(BaseModel):
    results: list[AnalyzeResult] = []


@router.post("")
async def analyze(body: AnalyzeReq) -> dict[str, Any]:
    raw = (body.input or "").strip()
    if not raw:
        raise HTTPException(400, "Укажите URL подписки, домен или IP")
    try:
        results = await analyzer.analyze(raw, body.user_agent or "")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception:
        raise HTTPException(502, "Не удалось проанализировать подписку")
    return {"kind": analyzer.classify_input(raw), "results": results}


def _asn_of(h: dict) -> set[int]:
    return {int(a.get("number") or 0) for a in (h.get("asns") or []) if a.get("number")}


def _merge_locations(existing: list[dict], incoming: list[dict]) -> list[dict]:
    seen = {(l.get("country_code", ""), l.get("city", "")) for l in existing}
    out = list(existing)
    for l in incoming:
        key = (l.get("country_code", ""), l.get("city", ""))
        if key not in seen:
            seen.add(key)
            out.append(l)
    return out


@router.post("/to-hostings")
async def to_hostings(body: ToHostingsReq) -> dict[str, Any]:
    """Group the analysis results by ASN and upsert a hosting per ASN. Dedup by
    matching ASN number (or name): an existing hosting gets the new locations
    merged in; a new ASN becomes a new hosting card."""
    results = [r.model_dump() for r in body.results]
    bodies = analyzer.group_to_hostings(results)
    if not bodies:
        raise HTTPException(400, "В результатах нет ASN для добавления")

    existing = store.list_hostings()
    created, updated = 0, 0
    for raw_body in bodies:
        # Validate/normalise through the model (tags/asns limits, etc.).
        body_dict = HostingBody(**raw_body).model_dump()
        new_asns = {a["number"] for a in body_dict["asns"] if a.get("number")}
        match = None
        for h in existing:
            if (new_asns & _asn_of(h)) or (h.get("name") and h["name"] == body_dict["name"]):
                match = h
                break
        if match:
            merged = {**match}
            merged["locations"] = _merge_locations(match.get("locations", []), body_dict["locations"])
            if not merged.get("asns"):
                merged["asns"] = body_dict["asns"]
            if not merged.get("website"):
                merged["website"] = body_dict["website"]
            store.update_hosting(match["id"], HostingBody(**{k: v for k, v in merged.items()
                                                             if k not in ("id", "created_at")}).model_dump())
            updated += 1
        else:
            store.add_hosting(body_dict)
            created += 1
    return {"ok": True, "created": created, "updated": updated}
