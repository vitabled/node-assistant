"""API «Подсети» (Обходы БС, Wave-5 PR-5). Хранилище — services/subnets_store."""
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import latency_lab
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


# ── Latency Lab: замер подсетей списка ────────────────────────
#
# Потолок на пачку. Мультискан — ОДИН запрос суточного лимита независимо от
# числа целей, а вот поштучный `subnet-scan` тратит по запросу на подсеть,
# поэтому случайный «выделить всё» на списке в 500 строк обнулил бы лимит
# аккаунта одним нажатием.
MAX_SCAN_SUBNETS = 64


class LatencyScanBody(BaseModel):
    provider_id: str
    list_id: str
    row_ids: list[str] = Field(default_factory=list)
    all: bool = False
    #: Пусто = мультискан по всем online-операторам (1 запрос лимита);
    #: конкретный оператор = поштучный subnet-scan.
    operator: str = ""
    async_: bool = Field(False, alias="async_")

    model_config = {"populate_by_name": True}


def _collect_subnets(provider_id: str, list_id: str, row_ids: list[str],
                     take_all: bool) -> list[str]:
    """Подсети выбранных строк: колонка `subnet`, иначе первая колонка списка."""
    data = store.get_store()
    lst = next((l for p in data["providers"] if p["id"] == provider_id
                for l in p.get("lists", []) if l["id"] == list_id), None)
    if not lst:
        raise HTTPException(404, "Список не найден")
    rows = lst.get("rows", [])
    if not take_all:
        wanted = set(row_ids)
        rows = [r for r in rows if r.get("id") in wanted]
    if not rows:
        raise HTTPException(404, "Строки не найдены")
    keys = [c["key"] for c in lst.get("columns", [])] or ["subnet"]
    key = "subnet" if "subnet" in keys else keys[0]
    out: list[str] = []
    for row in rows:
        value = (row.get("values", {}).get(key) or "").strip()
        if value and value not in out:
            out.append(value)
    if not out:
        raise HTTPException(400, "В выбранных строках нет подсетей")
    return out


def _latency_client():
    """Клиент Latency Lab или 400: ключ обязателен и интеграция включена."""
    cfg = latency_lab.config()
    if not cfg.enabled:
        raise HTTPException(400, "Latency Lab выключен в настройках")
    if not cfg.api_key_enc:
        raise HTTPException(400, "Не задан API-ключ Latency Lab")
    client = latency_lab.client()
    if client is None:
        raise HTTPException(400, "Не удалось расшифровать API-ключ Latency Lab")
    return client


@router.post("/latency-scan")
async def latency_scan(body: LatencyScanBody):
    """Замер выбранных подсетей через Latency Lab.

    Без оператора — мультискан (все online-операторы, один запрос лимита);
    с оператором — поштучный `subnet-scan` по каждой подсети.
    """
    client = _latency_client()
    cfg = latency_lab.config()
    subnets = _collect_subnets(body.provider_id, body.list_id,
                               body.row_ids, body.all)
    if len(subnets) > MAX_SCAN_SUBNETS:
        raise HTTPException(
            400, f"За раз не больше {MAX_SCAN_SUBNETS} подсетей "
                 f"(выбрано {len(subnets)})")

    operator = latency_lab.normalize_operator(body.operator or cfg.default_operator)
    if operator and operator not in latency_lab.OPERATORS:
        raise HTTPException(400, f"Неизвестный оператор: {operator}")

    jobs: list[dict] = []
    errors: list[str] = []

    if not operator:
        # Мультискан принимает цели одним текстом — это ровно один запрос
        # суточного лимита на всю пачку.
        data, err = await client.multiscan("\n".join(subnets),
                                           is_async=body.async_)
        if err:
            raise HTTPException(502, f"Latency Lab: {err}")
        data = data or {}
        jobs.append({"targets": subnets, "req_id": data.get("req_id", ""),
                     "status": data.get("status", "done"),
                     "result": data.get("result")})
    else:
        for subnet in subnets:
            data, err = await client.subnet_scan(operator, subnet,
                                                 is_async=body.async_)
            if err:
                errors.append(f"{subnet}: {err}")
                continue
            data = data or {}
            jobs.append({"targets": [subnet], "req_id": data.get("req_id", ""),
                         "status": data.get("status", "done"),
                         "result": data.get("result")})
        if not jobs:
            raise HTTPException(502, "Latency Lab: " + "; ".join(errors[:3]))

    return {"ok": True,
            "mode": "multiscan" if not operator else "subnet-scan",
            "operator": operator, "async": body.async_,
            "jobs": jobs, "errors": errors}


@router.get("/latency-scan/{req_id}")
async def latency_scan_status(req_id: str):
    client = _latency_client()
    data, err = await client.job_status(req_id)
    if err:
        return {"ok": False, "error": err, "req_id": req_id}
    data = data or {}
    return {"ok": True, "req_id": req_id, "status": data.get("status", ""),
            "result": data.get("result")}


class LatencyCancelBody(BaseModel):
    req_id: str


@router.post("/latency-scan/cancel")
async def latency_scan_cancel(body: LatencyCancelBody):
    client = _latency_client()
    data, err = await client.cancel(body.req_id)
    if err:
        return {"ok": False, "error": err, "req_id": body.req_id}
    return {"ok": True, "req_id": body.req_id,
            "result": (data or {}).get("result")}
