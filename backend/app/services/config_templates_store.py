"""Per-account store for user config templates (Wave-5 Plan D).

Local source of truth at `accounts/<id>/config_templates.json` (the section works
without a configured Remnawave panel; Remnawave export/import is optional). Atomic
writes + a process lock, mirroring hostings_store. `view_position` supports reorder.
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
MAX_TEMPLATES = 200


def _path(account_id: Optional[str]) -> Path:
    aid = account_id or accounts.current_account.get()
    if not aid:
        raise RuntimeError("No active account in context")
    return accounts.data_dir(aid) / "config_templates.json"


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


def list_templates(account_id: Optional[str] = None) -> list[dict]:
    return sorted(_read(account_id), key=lambda t: t.get("view_position", 0))


def get_template(template_id: str, account_id: Optional[str] = None) -> Optional[dict]:
    return next((t for t in _read(account_id) if t.get("id") == template_id), None)


def add_template(body: dict, account_id: Optional[str] = None) -> dict:
    with _LOCK:
        items = _read(account_id)
        if len(items) >= MAX_TEMPLATES:
            raise ValueError(f"Достигнут лимит шаблонов ({MAX_TEMPLATES})")
        pos = max((t.get("view_position", 0) for t in items), default=0) + 1
        entry = {**body, "id": uuid.uuid4().hex[:12], "view_position": pos,
                 "created_at": int(time.time())}
        items.append(entry)
        _write(account_id, items)
    return entry


def update_template(template_id: str, body: dict, account_id: Optional[str] = None) -> Optional[dict]:
    with _LOCK:
        items = _read(account_id)
        idx = next((i for i, t in enumerate(items) if t.get("id") == template_id), None)
        if idx is None:
            return None
        prev = items[idx]
        items[idx] = {**body, "id": template_id,
                      "view_position": prev.get("view_position", 0),
                      "created_at": prev.get("created_at", int(time.time()))}
        _write(account_id, items)
        return items[idx]


def delete_template(template_id: str, account_id: Optional[str] = None) -> bool:
    with _LOCK:
        items = _read(account_id)
        kept = [t for t in items if t.get("id") != template_id]
        if len(kept) == len(items):
            return False
        _write(account_id, kept)
    return True


def reorder_templates(ordered_ids: list[str], account_id: Optional[str] = None) -> list[dict]:
    with _LOCK:
        items = _read(account_id)
        rank = {tid: i for i, tid in enumerate(ordered_ids)}
        for t in items:
            if t.get("id") in rank:
                t["view_position"] = rank[t["id"]]
        _write(account_id, items)
    return sorted(items, key=lambda t: t.get("view_position", 0))
