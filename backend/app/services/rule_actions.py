"""
Rule action executors.

`execute_actions(actions, context, account_id, dry_run)` runs each action of a
fired rule and returns a per-action result list (the "plan" when dry_run). It is:
  - idempotent — hosts/nodes/users are only toggled toward the desired state
    (already-in-state entries are skipped), and Remnawave's action endpoints are
    themselves idempotent;
  - fail-soft — an unreachable Remnawave/Telegram is logged and recorded as an
    error result; it never raises (a webhook/loop tick must not die on it);
  - secret-safe — the Telegram bot-token is read from the Fernet vault by ref and
    never logged (see services/telegram.redact); dry_run results carry NO token.

Placeholders in a telegram `text` (`$hostname`, `$node`, `$event`, `$stableId`,
`$group`, `$account_id`, …) are substituted from `context`.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from app.models.settings import AppSettings
from app.services import rules_store, storage, telegram
from app.services.remnawave_client import RemnavaveClient

log = logging.getLogger("rules.actions")

# Remnawave node/user uuids are interpolated into request URL paths; only allow a
# canonical UUID shape so a hostile params/context value can't traverse the path.
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# Actions that talk to Remnawave (grouped so we build one client per invocation).
_RW_ACTIONS = {
    "hide_hosts",
    "show_hosts",
    "node_disable",
    "node_enable",
    "user_disable",
    "user_enable",
}


def _render(text: str, context: dict) -> str:
    """Substitute `$key` placeholders from context (longest keys first so a key
    that's a prefix of another, e.g. $node vs $node_id, isn't mangled)."""
    out = text or ""
    for key in sorted(context, key=len, reverse=True):
        out = out.replace(f"${key}", str(context.get(key, "")))
    return out


def _rw_client(account_id: str) -> Optional[RemnavaveClient]:
    """Build a Remnawave client from the account's stored settings, or None when
    the account hasn't configured a panel (background caller — no request creds)."""
    try:
        cfg = AppSettings(**storage.load_settings(account_id)).remnawave
    except Exception:
        return None
    if not cfg.panel_url or not cfg.api_token:
        return None
    return RemnavaveClient(cfg.panel_url, cfg.api_token)


async def execute_actions(
    actions: list[dict], context: dict, account_id: str, dry_run: bool = False
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    client: Optional[RemnavaveClient] = None
    if not dry_run and any(a.get("type") in _RW_ACTIONS for a in actions):
        client = _rw_client(account_id)

    for action in actions:
        atype = action.get("type", "")
        params = action.get("params") or {}
        if dry_run:
            results.append(_plan(atype, params, context))
            continue
        try:
            if atype == "telegram":
                res = await _do_telegram(params, context, account_id)
            elif atype in _RW_ACTIONS:
                res = await _do_remnawave(atype, params, context, client)
            else:
                res = {"ok": False, "detail": f"unknown action '{atype}'"}
        except Exception as exc:  # fail-soft: log + record, never propagate
            log.warning(
                "rules.action.error type=%s: %s", atype, telegram.redact(str(exc))
            )
            res = {"ok": False, "detail": telegram.redact(str(exc))[:200]}
        results.append({"type": atype, "executed": True, "dry_run": False, **res})
    return results


# ── planning (dry-run) ────────────────────────────────────────
def _plan(atype: str, params: dict, context: dict) -> dict[str, Any]:
    """A masked, side-effect-free description of what WOULD run. No secrets."""
    safe = {k: v for k, v in params.items() if k not in ("bot_token", "token_ref")}
    if atype == "telegram":
        safe["text"] = _render(params.get("text", ""), context)
        safe["bot_token"] = rules_store.MASK if params.get("token_ref") else ""
    return {"type": atype, "executed": False, "dry_run": True, "ok": True, "plan": safe}


# ── telegram ──────────────────────────────────────────────────
async def _do_telegram(params: dict, context: dict, account_id: str) -> dict[str, Any]:
    ref = params.get("token_ref", "")
    token = await rules_store.a_read_secret(ref, account_id) if ref else ""
    if not token:
        return {"ok": False, "detail": "bot-token not set (token_ref missing)"}
    chat_id = str(params.get("chat_id", "")).strip()
    text = _render(params.get("text", ""), context)
    res = await telegram.send_message(token, chat_id, text)
    return {"ok": bool(res.get("ok")), "detail": res.get("error", "")}


# ── remnawave (hosts / nodes / users) ─────────────────────────
async def _do_remnawave(
    atype: str, params: dict, context: dict, client: Optional[RemnavaveClient]
) -> dict[str, Any]:
    if client is None:
        return {"ok": False, "detail": "Remnawave не настроен для аккаунта"}

    if atype in ("hide_hosts", "show_hosts"):
        return await _toggle_hosts(atype == "hide_hosts", params, context, client)

    # node/user enable/disable — uuid from params, falling back to context.
    if atype in ("node_disable", "node_enable"):
        uuid = (
            params.get("node_uuid")
            or context.get("node_uuid")
            or context.get("nodeUuid")
        )
        if not uuid:
            return {"ok": False, "detail": "node_uuid не указан"}
        if not _valid_uuid(uuid):
            return {"ok": False, "detail": "node_uuid имеет неверный формат"}
        if atype == "node_disable":
            await client.disable_node(uuid)
        else:
            await client.enable_node(uuid)
        return {"ok": True, "detail": uuid}

    if atype in ("user_disable", "user_enable"):
        uuid = (
            params.get("user_uuid")
            or context.get("user_uuid")
            or context.get("userUuid")
        )
        if not uuid:
            return {"ok": False, "detail": "user_uuid не указан"}
        if not _valid_uuid(uuid):
            return {"ok": False, "detail": "user_uuid имеет неверный формат"}
        if atype == "user_disable":
            await client.disable_user(uuid)
        else:
            await client.enable_user(uuid)
        return {"ok": True, "detail": uuid}

    return {"ok": False, "detail": f"unknown action '{atype}'"}


def _valid_uuid(value: Any) -> bool:
    return isinstance(value, str) and bool(_UUID_RE.match(value))


async def _toggle_hosts(
    disable: bool, params: dict, context: dict, client: RemnavaveClient
) -> dict[str, Any]:
    """Bulk hide/show hosts selected by explicit uuids, by node, or by
    config-profile. A selector is REQUIRED — without one we refuse rather than
    silently toggling EVERY host (a misconfigured rule must not black-hole the
    whole panel). Idempotent: only hosts NOT already in the desired state are
    toggled, so re-runs (e.g. connection_lost fired twice) are no-ops."""
    want_uuids = set(params.get("host_uuids") or [])
    node_uuid = (
        params.get("node_uuid") or context.get("node_uuid") or context.get("nodeUuid")
    )
    profile_uuid = params.get("config_profile_uuid")

    if not (want_uuids or node_uuid or profile_uuid):
        return {
            "ok": False,
            "detail": (
                "не задан селектор хостов "
                "(host_uuids / node_uuid / config_profile_uuid) — "
                "операция над всеми хостами запрещена"
            ),
            "affected": 0,
        }

    hosts = await client.list_hosts()
    selected = []
    for h in hosts:
        uuid = h.get("uuid")
        if not uuid:
            continue
        if want_uuids:
            match = uuid in want_uuids
        elif node_uuid:
            match = node_uuid in (h.get("nodes") or [])
        else:
            match = (h.get("inbound") or {}).get("configProfileUuid") == profile_uuid
        if not match:
            continue
        # Idempotence: skip hosts already in the target state.
        already = bool(h.get("isDisabled"))
        if already == disable:
            continue
        selected.append(uuid)

    if not selected:
        return {
            "ok": True,
            "detail": "нет хостов для изменения (идемпотентно)",
            "affected": 0,
        }
    if disable:
        await client.bulk_disable_hosts(selected)
    else:
        await client.bulk_enable_hosts(selected)
    return {"ok": True, "detail": f"{len(selected)} хостов", "affected": len(selected)}
