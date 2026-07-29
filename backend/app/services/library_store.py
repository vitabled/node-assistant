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
    out = []
    for it in _read(account_id):
        if it.get("kind") == "folder":
            # A folder row's `path` is the folder itself, not a name on disk —
            # unlike a file's, it is exactly what the tree renders.
            out.append(dict(it))
            continue
        # Never expose the note body / stored path in the list view.
        row = {k: v for k, v in it.items() if k not in ("text", "path")}
        # Notes written before folders/ordering existed have no `folder`/`order`
        # key; default them here so the tree never has to reason about undefined.
        if row.get("kind") == "note":
            row.setdefault("folder", "")
            row.setdefault("order", 0)
            row.setdefault("updated_at", row.get("created_at", 0))
        out.append(row)
    return out


def search_notes(query: str, limit: int = 10, account_id: Optional[str] = None) -> list[dict]:
    """Заметки, где встречается `query` (в имени, папке или тексте).

    Живёт здесь, а не у вызывающего, ровно по одной причине: искать по телу
    заметки можно только имея сам текст, а `list_items` его намеренно вырезает.
    Читать индекс по одной заметке через `get_note` — это N чтений файла на один
    поиск. Возвращаем фрагмент вокруг совпадения, а не весь текст: вызывающему
    (ассистенту) нужен ориентир, полный текст он попросит по id.
    """
    needle = (query or "").strip().lower()
    if not needle:
        return []
    out: list[dict] = []
    for it in _read(account_id):
        if it.get("kind") != "note":
            continue
        text = it.get("text") or ""
        hay = f"{it.get('name', '')}\n{it.get('folder', '')}\n{text}".lower()
        pos = hay.find(needle)
        if pos < 0:
            continue
        start = max(0, text.lower().find(needle) - 120)
        out.append({
            "id": it.get("id"),
            "name": it.get("name"),
            "folder": it.get("folder", ""),
            "updated_at": it.get("updated_at", it.get("created_at", 0)),
            "excerpt": text[start:start + 400],
        })
        if len(out) >= max(1, limit):
            break
    return out


def _safe(name: str) -> str:
    name = _SAFE_NAME.sub("_", (name or "").strip())[:200]
    return name or "file"


# ── Obsidian-style notes: folders + wiki-links ────────────────
# `[[Заметка]]` links and `![[media-id]]` embeds. The embed form is matched first
# by the leading `!`, so a link regex must not swallow it — hence the negative
# lookbehind.
_WIKI_LINK = re.compile(r"(?<!!)\[\[([^\]|#]+?)(?:[|#][^\]]*)?\]\]")
_WIKI_EMBED = re.compile(r"!\[\[([^\]|]+?)(?:\|[^\]]*)?\]\]")
MAX_FOLDER_DEPTH = 8


def norm_folder(folder: str) -> str:
    """`"/Инфра//Провайдеры/"` → `"Инфра/Провайдеры"`.

    A folder is just a path string on the note (same model as Obsidian, where a
    folder has no identity of its own) — so it needs normalising, not a table.
    Backslashes are rejected rather than translated: the value is not a filesystem
    path and never reaches one, and silently accepting both separators would make
    `A\\B` and `A/B` two different folders in the tree.
    """
    parts = [p.strip() for p in (folder or "").replace("\\", "/").split("/")]
    parts = [p for p in parts if p and p not in (".", "..")]
    return "/".join(parts[:MAX_FOLDER_DEPTH])[:300]


def _links_of(text: str) -> list[str]:
    seen: list[str] = []
    for m in _WIKI_LINK.finditer(text or ""):
        name = m.group(1).strip()
        if name and name not in seen:
            seen.append(name)
    return seen


def media_ids_of(text: str) -> list[str]:
    return [m.group(1).strip() for m in _WIKI_EMBED.finditer(text or "")]


def _retarget_links(items: list[dict], old: str, new: str, skip_id: str = "") -> None:
    """Point every `[[old]]` at `[[new]]`, keeping any `|alias` / `#anchor`."""
    if not old or not new or old == new:
        return
    pattern = re.compile(r"\[\[" + re.escape(old) + r"((?:[|#][^\]]*)?)\]\]")
    for it in items:
        if it.get("kind") != "note" or it.get("id") == skip_id:
            continue
        text = it.get("text") or ""
        fixed = pattern.sub(lambda m: f"[[{new}{m.group(1)}]]", text)
        if fixed != text:
            it["text"] = fixed


def graph(account_id: Optional[str] = None) -> dict:
    """`{id: {name, folder, out: [ids], in: [ids], unresolved: [names]}}`.

    Links are stored by NAME (that is what the user types), so resolution happens
    here, on read: a link to a note that does not exist yet is reported as
    `unresolved` instead of being dropped — Obsidian shows those too, and they are
    how you notice a typo."""
    notes = [x for x in _read(account_id) if x.get("kind") == "note"]
    by_name: dict[str, str] = {}
    for n in notes:
        by_name.setdefault((n.get("name") or "").strip().lower(), n["id"])
    out: dict[str, dict] = {
        n["id"]: {"name": n.get("name", ""), "folder": n.get("folder", ""),
                  "out": [], "in": [], "unresolved": []}
        for n in notes
    }
    for n in notes:
        for target in _links_of(n.get("text") or ""):
            tid = by_name.get(target.lower())
            if tid and tid != n["id"]:
                out[n["id"]]["out"].append(tid)
                out[tid]["in"].append(n["id"])
            elif not tid:
                out[n["id"]]["unresolved"].append(target)
    return out


def _move_subtree(items: list[dict], src: str, dst: str) -> int:
    """Re-parent everything under `src` to `dst` (`dst=""` → the root).

    Notes carry the folder in `folder`, empty-folder rows in `path` — both have to
    move, or renaming a folder would leave its own row behind under the old name.
    """
    moved = 0
    for it in items:
        kind = it.get("kind")
        if kind == "note":
            cur = it.get("folder") or ""
        elif kind == "folder":
            cur = it.get("path") or ""
        else:
            continue
        if cur != src and not cur.startswith(src + "/"):
            continue
        new = norm_folder(dst + cur[len(src):]) if dst else norm_folder(cur[len(src):])
        it["folder" if kind == "note" else "path"] = new
        moved += 1
    return moved


def _prune_folders(items: list[dict]) -> list[dict]:
    """Drop folder rows a move left path-less (moved to the root — the folder is
    gone as a name) or duplicated (a subtree can land on top of an existing one)."""
    seen: set[str] = set()
    out = []
    for it in items:
        if it.get("kind") == "folder":
            p = it.get("path") or ""
            if not p or p in seen:
                continue
            seen.add(p)
        out.append(it)
    return out


def add_folder(path: str, account_id: Optional[str] = None) -> dict:
    """Create an empty folder as a row of its own.

    A folder is otherwise only implied by the notes inside it, so a freshly
    created empty one would vanish on the next reload.
    """
    p = norm_folder(path)
    if not p:
        raise ValueError("Не указана папка")
    with _LOCK:
        items = _read(account_id)
        existing = next((x for x in items if x.get("kind") == "folder" and x.get("path") == p), None)
        if existing is not None:
            return dict(existing)
        if len(items) >= MAX_ITEMS:
            raise ValueError(f"Достигнут лимит ({MAX_ITEMS})")
        entry = {"id": uuid.uuid4().hex[:12], "kind": "folder", "path": p,
                 "created_at": int(time.time())}
        items.append(entry)
        _write(account_id, items)
    return dict(entry)


def rename_folder(src: str, dst: str, account_id: Optional[str] = None) -> int:
    """Move a folder subtree. Returns how many notes and folders moved."""
    src, dst = norm_folder(src), norm_folder(dst)
    if not src:
        raise ValueError("Не указана исходная папка")
    with _LOCK:
        items = _read(account_id)
        moved = _move_subtree(items, src, dst)
        if moved:
            _write(account_id, _prune_folders(items))
    return moved


def reorder(rows: list[dict], account_id: Optional[str] = None) -> int:
    """Apply `{id, folder, order}` to notes; returns how many were applied.

    One entry point serves both dragging a note into another folder and changing
    its position — the frontend knows both at drop time. `folder=None` leaves the
    note where it is; an id that no longer exists is skipped rather than failing
    the whole move (it may have been deleted in another tab).
    """
    changed = 0
    with _LOCK:
        items = _read(account_id)
        by_id = {x["id"]: x for x in items if x.get("kind") == "note" and x.get("id")}
        for row in rows or []:
            it = by_id.get(str(row.get("id") or ""))
            if it is None:
                continue
            folder = row.get("folder")
            if folder is not None:
                it["folder"] = norm_folder(folder)
            try:
                it["order"] = int(row.get("order") or 0)
            except (TypeError, ValueError):
                it["order"] = 0
            changed += 1
        if changed:
            _write(account_id, items)
    return changed


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


def add_note(name: str, text: str, folder: str = "", account_id: Optional[str] = None) -> dict:
    with _LOCK:
        items = _read(account_id)
        if len(items) >= MAX_ITEMS:
            raise ValueError(f"Достигнут лимит ({MAX_ITEMS})")
        now = int(time.time())
        entry = {"id": uuid.uuid4().hex[:12], "kind": "note", "name": name or "Заметка",
                 "folder": norm_folder(folder), "order": 0, "text": text or "",
                 "created_at": now, "updated_at": now}
        items.append(entry)
        _write(account_id, items)
    return {k: v for k, v in entry.items() if k != "text"}


def get_note(item_id: str, account_id: Optional[str] = None) -> Optional[dict]:
    it = next((x for x in _read(account_id) if x.get("id") == item_id and x.get("kind") == "note"), None)
    return it


def update_note(item_id: str, name: str, text: str, folder: Optional[str] = None,
                account_id: Optional[str] = None) -> Optional[dict]:
    with _LOCK:
        items = _read(account_id)
        it = next((x for x in items if x.get("id") == item_id and x.get("kind") == "note"), None)
        if it is None:
            return None
        old_name = it["name"]
        new_name = name or old_name
        it["name"] = new_name
        it["text"] = text
        if folder is not None:
            it["folder"] = norm_folder(folder)
        it["updated_at"] = int(time.time())
        # Renaming a note would silently orphan every `[[old name]]` pointing at
        # it, so the links are rewritten too — the behaviour Obsidian defaults to.
        if new_name != old_name:
            _retarget_links(items, old_name, new_name, skip_id=item_id)
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
        if it.get("kind") == "folder":
            # Deleting a folder must not take its contents with it — silently
            # dropping someone's notes is not an option, so the subtree is lifted
            # into the parent folder instead.
            path = it.get("path") or ""
            _move_subtree(items, path, path.rsplit("/", 1)[0] if "/" in path else "")
        rest = [x for x in items if x.get("id") != item_id]
        _write(account_id, _prune_folders(rest))
    return True
