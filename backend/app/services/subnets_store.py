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
    {"key": "asnname",   "title": "Организация"},
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

# Имена операторов как они встречаются в строках (TSV LatencyLab и др.)
# → ключи OPERATORS. Проверяется подстрокой (регистр не важен).
_OPERATOR_ALIASES = {
    "т-мобайл": "tmobile", "t-mobile": "tmobile", "tmobile": "tmobile",
    "мегафон": "megafon", "megafon": "megafon",
    "билайн": "beeline", "beeline": "beeline",
    "мтс": "mts", "mts": "mts",
    "tele2": "tele2", "т2": "tele2", "t2": "tele2",
}

_ASN_NUM_RE = re.compile(r"AS(\d+)")

# ── тип ASN (эвристика, ip-info/ip-api тип без ключа не отдают) ─────
# Проверяется подстрокой по org/asnname/netname/provider (регистр и дефисы
# не важны: «data-center» == «data center» == «datacenter»).
_ASN_HOSTING_WORDS = (
    "hosting", "cloud", "data center", "datacenter", "server", "vps", "vds",
    "dedicated", "ihc", "selectel", "timeweb", "reg.ru", "ruvds", "firstvds",
    "aeza", "hetzner", "digitalocean", "aws", "azure", "gcore", "ddos-guard",
    "spaceweb", "beget", "sprinthost", "justhost", "cloudflare", "serverius",
    "хостинг", "облако", "дата-центр",
)
_ASN_ISP_WORDS = (
    "telecom", "telekom", "связь", "интернет", "сети", "net", "isp",
    "operator", "мтс", "билайн", "мегафон", "ростелеком", "beeline",
    "megafon", "mts", "телеком", "коммуникац", "wireline", "broadband",
    "provider", "wimax", "lte", "транстелеком",
)


def _asn_type(org: str, asnname: str = "", netname: str = "",
              provider: str = "") -> str:
    """Тип ASN по имени организации/ASN/сети/провайдеру — эвристика.

    Порядок: hosting → isp → business. Пусто только если данных нет вовсе
    (все четыре поля пустые). Регистр и дефисы не важны.
    """
    parts = " ".join(x for x in (org, asnname, netname, provider) if x)
    if not parts.strip():
        return ""
    raw = parts.lower()
    clean = raw.replace("-", " ")
    for words, kind in ((_ASN_HOSTING_WORDS, "hosting"),
                        (_ASN_ISP_WORDS, "isp")):
        for w in words:
            wl = w.lower()
            if wl in raw or wl.replace("-", " ") in clean:
                return kind
    return "business"


# ── иконки провайдеров/списков (файлы в DATA_DIR, раздача через API) ──
ICON_EXTS = ("png", "svg", "webp")
ICON_MAX_BYTES = 256 * 1024


def _icons_dir(account_id: Optional[str]) -> Path:
    aid = account_id or accounts.current_account.get()
    if not aid:
        raise RuntimeError("No active account in context")
    d = accounts.data_dir(aid) / "subnets_icons"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _validate_icon(blob: bytes, filename: str) -> str:
    """Расширение иконки (png/svg/webp) и размер ≤ 256 КБ. → ext."""
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    if ext not in ICON_EXTS:
        raise ValueError("Иконка должна быть PNG, SVG или WebP")
    if not blob:
        raise ValueError("Файл пуст")
    if len(blob) > ICON_MAX_BYTES:
        raise ValueError(f"Иконка не больше {ICON_MAX_BYTES // 1024} КБ")
    return ext


def _save_icon(kind_name: str, blob: bytes, filename: str,
               account_id: Optional[str]) -> str:
    """Сохранить файл иконки → имя файла (kind.ext). Старая иконка того же
    владельца с другим расширением удаляется."""
    ext = _validate_icon(blob, filename)
    name = f"{kind_name}.{ext}"
    d = _icons_dir(account_id)
    for old in d.glob(f"{kind_name}.*"):
        if old.name != name:
            try:
                old.unlink()
            except OSError:
                pass
    (d / name).write_bytes(blob)
    return name


def save_provider_icon(provider_id: str, blob: bytes, filename: str,
                       account_id: Optional[str] = None) -> str:
    """Загрузить иконку провайдера: файл в DATA_DIR + provider['icon']."""
    data = _load(account_id)
    p = _find_provider(data, provider_id)
    if not p:
        raise KeyError(provider_id)
    name = _save_icon(provider_id, blob, filename, account_id)
    p["icon"] = name
    _save(data, account_id)
    return name


def save_list_icon(provider_id: str, list_id: str, blob: bytes, filename: str,
                   account_id: Optional[str] = None) -> str:
    """Загрузить иконку списка: файл в DATA_DIR + list['icon']."""
    data = _load(account_id)
    lst = _find_list(data, provider_id, list_id)
    if not lst:
        raise KeyError(list_id)
    name = _save_icon(f"list_{list_id}", blob, filename, account_id)
    lst["icon"] = name
    _save(data, account_id)
    return name


def icon_file(name: str, account_id: Optional[str] = None) -> Optional[Path]:
    """Путь к файлу иконки по имени из provider/list['icon'] (или None)."""
    if not (name or "").strip():
        return None
    p = _icons_dir(account_id) / name
    return p if p.exists() else None


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
    p = {"id": uuid.uuid4().hex[:10], "name": name.strip() or "Провайдер",
         "icon": "", "lists": []}
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
           "icon": "",
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
def add_column(provider_id: str, list_id: str, title: str, account_id: Optional[str] = None, key: str = "") -> dict:
    data = _load(account_id)
    lst = _find_list(data, provider_id, list_id)
    if not lst:
        raise KeyError(list_id)
    k = key.strip() or f"col_{uuid.uuid4().hex[:8]}"
    col = {"key": k, "title": title.strip() or "Столбец"}
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


def _row_from_dict(raw: dict, op_keys: set) -> tuple[Optional[dict], Optional[str]]:
    """Плоский dict строки → (строка хранилища, ошибка|None).

    `subnet` обязателен и валидируется. Известные операторы (OPERATORS)
    ложатся в `operators`; неизвестный оператор и прочие метаданные —
    в `values` как есть (не падает, колонки не создаются).
    """
    subnet_raw = raw.get("subnet")
    if subnet_raw is None or str(subnet_raw).strip() == "":
        return None, "Нет поля subnet"
    try:
        subnet, ipver = parse_subnet(str(subnet_raw))
    except ValueError as exc:
        return None, str(exc)
    values = {"subnet": subnet, "ipver": ipver,
              "date": time.strftime("%Y-%m-%d")}
    operators = {k: False for k in op_keys}
    for k, v in raw.items():
        if k in ("subnet", "id", "values") or v is None or v == "":
            continue
        if k == "operator":
            # Строка вида «Т-Мобайл + МегаФон + Билайн + Т2» (или англ.)
            # → флаги operators. Неизвестные имена — в values как есть.
            found = False
            op_str = str(v).lower()
            for name, okey in _OPERATOR_ALIASES.items():
                if name in op_str:
                    operators[okey] = True
                    found = True
            if not found:
                values[k] = str(v)
        elif k == "operators" and isinstance(v, dict):
            for ok, ov in v.items():
                if ok in op_keys:
                    operators[ok] = bool(ov)
                else:
                    values[ok] = str(ov)
        elif k in op_keys:
            operators[k] = bool(v)
        else:
            values[k] = str(v)
    return {"id": uuid.uuid4().hex[:10], "values": values,
            "operators": operators}, None


def add_rows(provider_id: str, list_id: str, items: list,
             account_id: Optional[str] = None) -> dict:
    """Добавить строки (пакетно). Элемент — подсеть (str) или dict
    {subnet, ...метаданные}: метаданные ложатся в values строки, известные
    операторы — в operators. Обогащение ASN — отдельно (enrich_rows)."""
    data = _load(account_id)
    lst = _find_list(data, provider_id, list_id)
    if not lst:
        raise KeyError(list_id)
    rows = lst.setdefault("rows", [])
    op_keys = {o["key"] for o in OPERATORS}
    added, errors = [], []
    for raw in items:
        if isinstance(raw, dict):
            row, err = _row_from_dict(raw, op_keys)
            if err:
                errors.append(err)
                continue
            assert row is not None
        else:
            try:
                subnet, ipver = parse_subnet(str(raw))
            except ValueError as exc:
                errors.append(str(exc))
                continue
            row = {
                "id": uuid.uuid4().hex[:10],
                "values": {"subnet": subnet, "ipver": ipver,
                           "date": time.strftime("%Y-%m-%d")},
                "operators": {k: True for k in op_keys},
            }
        if len(rows) >= MAX_ROWS:
            errors.append(f"Лимит {MAX_ROWS} строк")
            break
        rows.append(row)
        added.append(row["values"]["subnet"])
    _save(data, account_id)
    return {"added": added, "errors": errors}


def import_flat_rows(provider_id: str, list_id: str, items: list[dict],
                     account_id: Optional[str] = None) -> dict:
    """Импорт плоских dict-строк {subnet, ...метаданные}.

    Дубликаты подсетей — skip. Колонки НЕ создаются: метаданные остаются
    в values строки (колонки заводит агент через add_column)."""
    data = _load(account_id)
    lst = _find_list(data, provider_id, list_id)
    if not lst:
        raise KeyError(list_id)
    rows = lst.setdefault("rows", [])
    seen = {(r.get("values") or {}).get("subnet") for r in rows}
    op_keys = {o["key"] for o in OPERATORS}
    added, skipped, errors = 0, 0, []
    for raw in items:
        row, err = _row_from_dict(raw, op_keys)
        if err:
            skipped += 1
            errors.append(err)
            continue
        assert row is not None
        subnet = row["values"]["subnet"]
        if subnet in seen:
            skipped += 1
            continue
        if len(rows) >= MAX_ROWS:
            errors.append(f"Лимит {MAX_ROWS} строк в списке")
            skipped += 1
            break
        rows.append(row)
        seen.add(subnet)
        added += 1
    _save(data, account_id)
    return {"added": added, "skipped": skipped, "errors": errors}


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
                   provider: str = "", country: str = "",
                   account_id: Optional[str] = None) -> None:
    """Записать обогащение строки (вызывается после внешнего lookup'а ip-api).

    Заполняет asn/asnname/provider/country. Уже заполненные поля НЕ
    перезаписываются — дозаполняются только пустые."""
    data = _load(account_id)
    lst = _find_list(data, provider_id, list_id)
    if not lst:
        return
    row = next((r for r in lst.get("rows", []) if r.get("id") == row_id), None)
    if not row:
        return
    values = row.setdefault("values", {})
    for key, val in (("asn", asn), ("asnname", asnname),
                     ("provider", provider), ("country", country)):
        if val and not str(values.get(key) or "").strip():
            values[key] = val
    _save(data, account_id)


def get_store(account_id: Optional[str] = None) -> dict:
    return _load(account_id)


def apply_asn_name(asn: str, name: str, account_id: Optional[str] = None) -> int:
    """Справочник ASN → подсети: во ВСЕХ списках всех провайдеров у строк с
    values.asn == asn (нормализованный «AS12345») перезаписать
    values.asnname = name. Справочник авторитетнее ручной правки ячейки.
    Возвращает число обновлённых строк (0, если name пуст)."""
    if not (name or "").strip():
        return 0
    data = _load(account_id)
    updated = 0
    for p in data["providers"]:
        for l in p.get("lists", []):
            for row in l.get("rows", []):
                values = row.get("values") or {}
                if str(values.get("asn") or "").strip() == asn:
                    values["asnname"] = name.strip()
                    updated += 1
    if updated:
        _save(data, account_id)
    return updated


def apply_asn_meta(asn: str, name: Optional[str] = None,
                   netname: Optional[str] = None,
                   country: Optional[str] = None,
                   asn_type: Optional[str] = None,
                   account_id: Optional[str] = None) -> int:
    """Справочник ASN → подсети: во ВСЕХ списках всех провайдеров у строк с
    values.asn == asn (нормализованный «AS12345») перезаписать
    values.asnname = name, values.netname = netname, values.country = country
    и values.asn_type = asn_type. Справочник авторитетнее ручной правки
    ячейки. Непустые значения перезаписывают всегда; пустые/None — поле не
    трогается. Возвращает число обновлённых строк (0, если все значения
    пусты)."""
    name = (name or "").strip()
    netname = (netname or "").strip()
    country = (country or "").strip()
    asn_type = (asn_type or "").strip()
    if not (name or netname or country or asn_type):
        return 0
    data = _load(account_id)
    updated = 0
    for p in data["providers"]:
        for l in p.get("lists", []):
            for row in l.get("rows", []):
                values = row.get("values") or {}
                if str(values.get("asn") or "").strip() == asn:
                    if name:
                        values["asnname"] = name
                    if netname:
                        values["netname"] = netname
                    if country:
                        values["country"] = country
                    if asn_type:
                        values["asn_type"] = asn_type
                    updated += 1
    if updated:
        _save(data, account_id)
    return updated


# ── импорт (см. api/subnets.py: /import) ───────────────────────
def set_store(data: dict, account_id: Optional[str] = None) -> None:
    """Заменить дерево целиком (режим replace при импорте)."""
    _save({"providers": list(data.get("providers") or [])}, account_id)


def ensure_provider(name: str, account_id: Optional[str] = None) -> dict:
    """Провайдер по имени: существующий или новый."""
    wanted = (name or "").strip() or "Импортированные"
    data = _load(account_id)
    p = next((x for x in data["providers"]
              if (x.get("name") or "").strip().lower() == wanted.lower()), None)
    return p if p else add_provider(wanted, account_id)


def ensure_list(provider_id: str, name: str, columns: Optional[list] = None,
                account_id: Optional[str] = None) -> dict:
    """Список по имени внутри провайдера: существующий или новый."""
    wanted = (name or "").strip() or "Импорт"
    data = _load(account_id)
    p = _find_provider(data, provider_id)
    if not p:
        raise KeyError(provider_id)
    lst = next((l for l in p.get("lists", [])
                if (l.get("name") or "").strip().lower() == wanted.lower()), None)
    if lst:
        return lst
    lst = add_list(provider_id, wanted, account_id)
    if columns:
        keys = {c.get("key") for c in columns if isinstance(c, dict)}
        if "subnet" in keys:
            data = _load(account_id)
            fresh = _find_list(data, provider_id, lst["id"])
            if fresh:
                fresh["columns"] = [{"key": c["key"], "title": c.get("title") or c["key"]}
                                    for c in columns
                                    if isinstance(c, dict) and c.get("key")]
                _save(data, account_id)
                return fresh
    return lst


def _column_key(lst: dict, title: str) -> str:
    """Ключ столбца по заголовку: существующий или новый (создаётся)."""
    t = (title or "").strip()
    col = next((c for c in lst["columns"]
                if (c.get("title") or "").strip().lower() == t.lower()
                or c.get("key") == t), None)
    if col:
        return col["key"]
    col = {"key": f"col_{uuid.uuid4().hex[:8]}", "title": t or "Столбец"}
    lst["columns"].append(col)
    return col["key"]


def import_rows(provider_id: str, list_id: str, items: list[dict],
                replace: bool = False, account_id: Optional[str] = None) -> dict:
    """Импорт строк в список.

    item = {subnet: str, values: {colKey: str}, extra: {заголовок: str},
            operators: {opKey: bool}}. Дубликаты подсетей — skip.
    """
    data = _load(account_id)
    lst = _find_list(data, provider_id, list_id)
    if not lst:
        raise KeyError(list_id)
    if replace:
        lst["rows"] = []
    rows = lst.setdefault("rows", [])
    seen = {(r.get("values") or {}).get("subnet") for r in rows}
    op_keys = {o["key"] for o in OPERATORS}
    imported, skipped, errors = 0, 0, []
    for item in items:
        try:
            subnet, ipver = parse_subnet(str(item.get("subnet") or ""))
        except ValueError as exc:
            errors.append(str(exc))
            skipped += 1
            continue
        if subnet in seen:
            skipped += 1
            continue
        if len(rows) >= MAX_ROWS:
            errors.append(f"Лимит {MAX_ROWS} строк в списке")
            skipped += 1
            break
        values = {"subnet": subnet, "ipver": ipver, "date": time.strftime("%Y-%m-%d")}
        for k, v in (item.get("values") or {}).items():
            if k in ("subnet", "ipver") or v in (None, ""):
                continue
            values[k] = str(v)
        for title, v in (item.get("extra") or {}).items():
            if v in (None, ""):
                continue
            values[_column_key(lst, title)] = str(v)
        operators = {k: True for k in op_keys}
        for k, v in (item.get("operators") or {}).items():
            if k in op_keys:
                operators[k] = bool(v)
        rows.append({"id": uuid.uuid4().hex[:10], "values": values,
                     "operators": operators})
        seen.add(subnet)
        imported += 1
    _save(data, account_id)
    return {"imported": imported, "skipped": skipped, "errors": errors}


def rows_to_items(rows: Optional[list]) -> list[dict]:
    """Строки снимка → items для import_rows."""
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        values = r.get("values") or {}
        out.append({"subnet": values.get("subnet") or "", "values": values,
                    "operators": r.get("operators") or {}})
    return out


def import_tree(providers: list, replace: bool = False,
                account_id: Optional[str] = None) -> dict:
    """Слить снимок дерева (provider→list→rows) в хранилище аккаунта."""
    if replace:
        set_store({"providers": []}, account_id)
    total = {"imported": 0, "skipped": 0, "errors": []}
    for sp in providers or []:
        if not isinstance(sp, dict):
            continue
        p = ensure_provider(sp.get("name") or "", account_id)
        for sl in sp.get("lists") or []:
            if not isinstance(sl, dict):
                continue
            lst = ensure_list(p["id"], sl.get("name") or "", sl.get("columns"),
                              account_id)
            res = import_rows(p["id"], lst["id"], rows_to_items(sl.get("rows")),
                              account_id=account_id)
            total["imported"] += res["imported"]
            total["skipped"] += res["skipped"]
            total["errors"] += res["errors"]
    return total
