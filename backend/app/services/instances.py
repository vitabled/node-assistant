"""Persistent workspace instances scoped to one account/workspace."""
from __future__ import annotations

import json
import threading
import uuid

from app.services import accounts

DEFAULT_INSTANCE_ID = "default"
_lock = threading.Lock()


def _path(account_id: str):
    return accounts.account_dir(account_id) / "instances.json"


def _read(account_id: str) -> list[dict]:
    path = _path(account_id)
    try:
        rows = json.loads(path.read_text(encoding="utf-8")).get("instances", [])
        if isinstance(rows, list):
            return rows
    except Exception:
        pass
    return []


def _write(account_id: str, rows: list[dict]) -> None:
    _path(account_id).write_text(
        json.dumps({"instances": rows}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def ensure_default(account_id: str) -> dict:
    """Create metadata only; legacy files remain in the account root untouched."""
    with _lock:
        rows = _read(account_id)
        found = next((row for row in rows if row.get("id") == DEFAULT_INSTANCE_ID), None)
        if found:
            return found
        default = {"id": DEFAULT_INSTANCE_ID, "name": "Default", "account_id": account_id}
        _write(account_id, [default, *rows])
        return default


def list_instances(account_id: str) -> list[dict]:
    ensure_default(account_id)
    return _read(account_id)


def get(instance_id: str, account_id: str) -> dict | None:
    return next((row for row in list_instances(account_id) if row.get("id") == instance_id), None)


def create(name: str, account_id: str) -> dict:
    clean = name.strip()
    if not clean:
        raise ValueError("instance name is empty")
    with _lock:
        rows = _read(account_id)
        if not any(row.get("id") == DEFAULT_INSTANCE_ID for row in rows):
            rows.insert(0, {"id": DEFAULT_INSTANCE_ID, "name": "Default", "account_id": account_id})
        row = {"id": str(uuid.uuid4()), "name": clean, "account_id": account_id}
        rows.append(row)
        _write(account_id, rows)
    accounts.data_dir(account_id, row["id"])
    return row


def select(instance_id: str | None, account_id: str) -> dict:
    selected = instance_id or DEFAULT_INSTANCE_ID
    row = get(selected, account_id)
    if not row:
        raise KeyError(selected)
    accounts.current_instance.set(selected)
    return row