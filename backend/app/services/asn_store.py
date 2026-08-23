"""«Подсети» (Обходы БС): справочник ASN (per-account).

Хранилище `accounts/<id>/asns.json`:
  asns: [{asn: "AS12345", name: "Яндекс", note: "...", updated_at: "..."}]

Ключ ASN всегда нормализован — «AS» + цифры («12345» → «AS12345»). Справочник
авторитетнее ручных значений asnname в строках подсетей: при upsert API
синхронизирует asnname во всех списках (см. subnets_store.apply_asn_name);
при удалении строки подсетей НЕ трогаются.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Optional

from app.services import accounts

_LOCK = threading.Lock()
MAX_ASNS = 500

_ASN_RE = re.compile(r"(?:AS\s*)?(\d+)", re.IGNORECASE)


def normalize_asn(asn: str) -> str:
    """«12345»/«as12345»/«AS 12345» → «AS12345». ValueError на мусоре."""
    m = _ASN_RE.search((asn or "").strip())
    if not m:
        raise ValueError("ASN должен быть номером (например 12345 или AS12345)")
    return f"AS{m.group(1)}"


def _path(account_id: Optional[str]) -> Path:
    aid = account_id or accounts.current_account.get()
    if not aid:
        raise RuntimeError("No active account in context")
    return accounts.data_dir(aid) / "asns.json"


def _load(account_id: Optional[str]) -> dict:
    p = _path(account_id)
    try:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("asns"), list):
                return data
    except Exception:
        pass
    return {"asns": []}


def _save(data: dict, account_id: Optional[str]) -> None:
    p = _path(account_id)
    with _LOCK:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def list_asns(account_id: Optional[str] = None) -> list[dict]:
    return _load(account_id)["asns"]


def get_asn(asn: str, account_id: Optional[str] = None) -> Optional[dict]:
    key = normalize_asn(asn)
    return next((x for x in _load(account_id)["asns"] if x["asn"] == key), None)


def upsert_asn(asn: str, name: str = "", note: str = "",
               account_id: Optional[str] = None) -> dict:
    """Создать/обновить запись справочника по номеру (нормализация без «AS»).

    Пустое name при апдейте существующей записи НЕ затирает текущее название.
    Возвращает запись; синхронизацию asnname в строках подсетей делает API
    (subnets_store.apply_asn_name) — здесь только справочник.
    """
    key = normalize_asn(asn)
    data = _load(account_id)
    rec = next((x for x in data["asns"] if x["asn"] == key), None)
    if rec is None:
        if len(data["asns"]) >= MAX_ASNS:
            raise ValueError(f"Не больше {MAX_ASNS} записей в справочнике")
        rec = {"asn": key, "name": name.strip(),
               "note": (note or "").strip(),
               "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        data["asns"].append(rec)
    else:
        if name.strip():
            rec["name"] = name.strip()
        rec["note"] = (note or "").strip()
        rec["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _save(data, account_id)
    return rec


def delete_asn(asn: str, account_id: Optional[str] = None) -> None:
    """Удалить запись справочника. Строки подсетей НЕ трогаются (asnname
    остаётся последним известным названием)."""
    key = normalize_asn(asn)
    data = _load(account_id)
    data["asns"] = [x for x in data["asns"] if x["asn"] != key]
    _save(data, account_id)
