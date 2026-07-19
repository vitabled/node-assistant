"""
Per-account store for panel sync groups (Ф5).

A Group bundles several deployed panels (referenced by their `panel_key` — the
stable id of a panel widget in the client's `panel_jobs`) with a priority and a
role. A `standby` panel is kept in sync by periodically restoring the freshest
backup of the nearest PRIMARY ranked above it (priority-based standby-sync; no
auto-failover — restore is destructive and deliberate).

Priority semantics: a HIGHER number means HIGHER priority. A standby's "nearest
higher primary" is the primary with the smallest priority that is still strictly
greater than the standby's own priority (the primary just above it).

Storage: `accounts/<id>/panel_groups.json` (mirrors the checker/testserver JSON
stores). No secrets live here — SSH creds are supplied per-request from the
client's `panel_jobs`.
"""

from __future__ import annotations

import json
import threading
import time
import uuid as _uuid
from typing import Optional

from app.services import accounts

_ROLES = ("primary", "standby")
_lock = threading.Lock()


def _path(account_id: Optional[str]):
    aid = account_id or accounts.current_account.get()
    if not aid:
        raise RuntimeError("No active account in context")
    return accounts.data_dir(aid) / "panel_groups.json"


def _id() -> str:
    return _uuid.uuid4().hex[:12]


def load_groups(account_id: Optional[str] = None) -> list[dict]:
    path = _path(account_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save_groups(groups: list[dict], account_id: Optional[str] = None) -> None:
    path = _path(account_id)
    # Atomic write: a concurrent reader never sees a half-written file, and a crash
    # mid-write can't corrupt the store.
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(groups, ensure_ascii=False, indent=2), encoding="utf-8")
    import os

    os.replace(tmp, path)


def get_group(group_id: str, account_id: Optional[str] = None) -> Optional[dict]:
    return next((g for g in load_groups(account_id) if g.get("id") == group_id), None)


def _normalize_members(members: list[dict]) -> list[dict]:
    """Validate/coerce members. Raises ValueError on bad role or duplicate priority."""
    out = []
    seen_prio: set[int] = set()
    seen_key: set[str] = set()
    for m in members or []:
        key = str(m.get("panel_key", "")).strip()
        if not key:
            raise ValueError("member.panel_key обязателен")
        if key in seen_key:
            raise ValueError("панель не может входить в группу дважды")
        seen_key.add(key)
        role = m.get("role", "standby")
        if role not in _ROLES:
            raise ValueError(f"role должен быть одним из {_ROLES}")
        prio = int(m.get("priority", 0))
        if prio in seen_prio:
            raise ValueError("приоритеты внутри группы должны быть уникальны")
        seen_prio.add(prio)
        out.append({"panel_key": key, "priority": prio, "role": role})
    return out


def add_group(body: dict, account_id: Optional[str] = None) -> dict:
    group = {
        "id": _id(),
        "name": str(body.get("name", "")).strip() or "Группа",
        "auto_sync": bool(body.get("auto_sync", False)),
        "interval_hours": max(1, int(body.get("interval_hours", 24))),
        "members": _normalize_members(body.get("members", [])),
        "last_sync_at": None,
        "last_sync_status": None,
        "created_at": int(time.time()),
    }
    with _lock:
        groups = load_groups(account_id)
        groups.append(group)
        save_groups(groups, account_id)
    return group


def update_group(
    group_id: str, patch: dict, account_id: Optional[str] = None
) -> Optional[dict]:
    with _lock:
        groups = load_groups(account_id)
        found = next((g for g in groups if g.get("id") == group_id), None)
        if not found:
            return None
        if "name" in patch:
            found["name"] = str(patch["name"]).strip() or found["name"]
        if "auto_sync" in patch:
            found["auto_sync"] = bool(patch["auto_sync"])
        if "interval_hours" in patch:
            found["interval_hours"] = max(1, int(patch["interval_hours"]))
        if "members" in patch:
            found["members"] = _normalize_members(patch["members"])
        for k in ("last_sync_at", "last_sync_status"):
            if k in patch:
                found[k] = patch[k]
        save_groups(groups, account_id)
        return found


def remove_group(group_id: str, account_id: Optional[str] = None) -> bool:
    with _lock:
        groups = load_groups(account_id)
        kept = [g for g in groups if g.get("id") != group_id]
        if len(kept) == len(groups):
            return False
        save_groups(kept, account_id)
        return True


def nearest_higher_primary(members: list[dict], standby_key: str) -> Optional[dict]:
    """The primary a standby should restore FROM: the primary with the smallest
    priority strictly greater than the standby's own (the one just above it).
    Returns None if the key isn't a standby, or there's no higher primary."""
    standby = next((m for m in members if m.get("panel_key") == standby_key), None)
    if not standby or standby.get("role") != "standby":
        return None
    sp = int(standby.get("priority", 0))
    higher = [
        m
        for m in members
        if m.get("role") == "primary" and int(m.get("priority", 0)) > sp
    ]
    if not higher:
        return None
    return min(higher, key=lambda m: int(m.get("priority", 0)))
