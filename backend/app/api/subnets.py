"""API «Подсети» (Обходы БС, Wave-5 PR-5). Хранилище — services/subnets_store."""
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import subnets_store as store

router = APIRouter(prefix="/api/subnets")


def _not_found(exc: KeyError) -> HTTPException:
    return HTTPException(404, f"Не найдено: {exc}")


@router.get("")
async def get_all():
    return {**store.get_store(), "operators": store.OPERATORS}


class ProviderBody(BaseModel):
    name: str = ""


@router.post("/providers", status_code=201)
async def create_provider(body: ProviderBody):
    return store.add_provider(body.name)


@router.patch("/providers/{provider_id}")
async def rename_provider(provider_id: str, body: ProviderBody):
    try:
        store.rename_provider(provider_id, body.name)
    except KeyError as e:
        raise _not_found(e)
    return {"ok": True}


@router.delete("/providers/{provider_id}", status_code=204)
async def delete_provider(provider_id: str):
    store.delete_provider(provider_id)


class ListBody(BaseModel):
    name: str = ""


@router.post("/providers/{provider_id}/lists", status_code=201)
async def create_list(provider_id: str, body: ListBody):
    try:
        return store.add_list(provider_id, body.name)
    except KeyError as e:
        raise _not_found(e)


@router.patch("/providers/{provider_id}/lists/{list_id}")
async def rename_list(provider_id: str, list_id: str, body: ListBody):
    try:
        store.rename_list(provider_id, list_id, body.name)
    except KeyError as e:
        raise _not_found(e)
    return {"ok": True}


@router.delete("/providers/{provider_id}/lists/{list_id}", status_code=204)
async def delete_list(provider_id: str, list_id: str):
    try:
        store.delete_list(provider_id, list_id)
    except KeyError as e:
        raise _not_found(e)


class RowsBody(BaseModel):
    subnets: list[str] = Field(..., min_length=1)


@router.post("/providers/{provider_id}/lists/{list_id}/rows", status_code=201)
async def add_rows(provider_id: str, list_id: str, body: RowsBody):
    try:
        return store.add_rows(provider_id, list_id, body.subnets)
    except KeyError as e:
        raise _not_found(e)


@router.delete("/providers/{provider_id}/lists/{list_id}/rows/{row_id}", status_code=204)
async def delete_row(provider_id: str, list_id: str, row_id: str):
    try:
        store.delete_row(provider_id, list_id, row_id)
    except KeyError as e:
        raise _not_found(e)


class CellBody(BaseModel):
    value: str = ""


@router.patch("/providers/{provider_id}/lists/{list_id}/rows/{row_id}/cell/{col_key}")
async def set_cell(provider_id: str, list_id: str, row_id: str, col_key: str, body: CellBody):
    try:
        store.set_cell(provider_id, list_id, row_id, col_key, body.value)
    except KeyError as e:
        raise _not_found(e)
    return {"ok": True}


class OperatorBody(BaseModel):
    on: bool = True


@router.patch("/providers/{provider_id}/lists/{list_id}/rows/{row_id}/operator/{op_key}")
async def toggle_operator(provider_id: str, list_id: str, row_id: str, op_key: str, body: OperatorBody):
    try:
        store.toggle_operator(provider_id, list_id, row_id, op_key, body.on)
    except KeyError as e:
        raise _not_found(e)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"ok": True}


class ColumnBody(BaseModel):
    title: str = ""


@router.post("/providers/{provider_id}/lists/{list_id}/columns", status_code=201)
async def add_column(provider_id: str, list_id: str, body: ColumnBody):
    try:
        return store.add_column(provider_id, list_id, body.title)
    except KeyError as e:
        raise _not_found(e)


@router.patch("/providers/{provider_id}/lists/{list_id}/columns/{col_key}")
async def rename_column(provider_id: str, list_id: str, col_key: str, body: ColumnBody):
    try:
        store.rename_column(provider_id, list_id, col_key, body.title)
    except KeyError as e:
        raise _not_found(e)
    return {"ok": True}


@router.delete("/providers/{provider_id}/lists/{list_id}/columns/{col_key}", status_code=204)
async def delete_column(provider_id: str, list_id: str, col_key: str):
    try:
        store.delete_column(provider_id, list_id, col_key)
    except KeyError as e:
        raise _not_found(e)
    except ValueError as e:
        raise HTTPException(422, str(e))


class ColOrderBody(BaseModel):
    order: list[str] = []


@router.put("/providers/{provider_id}/lists/{list_id}/columns-order")
async def reorder_columns(provider_id: str, list_id: str, body: ColOrderBody):
    try:
        store.reorder_columns(provider_id, list_id, body.order)
    except KeyError as e:
        raise _not_found(e)
    return {"ok": True}


# ── обогащение ASN (ip-api, как в «Анализе подписки») ─────────
class EnrichBody(BaseModel):
    row_ids: list[str] = Field(..., min_length=1)


@router.post("/providers/{provider_id}/lists/{list_id}/enrich")
async def enrich_rows(provider_id: str, list_id: str, body: EnrichBody):
    """ASN/название ASN для строк по подсети (ip-api по сетевому адресу)."""
    from app.services.subscription_analyze import _parse_as_field

    data = store.get_store()
    lst = next((l for p in data["providers"] if p["id"] == provider_id
                for l in p.get("lists", []) if l["id"] == list_id), None)
    if not lst:
        raise HTTPException(404, "Список не найден")
    rows = [r for r in lst.get("rows", []) if r.get("id") in set(body.row_ids)]
    if not rows:
        raise HTTPException(404, "Строки не найдены")

    updated = 0
    async with httpx.AsyncClient(timeout=8) as client:
        for row in rows:
            subnet = row.get("values", {}).get("subnet", "")
            ip = subnet.split("/")[0]
            try:
                r = await client.get(
                    f"http://ip-api.com/json/{ip}",
                    params={"fields": "status,as,asname,org"},
                )
                d = r.json()
            except Exception:
                continue
            if not isinstance(d, dict) or d.get("status") != "success":
                continue
            num, name = _parse_as_field(str(d.get("as") or ""))
            asnname = str(d.get("asname") or name or d.get("org") or "")
            store.update_row_asn(provider_id, list_id, row["id"],
                                 f"AS{num}" if num else "", asnname)
            updated += 1
    return {"updated": updated, "of": len(rows)}
