"""«Подсети» (Обходы БС, Wave-5 PR-5): справочник подсетей/IP по провайдерам.

Модель как в Библиотеке, но предметная — разметка подсетей:
  Провайдер → Списки → строки таблицы.

Хранилище `accounts/<id>/subnets.json`:
  providers: [{id, name, lists: [{id, name, columns: [{key, title}],
    rows: [{id, values: {<colKey>: str}, operators: {<opKey>: bool}}]}]}]

Дефолтные столбцы: subnet (подсеть), ipver (версия IP), asn, asnname
(название ASN), date, operators (иконки операторов). Пользовательские столбцы
добавляются в режиме редактирования таблицы.
"""
from __future__ import annotations

import ipaddress
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from app.services import accounts

_LOCK = threading.Lock()
MAX_LISTS = 200
MAX_ROWS = 2000

DEFAULT_COLUMNS = [
    {"key": "subnet",    "title": "Подсеть"},
    {"key": "ipver",     "title": "Версия IP"},
    {"key": "asn",       "title": "ASN"},
    {"key": "asnname",   "title": "Название ASN"},
    {"key": "date",      "title": "Дата"},
    {"key": "operators", "title": "Операторы"},
]

OPERATORS = [
    {"key": "mts",     "label": "MTS"},
    {"key": "beeline", "label": "Beeline"},
    {"key": "megafon", "label": "МегаФон"},
    {"key": "tele2",   "label": "Tele2"},
    {"key": "tmobile", "label": "T-Mobile"},
]

_ASN_NUM_RE = re.compile(r"AS(\d+)")


def _path(account_id: Optional[str]) -> Path:
    aid = account_id or accounts.current_account.get()
    if not aid:
        raise RuntimeError("No active account in context")
    return accounts.data_dir(aid) / "subnets.json"


def _load(account_id: Optional[str]) -> dict:
    p = _path(account_id)
    try:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("providers"), list):
                return data
    except Exception:
        pass
    return {"providers": []}


def _save(data: dict, account_id: Optional[str]) -> None:
    p = _path(account_id)
    with _LOCK:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _find_provider(data: dict, provider_id: str) -> Optional[dict]:
    return next((p for p in data["providers"] if p.get("id") == provider_id), None)


def _find_list(data: dict, provider_id: str, list_id: str) -> Optional[dict]:
    p = _find_provider(data, provider_id)
    if not p:
        return None
    return next((l for l in p.get("lists", []) if l.get("id") == list_id), None)


# ── провайдеры ─────────────────────────────────────────────────
def add_provider(name: str, account_id: Optional[str] = None) -> dict:
    data = _load(account_id)
    if len(data["providers"]) >= MAX_LISTS:
        raise ValueError(f"Не больше {MAX_LISTS} провайдеров")
    p = {"id": uuid.uuid4().hex[:10], "name": name.strip() or "Провайдер", "lists": []}
    data["providers"].append(p)
    _save(data, account_id)
    return p


def rename_provider(provider_id: str, name: str, account_id: Optional[str] = None) -> None:
    data = _load(account_id)
    p = _find_provider(data, provider_id)
    if not p:
        raise KeyError(provider_id)
    p["name"] = name.strip() or p["name"]
    _save(data, account_id)


def delete_provider(provider_id: str, account_id: Optional[str] = None) -> None:
    data = _load(account_id)
    data["providers"] = [p for p in data["providers"] if p.get("id") != provider_id]
    _save(data, account_id)


# ── списки ─────────────────────────────────────────────────────
def add_list(provider_id: str, name: str, account_id: Optional[str] = None) -> dict:
    data = _load(account_id)
    p = _find_provider(data, provider_id)
    if not p:
        raise KeyError(provider_id)
    if sum(len(x.get("lists", [])) for x in data["providers"]) >= MAX_LISTS:
        raise ValueError(f"Не больше {MAX_LISTS} списков")
    lst = {"id": uuid.uuid4().hex[:10], "name": name.strip() or "Список",
           "columns": [dict(c) for c in DEFAULT_COLUMNS], "rows": []}
    p.setdefault("lists", []).append(lst)
    _save(data, account_id)
    return lst


def rename_list(provider_id: str, list_id: str, name: str, account_id: Optional[str] = None) -> None:
    data = _load(account_id)
    lst = _find_list(data, provider_id, list_id)
    if not lst:
        raise KeyError(list_id)
    lst["name"] = name.strip() or lst["name"]
    _save(data, account_id)


def delete_list(provider_id: str, list_id: str, account_id: Optional[str] = None) -> None:
    data = _load(account_id)
    p = _find_provider(data, provider_id)
    if not p:
        raise KeyError(provider_id)
    p["lists"] = [l for l in p.get("lists", []) if l.get("id") != list_id]
    _save(data, account_id)


# ── столбцы (режим редактирования таблицы) ─────────────────────
def add_column(provider_id: str, list_id: str, title: str, account_id: Optional[str] = None) -> dict:
    data = _load(account_id)
    lst = _find_list(data, provider_id, list_id)
    if not lst:
        raise KeyError(list_id)
    col = {"key": f"col_{uuid.uuid4().hex[:8]}", "title": title.strip() or "Столбец"}
    lst["columns"].append(col)
    _save(data, account_id)
    return col


def rename_column(provider_id: str, list_id: str, col_key: str, title: str,
                  account_id: Optional[str] = None) -> None:
    data = _load(account_id)
    lst = _find_list(data, provider_id, list_id)
    if not lst:
        raise KeyError(list_id)
    col = next((c for c in lst["columns"] if c["key"] == col_key), None)
    if not col:
        raise KeyError(col_key)
    col["title"] = title.strip() or col["title"]
    _save(data, account_id)


def delete_column(provider_id: str, list_id: str, col_key: str,
                  account_id: Optional[str] = None) -> None:
    if col_key == "subnet":
        raise ValueError("Столбец «Подсеть» удалить нельзя")
    data = _load(account_id)
    lst = _find_list(data, provider_id, list_id)
    if not lst:
        raise KeyError(list_id)
    lst["columns"] = [c for c in lst["columns"] if c["key"] != col_key]
    for row in lst.get("rows", []):
        row.get("values", {}).pop(col_key, None)
    _save(data, account_id)


def reorder_columns(provider_id: str, list_id: str, order: list[str],
                    account_id: Optional[str] = None) -> None:
    data = _load(account_id)
    lst = _find_list(data, provider_id, list_id)
    if not lst:
        raise KeyError(list_id)
    by_key = {c["key"]: c for c in lst["columns"]}
    new_cols = [by_key[k] for k in order if k in by_key]
    new_cols += [c for c in lst["columns"] if c["key"] not in order]
    lst["columns"] = new_cols
    _save(data, account_id)


# ── строки ─────────────────────────────────────────────────────
def parse_subnet(raw: str) -> tuple[str, str]:
    """CIDR/IP → (нормализованная подсеть, версия IP). ValueError на мусоре."""
    s = (raw or "").strip()
    try:
        if "/" in s:
            net = ipaddress.ip_network(s, strict=False)
            return str(net), "IPv6" if net.version == 6 else "IPv4"
        ip = ipaddress.ip_address(s)
        suf = "/128" if ip.version == 6 else "/32"
        return str(ip) + suf, "IPv6" if ip.version == 6 else "IPv4"
    except ValueError:
        raise ValueError(f"Некорректная подсеть/IP: {s}") from None


def add_rows(provider_id: str, list_id: str, subnets: list[str],
             account_id: Optional[str] = None) -> dict:
    """Добавить строки по подсетям (пакетно). Обогащение ASN — отдельно
    (enrich_rows); здесь только нормализация подсети и версии IP."""
    data = _load(account_id)
    lst = _find_list(data, provider_id, list_id)
    if not lst:
        raise KeyError(list_id)
    rows = lst.setdefault("rows", [])
    added, errors = [], []
    for raw in subnets:
        try:
            subnet, ipver = parse_subnet(raw)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if len(rows) >= MAX_ROWS:
            errors.append(f"Лимит {MAX_ROWS} строк")
            break
        rows.append({
            "id": uuid.uuid4().hex[:10],
            "values": {"subnet": subnet, "ipver": ipver,
                       "date": time.strftime("%Y-%m-%d")},
            "operators": {o["key"]: True for o in OPERATORS},
        })
        added.append(subnet)
    _save(data, account_id)
    return {"added": added, "errors": errors}


def delete_row(provider_id: str, list_id: str, row_id: str,
               account_id: Optional[str] = None) -> None:
    data = _load(account_id)
    lst = _find_list(data, provider_id, list_id)
    if not lst:
        raise KeyError(list_id)
    lst["rows"] = [r for r in lst.get("rows", []) if r.get("id") != row_id]
    _save(data, account_id)


def set_cell(provider_id: str, list_id: str, row_id: str, col_key: str, value: str,
             account_id: Optional[str] = None) -> None:
    data = _load(account_id)
    lst = _find_list(data, provider_id, list_id)
    if not lst:
        raise KeyError(list_id)
    row = next((r for r in lst.get("rows", []) if r.get("id") == row_id), None)
    if not row:
        raise KeyError(row_id)
    row.setdefault("values", {})[col_key] = value
    _save(data, account_id)


def toggle_operator(provider_id: str, list_id: str, row_id: str, op_key: str, on: bool,
                    account_id: Optional[str] = None) -> None:
    if op_key not in {o["key"] for o in OPERATORS}:
        raise ValueError(f"Неизвестный оператор: {op_key}")
    data = _load(account_id)
    lst = _find_list(data, provider_id, list_id)
    if not lst:
        raise KeyError(list_id)
    row = next((r for r in lst.get("rows", []) if r.get("id") == row_id), None)
    if not row:
        raise KeyError(row_id)
    row.setdefault("operators", {})[op_key] = bool(on)
    _save(data, account_id)


def update_row_asn(provider_id: str, list_id: str, row_id: str, asn: str, asnname: str,
                   account_id: Optional[str] = None) -> None:
    """Записать обогащение ASN (вызывается после внешнего lookup'а)."""
    data = _load(account_id)
    lst = _find_list(data, provider_id, list_id)
    if not lst:
        return
    row = next((r for r in lst.get("rows", []) if r.get("id") == row_id), None)
    if not row:
        return
    row.setdefault("values", {})["asn"] = asn
    row["values"]["asnname"] = asnname
    _save(data, account_id)


def get_store(account_id: Optional[str] = None) -> dict:
    return _load(account_id)
