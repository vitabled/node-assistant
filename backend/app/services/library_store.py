"""Wave-5 Plan C (scoped) — per-account knowledge library: file storage + markdown
notes. Files live under accounts/<id>/library/files/, metadata in library/index.json.

Scoped v1 (no new pip deps): store/list/download/delete files + CRUD markdown
notes. Deferred: server-side text extraction (pdf/docx/xlsx), FTS5 full-text
search, rich in-app viewers.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from app.services import accounts

_LOCK = threading.Lock()
MAX_ITEMS = 500
MAX_FILE_BYTES = 25 * 1024 * 1024  # 25 MiB per file
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._()\-]+")


def _dir(account_id: Optional[str]) -> Path:
    aid = account_id or accounts.current_account.get()
    if not aid:
        raise RuntimeError("No active account in context")
    return accounts.data_dir(aid) / "library"


def _index_path(account_id: Optional[str]) -> Path:
    return _dir(account_id) / "index.json"


def _read(account_id: Optional[str]) -> list[dict]:
    p = _index_path(account_id)
    try:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


def _write(account_id: Optional[str], items: list[dict]) -> None:
    d = _dir(account_id)
    d.mkdir(parents=True, exist_ok=True)
    tmp = _index_path(account_id).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_index_path(account_id))


def list_items(account_id: Optional[str] = None) -> list[dict]:
    # Never expose the note body / stored path in the list view.
    return [{k: v for k, v in it.items() if k not in ("text", "path")} for it in _read(account_id)]


def _safe(name: str) -> str:
    name = _SAFE_NAME.sub("_", (name or "").strip())[:200]
    return name or "file"


def add_file(name: str, content: bytes, mime: str, account_id: Optional[str] = None) -> dict:
    if len(content) > MAX_FILE_BYTES:
        raise ValueError(f"Файл больше {MAX_FILE_BYTES // (1024 * 1024)} МБ")
    with _LOCK:
        items = _read(account_id)
        if len(items) >= MAX_ITEMS:
            raise ValueError(f"Достигнут лимит ({MAX_ITEMS})")
        fid = uuid.uuid4().hex[:12]
        safe = _safe(name)
        files_dir = _dir(account_id) / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        stored = files_dir / f"{fid}_{safe}"
        stored.write_bytes(content)
        entry = {"id": fid, "kind": "file", "name": name or safe, "filename": safe,
                 "mime": mime or "application/octet-stream", "size": len(content),
                 "path": stored.name, "created_at": int(time.time())}
        items.append(entry)
        _write(account_id, items)
    return {k: v for k, v in entry.items() if k != "path"}


def add_note(name: str, text: str, account_id: Optional[str] = None) -> dict:
    with _LOCK:
        items = _read(account_id)
        if len(items) >= MAX_ITEMS:
            raise ValueError(f"Достигнут лимит ({MAX_ITEMS})")
        entry = {"id": uuid.uuid4().hex[:12], "kind": "note", "name": name or "Заметка",
                 "text": text or "", "created_at": int(time.time())}
        items.append(entry)
        _write(account_id, items)
    return {k: v for k, v in entry.items() if k != "text"}


def get_note(item_id: str, account_id: Optional[str] = None) -> Optional[dict]:
    it = next((x for x in _read(account_id) if x.get("id") == item_id and x.get("kind") == "note"), None)
    return it


def update_note(item_id: str, name: str, text: str, account_id: Optional[str] = None) -> Optional[dict]:
    with _LOCK:
        items = _read(account_id)
        it = next((x for x in items if x.get("id") == item_id and x.get("kind") == "note"), None)
        if it is None:
            return None
        it["name"] = name or it["name"]
        it["text"] = text
        _write(account_id, items)
        return {k: v for k, v in it.items() if k != "text"}


def get_file(item_id: str, account_id: Optional[str] = None) -> Optional[tuple[bytes, str, str]]:
    it = next((x for x in _read(account_id) if x.get("id") == item_id and x.get("kind") == "file"), None)
    if it is None:
        return None
    p = _dir(account_id) / "files" / it.get("path", "")
    # Defence-in-depth: keep the resolved path inside the account's files dir.
    files_dir = (_dir(account_id) / "files").resolve()
    if not p.exists() or files_dir not in p.resolve().parents:
        return None
    return p.read_bytes(), it.get("filename", "file"), it.get("mime", "application/octet-stream")


def delete_item(item_id: str, account_id: Optional[str] = None) -> bool:
    with _LOCK:
        items = _read(account_id)
        it = next((x for x in items if x.get("id") == item_id), None)
        if it is None:
            return False
        if it.get("kind") == "file" and it.get("path"):
            try:
                (_dir(account_id) / "files" / it["path"]).unlink(missing_ok=True)
            except Exception:
                pass
        _write(account_id, [x for x in items if x.get("id") != item_id])
    return True
