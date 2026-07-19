"""
Reality-inbound migration helper (Ф7).

The official `remnawave/migrate` tool moves USERS but not the Xray Reality
keypairs. This helper reads Marzban's live Xray config (`GET /api/core/config`),
extracts each Reality inbound's `realitySettings` by tag, and patches the SAME-tag
inbound inside an EXISTING Remnawave config-profile — it never adds or removes
inbounds (a tag with no Remnawave match is reported, not created).

`build_reality_patch` is a PURE function (Marzban config + profile config →
patched profile config + report) so it is fully unit-testable.

Also `legacy_secret_cmd()` — the ONE direct Marzban DB read (`SELECT secret_key
FROM jwt`) needed to keep old subscription links working. Run over SSH silently
(the secret must never hit a log).
"""

from __future__ import annotations

import copy
from typing import Any

# realitySettings keys we carry over (superset — only present ones are copied).
_REALITY_KEYS = (
    "privateKey",
    "publicKey",
    "shortIds",
    "serverNames",
    "dest",
    "target",
    "spiderX",
    "fingerprint",
    "xver",
    "maxTimeDiff",
)


def _reality_inbounds(config: dict) -> dict[str, dict]:
    """tag → inbound, only for inbounds that carry realitySettings."""
    out: dict[str, dict] = {}
    for inb in (config or {}).get("inbounds") or []:
        rs = ((inb.get("streamSettings") or {}).get("realitySettings")) or {}
        tag = inb.get("tag")
        if tag and rs:
            out[tag] = inb
    return out


def build_reality_patch(
    marzban_config: dict, profile_config: dict
) -> tuple[dict, dict[str, Any]]:
    """Return (patched_profile_config, report). Copies Reality settings from each
    Marzban Reality inbound onto the same-tag Remnawave profile inbound. Existing
    inbounds are patched in place; nothing is added/removed."""
    mz = _reality_inbounds(marzban_config)
    patched = copy.deepcopy(profile_config or {})
    prof_by_tag = {i.get("tag"): i for i in (patched.get("inbounds") or [])}

    matched: list[str] = []
    unmatched: list[str] = []  # Reality tags with no Remnawave inbound
    for tag, mz_inb in mz.items():
        target = prof_by_tag.get(tag)
        if target is None:
            unmatched.append(tag)
            continue
        src_rs = (mz_inb.get("streamSettings") or {}).get("realitySettings") or {}
        ss = target.setdefault("streamSettings", {})
        # A tag matched a Marzban REALITY inbound → the target must be reality
        # (force it even if it was previously tls/none, else the inbound is
        # inconsistent: realitySettings present but security says tls).
        ss["security"] = "reality"
        dst_rs = ss.setdefault("realitySettings", {})
        for k in _REALITY_KEYS:
            if k in src_rs:
                dst_rs[k] = src_rs[k]
        matched.append(tag)

    return patched, {
        "matched": matched,
        "unmatched": unmatched,
        "reality_inbounds_in_source": list(mz.keys()),
    }


def legacy_secret_cmd() -> str:
    """Shell one-liner that prints Marzban's JWT secret_key (empty if unavailable).
    Tries the default SQLite DB, then a psql fallback — run over SSH SILENTLY."""
    return (
        "sqlite3 /var/lib/marzban/db.sqlite3 'SELECT secret_key FROM jwt LIMIT 1;' 2>/dev/null "
        "|| docker exec marzban-db psql -U marzban -t -A -c "
        "'SELECT secret_key FROM jwt LIMIT 1;' 2>/dev/null "
        "|| true"
    )
