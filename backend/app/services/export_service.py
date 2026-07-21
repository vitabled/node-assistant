"""Wave-5 Plan L (slice 1) — export/import a node-assistant account's per-account
JSON data as a portable .tar.gz (manifest + data/<store>.json).

Scope (v1): the JSON store layer. Secrets are STRIPPED by default (settings tokens
+ *_enc fields zeroed, netbird PAT file excluded). GLOBAL files (accounts.json,
metrics db, mcp_owner) are never included. Import replaces each included store
(settings.json merges so it never wipes the target's existing secrets), gated by
`confirm`. Deferred: SQLite row-dumps, password-encrypted full export, slice-2
(Remnawave panel snapshot)."""
from __future__ import annotations

import io
import json
import tarfile
import time
from typing import Optional

from app.services import accounts

FORMAT_VERSION = 1

# Per-account JSON stores (storage.py layer + a couple of side stores).
_JSON_FILES = [
    "settings.json", "templates.json", "traffic_rules.json", "subscriptions.json",
    "domains.json", "hosts.json", "checkers.json", "rules.json", "testservers.json",
    "certwarden.json", "hostings.json", "panel_groups.json", "config_templates.json",
    "prompt_presets.json", "stat_widgets.json",
]
# Excluded when secrets are stripped (opaque credential vault file).
_SECRET_EXCLUDE = {"netbird.json"}


def available_stores() -> list[str]:
    return list(_JSON_FILES)


def _strip_secrets(name: str, data):
    if name == "settings.json" and isinstance(data, dict):
        rw = data.get("remnawave")
        if isinstance(rw, dict):
            rw["api_token"] = ""
        dd = data.get("deploy_defaults")
        if isinstance(dd, dict):
            dd["cloudflare_api_key"] = ""
        xc = data.get("xray_checker")
        if isinstance(xc, dict):
            xc["subscription_url"] = ""
        for k in ("mcp", "ai"):
            sub = data.get(k)
            if isinstance(sub, dict):
                for f in list(sub):
                    if f.endswith("_enc"):
                        sub[f] = ""
        reg = data.get("remnawave_registry")
        if isinstance(reg, dict):
            for p in reg.get("panels") or []:
                if isinstance(p, dict):
                    p["api_token"] = ""
    return data


def build_archive(account_id: str, stores: Optional[list[str]] = None,
                  include_secrets: bool = False) -> bytes:
    d = accounts.data_dir(account_id)
    included: list[str] = []
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in _JSON_FILES:
            if stores and name not in stores:
                continue
            if not include_secrets and name in _SECRET_EXCLUDE:
                continue
            p = d / name
            if not p.exists():
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not include_secrets:
                data = _strip_secrets(name, data)
            raw = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            info = tarfile.TarInfo(f"data/{name}")
            info.size = len(raw)
            tar.addfile(info, io.BytesIO(raw))
            included.append(name)
        manifest = {
            "format_version": FORMAT_VERSION, "exported_at": int(time.time()),
            "source_account_id": account_id, "stores": included,
            "include_secrets": bool(include_secrets),
        }
        mraw = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        mi = tarfile.TarInfo("manifest.json")
        mi.size = len(mraw)
        tar.addfile(mi, io.BytesIO(mraw))
    return buf.getvalue()


# Settings subsections that carry credentials — a no-secrets import must NOT
# touch the target's version of these (would wipe its tokens). Non-secret
# subsections (optimization/appearance/…) import normally.
_SETTINGS_SECRET_SECTIONS = ("remnawave", "remnawave_registry", "deploy_defaults", "xray_checker", "mcp", "ai")


def _merge_settings(target, incoming):
    """Import non-secret settings while keeping the target's credential sections
    intact (the export is stripped, so importing them would blank the target)."""
    if not isinstance(incoming, dict):
        return incoming
    try:
        existing = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    except Exception:
        existing = {}
    merged = dict(incoming)
    for k in _SETTINGS_SECRET_SECTIONS:
        if k in existing:
            merged[k] = existing[k]
    return merged


def restore_archive(account_id: str, blob: bytes) -> dict:
    """Restore an archive into the account (replace-per-store; settings.json merges
    to preserve existing secrets). Unknown stores are skipped (forward-compat)."""
    d = accounts.data_dir(account_id)
    d.mkdir(parents=True, exist_ok=True)
    applied: dict[str, int] = {}
    skipped: list[str] = []
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        try:
            mf = tar.extractfile("manifest.json")
            manifest = json.loads(mf.read().decode("utf-8")) if mf else {}
        except KeyError:
            raise ValueError("Архив без manifest.json")
        if manifest.get("format_version") != FORMAT_VERSION:
            raise ValueError("Неподдерживаемая версия формата архива")
        for member in tar.getmembers():
            if not (member.name.startswith("data/") and member.name.endswith(".json")):
                continue
            name = member.name[len("data/"):]
            if name not in _JSON_FILES:
                skipped.append(name)
                continue
            f = tar.extractfile(member)
            if not f:
                continue
            try:
                incoming = json.loads(f.read().decode("utf-8"))
            except Exception:
                skipped.append(name)
                continue
            target = d / name
            if name == "settings.json":
                incoming = _merge_settings(target, incoming)
            target.write_text(json.dumps(incoming, ensure_ascii=False, indent=2), encoding="utf-8")
            applied[name] = 1
    return {"applied": applied, "skipped": skipped}
