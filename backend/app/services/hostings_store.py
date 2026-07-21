"""Per-account catalogue of hosting providers (Wave-4 Plan A — «Хостинги»).

An independent reference catalogue (NOT the infra-billing subsystem): each hosting
card holds tariffs, specs, features, notes and one or more locations. Locations
carry lat/lng so the «Карта» section can plot them (geocoding — city → coords —
is done client-side; the store just persists whatever coords it's given).

Per-account isolation mirrors the other JSON stores: data lives at
`accounts/<id>/hostings.json`. Writes are atomic (temp file + os.replace) and the
read-modify-write is serialised under a process-wide lock so concurrent edits
can't lose each other.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from app.services import accounts

_LOCK = threading.Lock()
MAX_HOSTINGS = 500  # per-account ceiling (defensive)


def _path(account_id: Optional[str]) -> Path:
    aid = account_id or accounts.current_account.get()
    if not aid:
        raise RuntimeError("No active account in context")
    return accounts.data_dir(aid) / "hostings.json"


def _read(account_id: Optional[str]) -> list[dict]:
    p = _path(account_id)
    try:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


def _write(account_id: Optional[str], items: list[dict]) -> None:
    p = _path(account_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def list_hostings(account_id: Optional[str] = None) -> list[dict]:
    return _read(account_id)


def add_hosting(body: dict, account_id: Optional[str] = None) -> dict:
    entry = {**body, "id": uuid.uuid4().hex[:12], "created_at": int(time.time())}
    with _LOCK:
        items = _read(account_id)
        if len(items) >= MAX_HOSTINGS:
            raise ValueError(f"Достигнут лимит хостингов ({MAX_HOSTINGS})")
        items.append(entry)
        _write(account_id, items)
    return entry


def update_hosting(hosting_id: str, body: dict, account_id: Optional[str] = None) -> Optional[dict]:
    with _LOCK:
        items = _read(account_id)
        idx = next((i for i, h in enumerate(items) if h.get("id") == hosting_id), None)
        if idx is None:
            return None
        # keep id + created_at; replace the rest with the new body
        items[idx] = {**body, "id": hosting_id, "created_at": items[idx].get("created_at", int(time.time()))}
        _write(account_id, items)
        return items[idx]


def delete_hosting(hosting_id: str, account_id: Optional[str] = None) -> bool:
    with _LOCK:
        items = _read(account_id)
        kept = [h for h in items if h.get("id") != hosting_id]
        if len(kept) == len(items):
            return False
        _write(account_id, kept)
    return True
