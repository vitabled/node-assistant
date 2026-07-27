"""Per-account «Хранилище» — the user's own secrets (Wave-9 Plan A Ф1).

API keys, SSH passwords, SSH private keys, logins and hosting-provider creds the
operator chooses to keep in the panel instead of retyping them. This is a
**module-scoped relaxation** of the project's "no third-party secrets at rest"
rule (same shape as infra_billing_store / rules_store): the user explicitly asked
for a manager, so the secrets persist — encrypted with Fernet (key = SHA-256 of
`settings.encryption_key`) and never returned by the list/get paths.

Three decisions worth stating, because each had an alternative:

* **JSON, not SQLite.** `export_service` only walks JSON stores and it zeroes any
  field whose name ends in `_enc` — so calling the ciphertext field `fields_enc`
  buys export/import and the Telegram auto-backup with secret-stripping for free.
  Volume is tens of entries; there is no schema to migrate.
* **One Fernet blob over a JSON object of fields**, not one row per field: provider
  creds need 2-5 fields (Oracle 5, Beget 2), and an entry-per-field would be noise.
  One blob = one decryption path.
* **A broken secret never raises.** If `ENCRYPTION_KEY` changed, `read_fields`
  returns None and `list_entries` flags the entry `broken` — one unreadable secret
  must not take the whole page down.

Storage layout mirrors the other JSON stores: `accounts/<id>/vault.json` holding
`{"entries": [...]}`, atomic writes (temp file + replace) with the
read-modify-write serialised under a process-wide lock.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings
from app.services import accounts

_LOCK = threading.Lock()

KINDS = ("api_key", "ssh_password", "ssh_key", "login", "provider_creds", "note")

MAX_ENTRIES = 500
MAX_SECRET_BYTES = 64 * 1024  # ed25519 ~400 B, RSA-4096 ~3 KB — ample headroom
MAX_NAME = 80
MAX_RESOURCE = 200
MAX_TAGS = 10
MAX_TAG_LEN = 24


# ── ids / time ────────────────────────────────────────────────
def _id() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> int:
    return int(time.time())


# ── Fernet (secrets at rest) ──────────────────────────────────
def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.encryption_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def _decrypt(token: str) -> Optional[str]:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, Exception):
        return None


def _mask(secret: str) -> str:
    """`sk-a********` — a short prefix so the user recognises WHICH secret this is.
    The star run is fixed-width on purpose: scaling it with len(secret) would leak
    the secret's length into a hint that is shown without any reveal."""
    if not secret:
        return ""
    keep = min(4, max(1, len(secret) // 4))
    return secret[:keep] + "*" * 8


# ── persistence ───────────────────────────────────────────────
def _path(account_id: Optional[str]) -> Path:
    aid = account_id or accounts.current_account.get()
    if not aid:
        raise RuntimeError("No active account in context")
    return accounts.data_dir(aid) / "vault.json"


def _read(account_id: Optional[str]) -> list[dict]:
    p = _path(account_id)
    try:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                items = data.get("entries")
                return items if isinstance(items, list) else []
    except Exception:
        pass
    return []


def _write(account_id: Optional[str], items: list[dict]) -> None:
    p = _path(account_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({"entries": items}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(p)


# ── validation / normalisation ────────────────────────────────
def _norm_tags(tags: Optional[list[str]]) -> list[str]:
    out: list[str] = []
    for raw in tags or []:
        # split() collapses any whitespace run (incl. CR/LF) and trims.
        t = " ".join(str(raw or "").split())[:MAX_TAG_LEN]
        if t and t not in out:
            out.append(t)
        if len(out) >= MAX_TAGS:
            break
    return out


def _clean_fields(fields: Optional[dict]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in (fields or {}).items():
        key = " ".join(str(k).split())
        if key:
            out[key] = v if isinstance(v, str) else str(v)
    return out


def _encode_fields(fields: dict[str, str]) -> str:
    blob = json.dumps(fields, ensure_ascii=False)
    if len(blob.encode("utf-8")) > MAX_SECRET_BYTES:
        raise ValueError(f"Секрет слишком большой (максимум {MAX_SECRET_BYTES // 1024} KiB)")
    return _encrypt(blob)


def _decode_fields(blob: Optional[str]) -> Optional[dict[str, str]]:
    """Plaintext fields, `{}` when the entry carries no secret, None when the
    ciphertext no longer decrypts (a changed ENCRYPTION_KEY)."""
    if not blob:
        return {}
    raw = _decrypt(str(blob))
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _check_kind(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"Неизвестный тип записи: {kind}")
    return kind


def _check_name(name: str) -> str:
    name = str(name or "").strip()
    if not name:
        raise ValueError("Название не может быть пустым")
    if len(name) > MAX_NAME:
        raise ValueError(f"Название длиннее {MAX_NAME} символов")
    return name


def _check_resource(resource: str) -> str:
    resource = str(resource or "").strip()
    if len(resource) > MAX_RESOURCE:
        raise ValueError(f"Ресурс длиннее {MAX_RESOURCE} символов")
    return resource


# ── public shape (never carries a secret) ─────────────────────
def _public(entry: dict) -> dict:
    fields = _decode_fields(entry.get("fields_enc"))
    broken = fields is None
    names = list(fields.keys()) if fields else []
    # isinstance guard: the file is hand-editable, so a value may not be a string.
    first = next((v for v in fields.values() if isinstance(v, str) and v), "") if fields else ""
    return {
        "id": entry.get("id", ""),
        "name": entry.get("name", ""),
        "kind": entry.get("kind", ""),
        "resource": entry.get("resource", ""),
        "username": entry.get("username", ""),
        "note": entry.get("note", ""),
        "tags": entry.get("tags", []) or [],
        "field_names": names,
        "hint": _mask(first),
        "has_secret": bool(names),
        "broken": broken,
        "created_at": entry.get("created_at", 0),
        "updated_at": entry.get("updated_at", 0),
        "revealed_at": entry.get("revealed_at"),
    }


# ── CRUD ──────────────────────────────────────────────────────
def list_entries(account_id: Optional[str] = None) -> list[dict]:
    return [_public(e) for e in _read(account_id)]


def get_entry(entry_id: str, account_id: Optional[str] = None) -> Optional[dict]:
    entry = next((e for e in _read(account_id) if e.get("id") == entry_id), None)
    return _public(entry) if entry else None


def create_entry(
    *,
    name: str,
    kind: str,
    resource: str = "",
    username: str = "",
    note: str = "",
    tags: Optional[list[str]] = None,
    fields: dict,
    account_id: Optional[str] = None,
) -> dict:
    now = _now()
    entry = {
        "id": _id(),
        "name": _check_name(name),
        "kind": _check_kind(kind),
        "resource": _check_resource(resource),
        "username": str(username or ""),
        "note": str(note or ""),
        "tags": _norm_tags(tags),
        "fields_enc": _encode_fields(_clean_fields(fields)),
        "created_at": now,
        "updated_at": now,
        "revealed_at": None,
    }
    with _LOCK:
        items = _read(account_id)
        if len(items) >= MAX_ENTRIES:
            raise ValueError(f"Достигнут лимит записей ({MAX_ENTRIES})")
        items.append(entry)
        _write(account_id, items)
    return _public(entry)


def update_entry(
    entry_id: str,
    *,
    name: Optional[str] = None,
    kind: Optional[str] = None,
    resource: Optional[str] = None,
    username: Optional[str] = None,
    note: Optional[str] = None,
    tags: Optional[list[str]] = None,
    fields: Optional[dict] = None,
    account_id: Optional[str] = None,
) -> Optional[dict]:
    """Patch semantics: a None argument leaves the field alone. In particular
    `fields=None` keeps the stored secret (blank-keeps — the editor never has to
    round-trip a plaintext secret just to rename an entry)."""
    with _LOCK:
        items = _read(account_id)
        idx = next((i for i, e in enumerate(items) if e.get("id") == entry_id), None)
        if idx is None:
            return None
        entry = items[idx]
        if name is not None:
            entry["name"] = _check_name(name)
        if kind is not None:
            entry["kind"] = _check_kind(kind)
        if resource is not None:
            entry["resource"] = _check_resource(resource)
        if username is not None:
            entry["username"] = str(username)
        if note is not None:
            entry["note"] = str(note)
        if tags is not None:
            entry["tags"] = _norm_tags(tags)
        if fields is not None:
            entry["fields_enc"] = _encode_fields(_clean_fields(fields))
        entry["updated_at"] = _now()
        _write(account_id, items)
        return _public(entry)


def delete_entry(entry_id: str, account_id: Optional[str] = None) -> bool:
    with _LOCK:
        items = _read(account_id)
        kept = [e for e in items if e.get("id") != entry_id]
        if len(kept) == len(items):
            return False
        _write(account_id, kept)
    return True


def read_fields(entry_id: str, account_id: Optional[str] = None) -> Optional[dict]:
    """Internal plaintext resolve (ssh-auth, provider adapters, the reveal route).
    None = no such entry, or the secret no longer decrypts."""
    entry = next((e for e in _read(account_id) if e.get("id") == entry_id), None)
    if entry is None:
        return None
    return _decode_fields(entry.get("fields_enc"))


def touch_revealed(entry_id: str, account_id: Optional[str] = None) -> None:
    """Audit trail: when the secret was last looked at. Deliberately does NOT
    bump `updated_at` — reading is not editing."""
    with _LOCK:
        items = _read(account_id)
        entry = next((e for e in items if e.get("id") == entry_id), None)
        if entry is None:
            return
        entry["revealed_at"] = _now()
        _write(account_id, items)


# ── async wrappers (blocking json/crypto off the event loop) ──
async def a_list_entries(account_id: Optional[str] = None) -> list[dict]:
    return await asyncio.to_thread(list_entries, account_id)


async def a_get_entry(entry_id: str, account_id: Optional[str] = None) -> Optional[dict]:
    return await asyncio.to_thread(get_entry, entry_id, account_id)


async def a_create_entry(**kwargs) -> dict:
    return await asyncio.to_thread(lambda: create_entry(**kwargs))


async def a_update_entry(entry_id: str, **kwargs) -> Optional[dict]:
    return await asyncio.to_thread(lambda: update_entry(entry_id, **kwargs))


async def a_delete_entry(entry_id: str, account_id: Optional[str] = None) -> bool:
    return await asyncio.to_thread(delete_entry, entry_id, account_id)


async def a_read_fields(entry_id: str, account_id: Optional[str] = None) -> Optional[dict]:
    return await asyncio.to_thread(read_fields, entry_id, account_id)


async def a_touch_revealed(entry_id: str, account_id: Optional[str] = None) -> None:
    await asyncio.to_thread(touch_revealed, entry_id, account_id)
