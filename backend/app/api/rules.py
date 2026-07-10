"""
Rules engine API + background loop.

Two routers:
- `router` (/api/rules, account-gated) — CRUD + a dry-run `test` endpoint.
- `webhook_router` (/api/webhooks, NOT account-gated) — the Remnawave webhook
  receiver. Its capability is a valid HMAC signature (a browser can't forge one
  without the shared secret), so it stays outside `require_account` — same posture
  as the WS log stream.

Trigger taxonomy: `xray_down` (a node down ≥ N min in xray-checker, with hysteresis
+ cooldown) and `cron` are driven by `rules_loop` (a lifespan task, per-account,
explicit account_id — the pattern from xray_checker.poller_loop). `webhook` is
driven by the receiver, which runs matching rules across ALL accounts on a
verified event.

Secret handling: a telegram action's plaintext `bot_token` is moved into the
Fernet vault on write (→ `token_ref`) and NEVER stored in rules.json; responses
mask it as `••••`. `token_ref` (opaque, not the secret) IS returned so an edit can
re-send it to keep the existing token.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.config import settings
from app.services import (
    accounts,
    metrics_store,
    rule_actions,
    rule_engine,
    rules_store,
    telegram,
)
from app.api.xray_checker import _filter_by_account

router = APIRouter(prefix="/api/rules")
webhook_router = APIRouter(prefix="/api/webhooks")
log = logging.getLogger("rules")

_TICK = 60  # rules_loop cadence (seconds)

_TRIGGER_TYPES = ("xray_down", "webhook", "cron")
_ACTION_TYPES = (
    "telegram",
    "hide_hosts",
    "show_hosts",
    "node_disable",
    "node_enable",
    "user_disable",
    "user_enable",
)
_OPS = ("eq", "ne", "gt", "gte", "lt", "lte", "contains", "in", "exists")
_JOINS = ("and", "or")


# ── request models ────────────────────────────────────────────
class Trigger(BaseModel):
    type: str
    params: dict = {}

    @field_validator("type")
    @classmethod
    def _t(cls, v: str) -> str:
        if v not in _TRIGGER_TYPES:
            raise ValueError(f"trigger.type должен быть одним из {_TRIGGER_TYPES}")
        return v


class Condition(BaseModel):
    field: str
    op: str = "eq"
    value: Any = None
    join: str = "and"

    @field_validator("op")
    @classmethod
    def _op(cls, v: str) -> str:
        if v not in _OPS:
            raise ValueError(f"op должен быть одним из {_OPS}")
        return v

    @field_validator("join")
    @classmethod
    def _join(cls, v: str) -> str:
        if v not in _JOINS:
            raise ValueError("join должен быть 'and' или 'or'")
        return v


class Action(BaseModel):
    type: str
    params: dict = {}

    @field_validator("type")
    @classmethod
    def _t(cls, v: str) -> str:
        if v not in _ACTION_TYPES:
            raise ValueError(f"action.type должен быть одним из {_ACTION_TYPES}")
        return v


class RuleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    enabled: bool = False
    trigger: Trigger
    conditions: list[Condition] = []
    actions: list[Action] = []
    cooldown_sec: int = Field(300, ge=0, le=86400)
    dry_run: bool = False


class RuleUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    enabled: Optional[bool] = None
    trigger: Optional[Trigger] = None
    conditions: Optional[list[Condition]] = None
    actions: Optional[list[Action]] = None
    cooldown_sec: Optional[int] = Field(default=None, ge=0, le=86400)
    dry_run: Optional[bool] = None


# ── secret ingest / masking ───────────────────────────────────
def _ingest_actions(actions: list[dict], account_id: str) -> list[dict]:
    """Move plaintext telegram bot-tokens into the vault → token_ref; never keep
    plaintext in rules.json. An action echoing MASK/omitting bot_token keeps its
    existing token_ref (edit without changing the token)."""
    out = []
    for a in actions:
        a = {**a, "params": {**(a.get("params") or {})}}
        if a.get("type") == "telegram":
            p = a["params"]
            tok = p.pop("bot_token", None)
            if tok and tok != rules_store.MASK:
                old = p.get("token_ref")
                p["token_ref"] = rules_store.put_secret(tok, account_id)
                if old and old != p["token_ref"]:
                    rules_store.delete_secret(old, account_id)
        out.append(a)
    return out


def _public_rule(rule: dict) -> dict:
    """Mask secrets for API responses (never expose the plaintext bot-token)."""
    r = {**rule, "actions": []}
    for a in rule.get("actions") or []:
        a = {**a, "params": {**(a.get("params") or {})}}
        if a.get("type") == "telegram" and a["params"].get("token_ref"):
            a["params"]["bot_token"] = rules_store.MASK
        r["actions"].append(a)
    return r


# ── CRUD ──────────────────────────────────────────────────────
@router.get("")
async def list_rules_ep() -> list[dict]:
    return [_public_rule(r) for r in rules_store.list_rules()]


@router.post("", status_code=201)
async def create_rule(body: RuleCreate) -> dict:
    aid = accounts.current_account.get() or ""
    data = body.model_dump()
    data["actions"] = _ingest_actions(data["actions"], aid)
    return _public_rule(rules_store.add_rule(data))


@router.patch("/{rule_id}")
async def patch_rule(rule_id: str, body: RuleUpdate) -> dict:
    aid = accounts.current_account.get() or ""
    if not rules_store.get_rule(rule_id):
        raise HTTPException(404, "Правило не найдено")
    patch = body.model_dump(exclude_unset=True)
    if "actions" in patch:
        patch["actions"] = _ingest_actions(patch["actions"], aid)
    return _public_rule(rules_store.update_rule(rule_id, patch))


@router.delete("/{rule_id}", status_code=204)
async def delete_rule(rule_id: str) -> None:
    if not rules_store.remove_rule(rule_id):
        raise HTTPException(404, "Правило не найдено")


async def _dry_run(rule: dict, aid: str) -> dict:
    """Evaluate a rule against a fixture event (trigger-forced, cooldown cleared)
    and return the would-run action plan. Pure dry-run: nothing is sent/changed,
    and the plan masks secrets (see rule_actions._plan)."""
    now = int(time.time())
    event = _fixture_event(rule)
    probe = {**rule, "enabled": True, "last_fired_at": None, "last_fired": {}}
    res = rule_engine.evaluate(probe, event, now, {})
    ctx = _context(event, aid)
    plan = await rule_actions.execute_actions(
        rule.get("actions") or [], ctx, aid, dry_run=True
    )
    return {
        "event": event,
        "evaluation": {k: res[k] for k in ("should_fire", "reason", "dry_run")},
        "plan": plan,
    }


@router.post("/test")
async def test_rule_draft(body: RuleCreate) -> dict:
    """Dry-run a rule DRAFT (an unsaved rule body) — nothing is persisted and no
    secret is vaulted. This lets the UI preview a new/edited rule without creating
    an orphan rule + orphan vault secret on cancel."""
    aid = accounts.current_account.get() or ""
    return await _dry_run(body.model_dump(), aid)


@router.post("/{rule_id}/test")
async def test_rule(rule_id: str) -> dict:
    """Dry-run a PERSISTED rule by id (nothing is sent/changed)."""
    aid = accounts.current_account.get() or ""
    rule = rules_store.get_rule(rule_id)
    if not rule:
        raise HTTPException(404, "Правило не найдено")
    return await _dry_run(rule, aid)


# ── webhook receiver (ungated, HMAC-verified) ─────────────────
@webhook_router.post("/remnawave")
async def remnawave_webhook(
    request: Request,
    x_remnawave_signature: str = Header(default=""),
    x_remnawave_timestamp: str = Header(default=""),
) -> dict:
    """HMAC-SHA256 verified receiver for Remnawave webhooks. On a valid signature,
    runs `webhook`-triggered rules for EVERY account (the webhook is global; each
    account only has rules if it opted in). Invalid/absent signature → 401."""
    secret = settings.webhook_secret_header
    body = await request.body()
    if not secret or not _verify_hmac(secret, body, x_remnawave_signature):
        raise HTTPException(401, "invalid signature")
    try:
        payload = json.loads(body.decode() or "{}")
    except Exception:
        raise HTTPException(400, "invalid json")
    # Anti-replay: reject a signed body whose (HMAC-covered) timestamp is stale/future.
    # The X-Remnawave-Timestamp header is NOT signed, so we key off the body field.
    if not _replay_fresh(payload.get("timestamp"), int(time.time())):
        raise HTTPException(401, "stale timestamp")
    event = {
        "type": "webhook",
        "event": payload.get("event", ""),
        "scope": payload.get("scope", ""),
        "timestamp": payload.get("timestamp"),
        "data": payload.get("data") or {},
    }
    fired = await _run_webhook_event(event)
    return {"ok": True, "matched": fired}


def _verify_hmac(secret: str, body: bytes, signature: str) -> bool:
    if not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    sig = signature.split("=", 1)[1] if signature.startswith("sha256=") else signature
    return hmac.compare_digest(expected, sig)


_REPLAY_WINDOW = 300  # seconds either side of now


def _replay_fresh(ts: Any, now: int) -> bool:
    """True if the signed-body timestamp is within ±_REPLAY_WINDOW of now, OR is
    absent/unparseable (fail-open — the HMAC already authenticates; the timestamp
    is defense-in-depth against replay of a captured request). Accepts epoch
    seconds or milliseconds (numeric or numeric-string)."""
    if ts is None:
        return True
    try:
        val = float(ts)
    except (TypeError, ValueError):
        return True  # non-numeric (e.g. ISO string) — don't reject on format
    if val > 1e12:  # milliseconds
        val /= 1000.0
    return abs(now - val) <= _REPLAY_WINDOW


# ── event / context helpers ───────────────────────────────────
def _context(event: dict, account_id: str) -> dict[str, Any]:
    """Placeholder namespace for action text + uuid fallbacks."""
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    node = event.get("node") or data.get("nodeName") or ""
    ctx: dict[str, Any] = {
        "event": event.get("event") or event.get("type") or "",
        "node": node,
        "hostname": node,
        "stableId": event.get("stableId", ""),
        "group": event.get("group", ""),
        "account_id": account_id,
    }
    for k, v in data.items():
        ctx.setdefault(k, v)
    ctx.setdefault("node_uuid", data.get("nodeUuid", ""))
    ctx.setdefault("user_uuid", data.get("userUuid", ""))
    return ctx


def _fixture_event(rule: dict) -> dict:
    """A representative event for the dry-run test endpoint."""
    trig = rule.get("trigger") or {}
    params = trig.get("params") or {}
    ttype = trig.get("type")
    if ttype == "xray_down":
        minutes = int(params.get("minutes", 5) or 5)
        node = params.get("node") or "node-1"
        return {
            "type": "xray_down",
            "node": node,
            "name": node,
            "stableId": params.get("stableId", "s1"),
            "down_seconds": (minutes + 1) * 60,
            "checker_id": params.get("checker_id", metrics_store.LOCAL_CHECKER_ID),
        }
    if ttype == "webhook":
        return {
            "type": "webhook",
            "event": params.get("event", "node.connection_lost"),
            "scope": params.get("scope", "node"),
            "data": {"nodeName": "node-1"},
        }
    return {"type": "cron"}


async def _xray_down_events(rule: dict, account_id: str) -> list[dict]:
    """One xray_down event PER node with an ONGOING down incident ≥ N min (the
    incident's consecutive-offline duration IS the hysteresis signal). Returns a
    list — every down node gets its own event so a rule with a per-node filter
    still matches, and per-node cooldown keeps them independent (previously only
    the single worst node was surfaced per tick)."""
    params = (rule.get("trigger") or {}).get("params") or {}
    minutes = int(params.get("minutes", 5) or 5)
    checker_id = params.get("checker_id") or metrics_store.LOCAL_CHECKER_ID
    try:
        incidents = await metrics_store.get_incidents(2, checker_id)
    except Exception:
        return []
    incidents = _filter_by_account(incidents, account_id)  # per-account tag scope
    events = []
    for i in incidents:
        if not i.get("ongoing"):
            continue
        if int(i.get("durationSec", 0) or 0) < minutes * 60:
            continue
        events.append(
            {
                "type": "xray_down",
                "node": i.get("name", ""),
                "name": i.get("name", ""),
                "stableId": i.get("stableId", ""),
                "group": i.get("group", ""),
                "down_seconds": int(i.get("durationSec", 0) or 0),
                "checker_id": checker_id,
            }
        )
    return events


def _cron_due(rule: dict, now: int) -> bool:
    params = (rule.get("trigger") or {}).get("params") or {}
    interval = int(
        params.get("interval_sec") or (int(params.get("minutes", 0) or 0) * 60) or 0
    )
    if interval <= 0:
        return False
    last = rule.get("last_fired_at")
    return last is None or (now - int(last)) >= interval


# ── firing (loop + webhook) ───────────────────────────────────
async def _fire(rule: dict, event: dict, account_id: str, now: int) -> bool:
    res = rule_engine.evaluate(rule, event, now, {})
    if not res["should_fire"]:
        return False
    ctx = _context(event, account_id)
    await rule_actions.execute_actions(
        res["matched_actions"], ctx, account_id, rule.get("dry_run", False)
    )
    if not rule.get("dry_run"):
        scope = rule_engine.cooldown_scope(event)
        rules_store.mark_fired(rule["id"], scope, now, account_id)
    return True


async def _run_account_scheduled(account_id: str, now: int) -> None:
    for rule in rules_store.list_rules(account_id):
        if not rule.get("enabled"):
            continue
        ttype = (rule.get("trigger") or {}).get("type")
        if ttype == "xray_down":
            events = await _xray_down_events(rule, account_id)
        elif ttype == "cron":
            if not _cron_due(rule, now):
                continue
            events = [{"type": "cron"}]
        else:
            continue  # webhook rules are driven by the receiver
        for event in events:
            try:
                await _fire(rule, event, account_id, now)
            except Exception as exc:
                log.warning(
                    "rules.fire_failed rule=%s: %s",
                    rule.get("id"),
                    telegram.redact(str(exc))[:200],
                )


async def _run_webhook_event(event: dict) -> int:
    """Run webhook-triggered rules across ALL accounts on a verified event."""
    now = int(time.time())
    fired = 0
    for acc in accounts.list_accounts():
        aid = acc["id"]
        try:
            rules = rules_store.list_rules(aid)
        except Exception:
            continue
        for rule in rules:
            if (
                not rule.get("enabled")
                or (rule.get("trigger") or {}).get("type") != "webhook"
            ):
                continue
            try:
                if await _fire(rule, event, aid, now):
                    fired += 1
            except Exception as exc:
                log.warning(
                    "rules.webhook_fire_failed rule=%s: %s",
                    rule.get("id"),
                    telegram.redact(str(exc))[:200],
                )
    return fired


async def rules_loop() -> None:
    """Lifespan task: every ~60s, run xray_down/cron rules for each account.
    Per-account try/except + a top-level guard so one bad account/tick never kills
    the loop (mirrors xray_checker.poller_loop). No request context → explicit
    account_id everywhere."""
    while True:
        now = int(time.time())
        try:
            for acc in accounts.list_accounts():
                try:
                    await _run_account_scheduled(acc["id"], now)
                except Exception as exc:
                    log.warning(
                        "rules.account_failed account=%s: %s",
                        acc.get("id"),
                        telegram.redact(str(exc))[:200],
                    )
        except Exception:
            pass
        await asyncio.sleep(_TICK)
