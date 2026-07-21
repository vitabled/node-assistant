"""Wave-5 Plan I — system-prompt presets for the built-in AI agent.

Built-in presets are read-only repo assets (backend/app/assets/prompts/); user
presets are per-account JSON (storage.py). The active preset id lives on
AiConfig; resolve_active_text falls back to the `default` builtin (= today's
behaviour) when empty/unknown/unavailable. Prompts are NOT secrets → plain JSON.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Optional

from app.services import storage

_ASSETS = Path(__file__).resolve().parent.parent / "assets" / "prompts"
DEFAULT_ID = "default"


def _load_builtin() -> list[dict]:
    try:
        manifest = json.loads((_ASSETS / "PRESETS.json").read_text(encoding="utf-8"))
    except Exception:
        return []
    out: list[dict] = []
    for p in manifest.get("presets", []):
        text = ""
        try:
            text = (_ASSETS / p["file"]).read_text(encoding="utf-8").strip()
        except Exception:
            pass
        out.append({
            "id": p["id"], "name": p["name"], "text": text, "builtin": True,
            "source_url": p.get("source_url"), "license": p.get("license"),
            "unavailable": bool(p.get("unavailable", False)),
        })
    return out


def _builtin_by_id(pid: str) -> Optional[dict]:
    return next((p for p in _load_builtin() if p["id"] == pid), None)


def list_presets(account_id: Optional[str] = None) -> list[dict]:
    builtin = _load_builtin()
    ids = {p["id"] for p in builtin}
    user = [{**p, "builtin": False} for p in storage.load_prompt_presets(account_id) if p.get("id") not in ids]
    return builtin + user


def get_preset(pid: str, account_id: Optional[str] = None) -> Optional[dict]:
    return next((p for p in list_presets(account_id) if p["id"] == pid), None)


def create_preset(name: str, text: str, account_id: Optional[str] = None) -> dict:
    entry = {"id": "u_" + uuid.uuid4().hex[:10], "name": name, "text": text,
             "builtin": False, "created_at": int(time.time())}
    items = storage.load_prompt_presets(account_id)
    items.append(entry)
    storage.save_prompt_presets(items, account_id)
    return entry


def update_preset(pid: str, name: str, text: str, account_id: Optional[str] = None) -> Optional[dict]:
    if _builtin_by_id(pid):
        raise ValueError("Встроенный пресет нельзя изменить")
    items = storage.load_prompt_presets(account_id)
    idx = next((i for i, p in enumerate(items) if p.get("id") == pid), None)
    if idx is None:
        return None
    items[idx] = {**items[idx], "name": name, "text": text}
    storage.save_prompt_presets(items, account_id)
    return items[idx]


def delete_preset(pid: str, account_id: Optional[str] = None) -> bool:
    if _builtin_by_id(pid):
        raise ValueError("Встроенный пресет нельзя удалить")
    items = storage.load_prompt_presets(account_id)
    kept = [p for p in items if p.get("id") != pid]
    if len(kept) == len(items):
        return False
    storage.save_prompt_presets(kept, account_id)
    return True


def fork_preset(builtin_id: str, account_id: Optional[str] = None) -> dict:
    b = _builtin_by_id(builtin_id) or get_preset(builtin_id, account_id)
    if not b:
        raise ValueError("Пресет не найден")
    return create_preset(f"{b['name']} (копия)", b.get("text", ""), account_id)


def resolve_active_text(active_preset_id: str, account_id: Optional[str] = None) -> str:
    """Text of the active preset, falling back to the `default` builtin."""
    default = _builtin_by_id(DEFAULT_ID)
    default_text = default["text"] if default else ""
    if not active_preset_id:
        return default_text
    p = get_preset(active_preset_id, account_id)
    if not p or p.get("unavailable") or not p.get("text"):
        return default_text
    return p["text"]
