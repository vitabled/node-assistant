"""Shared per-account media store — images and attachments used ACROSS sections.

Two consumers so far: hosting cards (screenshots of a panel, a price list) and
note embeds in the Library (`![[id]]` inside markdown). One store rather than one
per section, so an upload guard, a size cap and a preview only have to be right
once.

⚠️ Serving user-uploaded bytes from OUR origin is the risk here, and it is why
the mime allow-list is split in two:

- `INLINE_MIME` — raster images only. They render inline (that is the whole point
  of embedding), and a browser cannot be tricked into executing a PNG.
- everything else — handed out as an opaque attachment (`octet-stream`), the same
  rule §11h установил for subpage overlay members.

**SVG is deliberately NOT inline-able**: it is an XML document that can carry
`<script>`, so an inline SVG upload would be stored XSS against the panel.

The Library's own `file` items (`library_store`) stay where they are: those are
documents the user filed away, this is media referenced from somewhere else.
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

MAX_ITEMS = 2000
MAX_FILE_BYTES = 15 * 1024 * 1024          # 15 MiB — a screenshot, not a video dump
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._()\-]+")

# Rendered inline by the browser. Raster only — see the module docstring.
INLINE_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/avif": ".avif",
}
# Accepted, stored, downloadable — never rendered on our origin.
ATTACH_MIME = {
    "image/svg+xml": ".svg",
    "application/pdf": ".pdf",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "text/plain": ".txt",
}


def is_inline(mime: str) -> bool:
    return (mime or "").lower() in INLINE_MIME


def _dir(account_id: Optional[str]) -> Path:
    aid = account_id or accounts.current_account.get()
    if not aid:
        raise RuntimeError("No active account in context")
    return accounts.data_dir(aid) / "media"


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


def _safe(name: str) -> str:
    return (_SAFE_NAME.sub("_", (name or "").strip())[:200]) or "file"


def _public(entry: dict) -> dict:
    """The stored path never leaves the backend."""
    return {k: v for k, v in entry.items() if k != "path"}


def list_items(account_id: Optional[str] = None) -> list[dict]:
    return [_public(it) for it in _read(account_id)]


def add(name: str, content: bytes, mime: str, account_id: Optional[str] = None) -> dict:
    mime = (mime or "").split(";")[0].strip().lower()
    if mime not in INLINE_MIME and mime not in ATTACH_MIME:
        raise ValueError(f"Тип файла не поддерживается: {mime or 'неизвестен'}")
    if not content:
        raise ValueError("Пустой файл")
    if len(content) > MAX_FILE_BYTES:
        raise ValueError(f"Файл больше {MAX_FILE_BYTES // (1024 * 1024)} МБ")
    with _LOCK:
        items = _read(account_id)
        if len(items) >= MAX_ITEMS:
            raise ValueError(f"Достигнут лимит медиа ({MAX_ITEMS})")
        mid = uuid.uuid4().hex[:12]
        safe = _safe(name)
        files_dir = _dir(account_id) / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        # The extension comes from the ALLOWED mime, not from the upload's name:
        # an attacker-chosen "shot.png.html" must not end up on disk as .html.
        ext = INLINE_MIME.get(mime) or ATTACH_MIME.get(mime) or ".bin"
        stored = files_dir / f"{mid}{ext}"
        stored.write_bytes(content)
        entry = {
            "id": mid, "name": name or safe, "mime": mime, "size": len(content),
            "inline": mime in INLINE_MIME, "path": stored.name,
            "created_at": int(time.time()),
        }
        items.append(entry)
        _write(account_id, items)
    return _public(entry)


def get(media_id: str, account_id: Optional[str] = None) -> Optional[tuple[bytes, str, str]]:
    """`(bytes, mime, display name)` or None."""
    it = next((x for x in _read(account_id) if x.get("id") == media_id), None)
    if it is None:
        return None
    files_dir = (_dir(account_id) / "files").resolve()
    p = _dir(account_id) / "files" / it.get("path", "")
    # Defence-in-depth: the resolved path must stay inside the account's dir.
    if not p.exists() or files_dir not in p.resolve().parents:
        return None
    return p.read_bytes(), it.get("mime", "application/octet-stream"), it.get("name", "file")


def get_meta(media_id: str, account_id: Optional[str] = None) -> Optional[dict]:
    it = next((x for x in _read(account_id) if x.get("id") == media_id), None)
    return _public(it) if it else None


def rename(media_id: str, name: str, account_id: Optional[str] = None) -> Optional[dict]:
    with _LOCK:
        items = _read(account_id)
        it = next((x for x in items if x.get("id") == media_id), None)
        if it is None:
            return None
        it["name"] = (name or it["name"])[:200]
        _write(account_id, items)
        return _public(it)


def delete(media_id: str, account_id: Optional[str] = None) -> bool:
    with _LOCK:
        items = _read(account_id)
        it = next((x for x in items if x.get("id") == media_id), None)
        if it is None:
            return False
        try:
            (_dir(account_id) / "files" / it.get("path", "")).unlink(missing_ok=True)
        except Exception:
            pass
        _write(account_id, [x for x in items if x.get("id") != media_id])
    return True
