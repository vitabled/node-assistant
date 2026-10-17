"""Read-only view of the per-account node-access store (`node_creds.json`).

This store is the SERVER's record of an already-known node's SSH access. The
Remnawave→cards sync may copy those credentials into a freshly created card, but
it must NEVER invent them: anything unexpected (missing file, unknown shape,
broken JSON) degrades to "no creds" — the card is still created, just without a
password. Only reads; nothing here ever writes.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from app.services import accounts

FILE_NAME = "node_creds.json"


def _records(raw: Any) -> list[dict[str, Any]]:
    """Accept the shapes the store may take, ignoring everything else."""
    if isinstance(raw, dict):
        if isinstance(raw.get("creds"), list):
            return [r for r in raw["creds"] if isinstance(r, dict)]
        if isinstance(raw.get("nodes"), list):
            return [r for r in raw["nodes"] if isinstance(r, dict)]
        # A map keyed by ip: {"1.2.3.4": {...}}
        out: list[dict[str, Any]] = []
        for key, value in raw.items():
            if isinstance(value, dict):
                out.append({"ip": key, **value})
        return out
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    return []


def load_creds(account_id: Optional[str] = None) -> list[dict[str, Any]]:
    """All stored node credentials for one account (empty when none/unreadable)."""
    aid = account_id or accounts.current_account.get()
    if not aid:
        return []
    path = accounts.account_dir(aid) / FILE_NAME
    try:
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return _records(raw)


def creds_by_ip(account_id: Optional[str] = None) -> dict[str, dict[str, Any]]:
    """Credentials keyed by node ip (first record wins on a duplicate)."""
    by_ip: dict[str, dict[str, Any]] = {}
    for record in load_creds(account_id):
        ip = str(record.get("ip") or "").strip()
        if ip and ip not in by_ip:
            by_ip[ip] = record
    return by_ip
