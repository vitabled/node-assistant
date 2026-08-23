"""API «Подсети» (Обходы БС, Wave-5 PR-5). Хранилище — services/subnets_store."""
import csv
import io
import json
import time

import httpx
import openpyxl
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from openpyxl.styles import Font
from pydantic import BaseModel, Field, model_validator

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
    """Строки списка: старый формат {subnets: [...]} или строки с метаданными
    {rows: [{subnet, ...поля}]}. Хотя бы один из списков непустой."""
    subnets: list[str] = []
    rows: list[dict] = []

    @model_validator(mode="after")
    def _at_least_one(self):
        if not self.subnets and not self.rows:
            raise ValueError("Передайте subnets (подсети) или rows (строки с метаданными)")
        return self


@router.post("/providers/{provider_id}/lists/{list_id}/rows", status_code=201)
async def add_rows(provider_id: str, list_id: str, body: RowsBody):
    try:
        items = body.subnets if body.subnets else body.rows
        return store.add_rows(provider_id, list_id, items)
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


# ── пакетное обновление ячеек ──────────────────────────────────
MAX_BATCH_UPDATES = 500


class CellUpdateBody(BaseModel):
    row_id: str
    col: str
    value: str = ""


class BatchCellsBody(BaseModel):
    updates: list[CellUpdateBody] = Field(..., min_length=1, max_length=MAX_BATCH_UPDATES)


@router.patch("/providers/{provider_id}/lists/{list_id}/rows/batch")
async def batch_update_cells(provider_id: str, list_id: str, body: BatchCellsBody):
    """Пакетное обновление ячеек. Битые апдейты (нет строки/пустой col)
    не убивают batch: считаются в skipped и перечисляются в errors."""
    _pick_list(provider_id, list_id)  # 404, если списка нет
    updated, skipped = 0, 0
    errors: list[str] = []
    for u in body.updates:
        if not (u.row_id or "").strip() or not (u.col or "").strip():
            skipped += 1
            errors.append("row_id/col не могут быть пустыми")
            continue
        try:
            store.set_cell(provider_id, list_id, u.row_id, u.col, u.value)
            updated += 1
        except KeyError as e:
            skipped += 1
            errors.append(f"row {u.row_id}: не найдена ({e})")
        except Exception as e:  # битая строка не убивает batch
            skipped += 1
            errors.append(str(e))
    return {"ok": True, "updated": updated, "skipped": skipped, "errors": errors}


# ── JSON-импорт строк с метаданными ────────────────────────────
class ImportJsonBody(BaseModel):
    rows: list[dict] = Field(..., min_length=1)


@router.post("/providers/{provider_id}/lists/{list_id}/import-json")
async def import_json_rows(provider_id: str, list_id: str, body: ImportJsonBody):
    """Импорт строк {subnet, ...метаданные}. Колонки автоматически НЕ
    создаются — метаданные лежат в values строк, колонки заводит агент
    через POST /columns. Дубликаты подсетей — skip."""
    if len(body.rows) > MAX_IMPORT_ROWS:
        raise HTTPException(400, f"За раз не больше {MAX_IMPORT_ROWS} строк "
                                 f"(получено {len(body.rows)})")
    try:
        res = store.import_flat_rows(provider_id, list_id, body.rows)
    except KeyError:
        raise HTTPException(404, "Список не найден")
    return {"ok": True, **res}


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
    # Кастомный ключ колонки (совпадает с ключом поля в row.values) —
    # например "operator"/"asn"/"country". Пусто → генерируется col_xxx.
    key: str = ""


@router.post("/providers/{provider_id}/lists/{list_id}/columns", status_code=201)
async def add_column(provider_id: str, list_id: str, body: ColumnBody):
    try:
        return store.add_column(provider_id, list_id, body.title, key=body.key)
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


# ── импорт/экспорт (json/csv/txt) ─────────────────────────────
#
# Плоские форматы (csv/txt) описывают ровно один список, поэтому требуют
# list_id; json умеет и полный снимок дерева, и снимок одного списка.
MAX_IMPORT_ROWS = 5000
JSON_FORMAT = "na-subnets"
JSON_VERSION = 1
_DEFAULT_KEYS = {c["key"] for c in store.DEFAULT_COLUMNS}
_OP_KEYS = [o["key"] for o in store.OPERATORS]
_CSV_ALIASES = {"version": "ipver", "версия ip": "ipver", "подсеть": "subnet",
                "дата": "date", "название asn": "asnname"}
_MEDIA = {"json": "application/json; charset=utf-8",
          "csv": "text/csv; charset=utf-8",
          "txt": "text/plain; charset=utf-8",
          "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


def _pick_list(provider_id: str, list_id: str) -> tuple[dict, dict]:
    data = store.get_store()
    for p in data["providers"]:
        for l in p.get("lists", []):
            if l.get("id") == list_id and (not provider_id or p.get("id") == provider_id):
                return p, l
    raise HTTPException(404, "Список не найден")


def _flat_header(lst: dict) -> list[tuple[str, str]]:
    """Колонки плоского CSV: (заголовок, ключ). operators разворачивается."""
    out: list[tuple[str, str]] = []
    for col in lst.get("columns", []):
        key = col.get("key") or ""
        if key == "operators":
            out += [(k, f"op:{k}") for k in _OP_KEYS]
        elif key in _DEFAULT_KEYS:
            out.append((key, key))
        else:
            out.append((col.get("title") or key, key))
    return out


def _list_to_csv(lst: dict) -> str:
    header = _flat_header(lst)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([h for h, _ in header])
    for row in lst.get("rows", []):
        values, ops = row.get("values") or {}, row.get("operators") or {}
        line = []
        for _, key in header:
            if key.startswith("op:"):
                line.append("1" if ops.get(key[3:], False) else "0")
            else:
                line.append(str(values.get(key) or ""))
        w.writerow(line)
    return buf.getvalue()


def _list_to_txt(lst: dict) -> str:
    out = [f"# {lst.get('name') or 'Список'}"]
    for row in lst.get("rows", []):
        subnet = (row.get("values") or {}).get("subnet") or ""
        if subnet:
            out.append(subnet)
    return "\n".join(out) + "\n"


def _prov_key(row: dict) -> tuple:
    """Ключ сортировки/группировки по провайдеру: без провайдера — в конец."""
    prov = ((row.get("values") or {}).get("provider") or "").strip()
    return (1 if not prov else 0, prov)


def _list_to_xlsx(lst: dict) -> bytes:
    """Список → .xlsx (bytes). Колонки как в CSV (operators → 5 колонок,
    значения 1/0); строки отсортированы по провайдеру (пустые — в конец,
    группа «—») и сгруппированы: ws.row_dimensions.group(start, end,
    outline_level=1) — сворачивание «+/-» в Excel, hidden=False.
    Заголовок жирный, первая строка закреплена (freeze_panes="A2"),
    автоширина колонок (cap 40)."""
    header = _flat_header(lst)
    op_label = {o["key"]: o["label"] for o in store.OPERATORS}
    titles = [op_label.get(k[3:], h) if k.startswith("op:") else h
              for h, k in header]

    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    name = "".join(c if c not in "[]:*?/\\" else " " for c in (lst.get("name") or ""))
    ws.title = (name.strip() or "Таблица")[:31]
    ws.append(titles)

    data = sorted(lst.get("rows") or [], key=_prov_key)
    run_start = 0
    for i, row in enumerate(data):
        # смена провайдера → закрыть предыдущую группу (строки run_start..i)
        if i > 0 and _prov_key(row) != _prov_key(data[i - 1]):
            ws.row_dimensions.group(run_start + 2, i + 1, outline_level=1)
            run_start = i
        values, ops = row.get("values") or {}, row.get("operators") or {}
        line = []
        for _, key in header:
            if key.startswith("op:"):
                line.append(1 if ops.get(key[3:], False) else 0)
            else:
                line.append(values.get(key) or "")
        ws.append(line)
    if data:
        ws.row_dimensions.group(run_start + 2, len(data) + 1, outline_level=1)

    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"
    for col in ws.columns:
        letter = col[0].column_letter
        width = max(len(str(c.value or "")) for c in col)
        ws.column_dimensions[letter].width = min(max(width + 2, 10), 40)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@router.get("/export")
async def export_subnets(provider_id: str = "", list_id: str = "", format: str = "json"):
    fmt = (format or "json").strip().lower()
    if fmt not in _MEDIA:
        raise HTTPException(400, "Формат должен быть json, csv, txt или xlsx")
    if fmt in ("csv", "txt", "xlsx") and not list_id:
        raise HTTPException(400, "Для формата csv/txt/xlsx укажите список (list_id)")

    if list_id:
        provider, lst = _pick_list(provider_id, list_id)
    else:
        provider, lst = None, None

    if fmt == "json":
        if lst is not None and provider is not None:
            payload = {"_format": JSON_FORMAT, "_version": JSON_VERSION,
                       "providers": [{"id": provider["id"], "name": provider["name"],
                                      "lists": [lst]}]}
        else:
            data = store.get_store()
            providers = data["providers"]
            if provider_id:
                providers = [p for p in providers if p.get("id") == provider_id]
                if not providers:
                    raise HTTPException(404, "Провайдер не найден")
            payload = {"_format": JSON_FORMAT, "_version": JSON_VERSION,
                       "providers": providers}
        body = json.dumps(payload, ensure_ascii=False, indent=1)
    elif fmt == "csv":
        body = _list_to_csv(lst or {})
    elif fmt == "txt":
        body = _list_to_txt(lst or {})
    else:
        body = _list_to_xlsx(lst or {})

    name = f"subnets_{time.strftime('%Y%m%d-%H%M%S')}.{fmt}"
    payload = body if isinstance(body, bytes) else body.encode("utf-8")
    return Response(payload, media_type=_MEDIA[fmt],
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _detect_format(filename: str, text: str) -> str:
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    if ext in ("json", "csv", "txt"):
        return ext
    head = text.lstrip()[:1]
    if head in ("{", "["):
        return "json"
    first = next((l for l in text.splitlines() if l.strip()), "")
    return "csv" if ("," in first or ";" in first) else "txt"


def _parse_txt(text: str) -> list[dict]:
    items = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("//"):
            continue
        for sep in ("#", "—", " - ", "\t", ";", ","):
            if sep in s:
                s = s.split(sep, 1)[0].strip()
        if s:
            items.append({"subnet": s})
    return items


_TRUE = {"1", "true", "yes", "y", "да", "+", "on", "истина"}


def _parse_csv(text: str) -> list[dict]:
    sample = next((l for l in text.splitlines() if l.strip()), "")
    delim = ";" if sample.count(";") > sample.count(",") else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    rows = [r for r in rows if any((c or "").strip() for c in r)]
    if not rows:
        return []
    head = [(c or "").strip() for c in rows[0]]
    lowered = [h.lower() for h in head]
    has_header = any(_CSV_ALIASES.get(h, h) == "subnet" for h in lowered)
    if not has_header:
        return [{"subnet": (r[0] or "").strip()} for r in rows if (r[0] or "").strip()]

    items = []
    for r in rows[1:]:
        cells = [(c or "").strip() for c in r]
        item: dict = {"subnet": "", "values": {}, "extra": {}, "operators": {}}
        for i, title in enumerate(head):
            val = cells[i] if i < len(cells) else ""
            key = _CSV_ALIASES.get(lowered[i], lowered[i])
            if key == "subnet":
                item["subnet"] = val
            elif key in _OP_KEYS:
                item["operators"][key] = val.strip().lower() in _TRUE
            elif key in _DEFAULT_KEYS:
                item["values"][key] = val
            elif title:
                item["extra"][title] = val
        if item["subnet"]:
            items.append(item)
    return items


def _snapshot_providers(payload) -> list:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        raise HTTPException(400, "Некорректный JSON: ожидался объект снимка")
    providers = payload.get("providers")
    if isinstance(providers, list):
        return providers
    if isinstance(payload.get("lists"), list):  # снимок одного провайдера
        return [payload]
    if isinstance(payload.get("rows"), list):  # снимок одного списка
        return [{"name": "Импортированные", "lists": [payload]}]
    raise HTTPException(400, "Некорректный JSON: нет providers/lists/rows")


def _snapshot_items(providers: list) -> list[dict]:
    """Все строки снимка плоским списком (импорт в конкретный список)."""
    items: list[dict] = []
    for p in providers:
        if not isinstance(p, dict):
            continue
        for l in p.get("lists") or []:
            if isinstance(l, dict):
                items += store.rows_to_items(l.get("rows"))
    return items


@router.post("/import")
async def import_subnets(file: UploadFile = File(...), provider_id: str = Form(""),
                         list_id: str = Form(""), mode: str = Form("merge"),
                         format: str = Form("")):
    mode = (mode or "merge").strip().lower()
    if mode not in ("merge", "replace"):
        raise HTTPException(400, "Режим должен быть merge или replace")
    blob = await file.read()
    if not blob:
        raise HTTPException(400, "Файл пуст")
    try:
        text = blob.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(400, "Файл должен быть в кодировке UTF-8")

    fmt = (format or "").strip().lower() or _detect_format(file.filename or "", text)
    if fmt not in ("json", "csv", "txt"):
        raise HTTPException(400, "Поддерживаются только json, csv и txt")

    if fmt == "json":
        try:
            payload = json.loads(text)
        except ValueError:
            raise HTTPException(400, "Некорректный JSON-файл")
        providers = _snapshot_providers(payload)
        rows_total = sum(len(l.get("rows") or [])
                         for p in providers if isinstance(p, dict)
                         for l in (p.get("lists") or []) if isinstance(l, dict))
        if rows_total > MAX_IMPORT_ROWS:
            raise HTTPException(400, f"За раз не больше {MAX_IMPORT_ROWS} строк "
                                     f"(в файле {rows_total})")
        if not list_id:
            res = store.import_tree(providers, replace=(mode == "replace"))
            return {"ok": True, **res}
        items = _snapshot_items(providers)
    else:
        items = _parse_csv(text) if fmt == "csv" else _parse_txt(text)
        if len(items) > MAX_IMPORT_ROWS:
            raise HTTPException(400, f"За раз не больше {MAX_IMPORT_ROWS} строк "
                                     f"(в файле {len(items)})")

    if not items:
        raise HTTPException(400, "В файле не найдено подсетей")

    if list_id:
        provider, lst = _pick_list(provider_id, list_id)
        pid, lid = provider["id"], lst["id"]
    else:
        provider = (store.ensure_provider("Импортированные") if not provider_id
                    else next((p for p in store.get_store()["providers"]
                               if p.get("id") == provider_id), None))
        if not provider:
            raise HTTPException(404, "Провайдер не найден")
        lst = store.ensure_list(provider["id"],
                                f"Импорт {time.strftime('%Y-%m-%d %H:%M:%S')}")
        pid, lid = provider["id"], lst["id"]

    try:
        res = store.import_rows(pid, lid, items, replace=(mode == "replace"))
    except KeyError:
        raise HTTPException(404, "Список не найден")
    return {"ok": True, **res}
