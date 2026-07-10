"""
Pure evaluator for the rules engine (trigger → conditions → decision).

`evaluate(rule, event, now, state)` is a SIDE-EFFECT-FREE function: given a rule,
an event and the current time, it decides whether the rule should fire. It does
NOT execute actions and touches no network/store, so it is fully unit-testable.

Firing is gated, in order, by:
  1. `enabled` — a disabled rule never fires (default-disabled by design; actions
     can be destructive against a prod panel).
  2. trigger match — the event's `type` must match the rule's trigger type, plus
     trigger-specific matching (xray_down hysteresis, webhook event/scope filter).
  3. cooldown — `now - last_fired_at < cooldown_sec` suppresses re-fires (anti-flap
     debounce, on top of the xray_down N-minutes-consecutive hysteresis).
  4. conditions — an and/or chain over the event fields.

`state` carries runtime data the loop computed out-of-band (e.g. `down_seconds`
when the event itself doesn't embed it); the event takes precedence.
"""

from __future__ import annotations

from typing import Any, Optional


def evaluate(
    rule: dict, event: dict, now: int, state: Optional[dict] = None
) -> dict[str, Any]:
    """Return {should_fire, matched_actions, reason, dry_run}. Never raises."""
    state = state or {}
    if not rule.get("enabled", False):
        return _result(False, rule, "disabled")

    ok, reason = _trigger_matches(rule.get("trigger") or {}, event, state)
    if not ok:
        return _result(False, rule, reason)

    cooldown = int(rule.get("cooldown_sec", 0) or 0)
    if cooldown > 0:
        last = _last_fired_for(rule, event)
        if last is not None and (now - int(last)) < cooldown:
            return _result(False, rule, "cooldown")

    if not _conditions_match(rule.get("conditions") or [], event):
        return _result(False, rule, "conditions_unmet")

    return _result(True, rule, "matched")


def cooldown_scope(event: dict) -> str:
    """Cooldown bucket key for an event. xray_down is scoped PER-NODE so a down A
    can't suppress a down B during cooldown (and a per-node filter rule for a
    non-worst node still fires). webhook/cron share the global "" bucket."""
    if event.get("type") == "xray_down":
        return event.get("stableId") or event.get("node") or event.get("name") or ""
    return ""


def _last_fired_for(rule: dict, event: dict) -> Optional[int]:
    """Last-fired timestamp for this event's cooldown scope.

    - No per-scope `last_fired` map yet (legacy / never-fired rule) → the scalar
      `last_fired_at` governs EVERY scope (global cooldown, backward-compatible).
    - Map present → per-scope: this scope's own timestamp, or (for the global ""
      bucket) the scalar, else None (this node has never fired → no cooldown)."""
    scope = cooldown_scope(event)
    fired_map = rule.get("last_fired")
    if not isinstance(fired_map, dict):
        return rule.get("last_fired_at")
    if scope in fired_map:
        return fired_map[scope]
    if scope == "":
        return rule.get("last_fired_at")
    return None


def _result(fire: bool, rule: dict, reason: str) -> dict[str, Any]:
    return {
        "should_fire": fire,
        "matched_actions": (rule.get("actions") or []) if fire else [],
        "reason": reason,
        "dry_run": bool(rule.get("dry_run", False)),
    }


# ── trigger matching ──────────────────────────────────────────
def _trigger_matches(trigger: dict, event: dict, state: dict) -> tuple[bool, str]:
    ttype = trigger.get("type")
    if ttype != event.get("type"):
        return False, "trigger_mismatch"
    params = trigger.get("params") or {}

    if ttype == "xray_down":
        threshold = int(params.get("minutes", 5) or 0) * 60
        down = event.get("down_seconds")
        if down is None:
            down = state.get("down_seconds", 0)
        if int(down or 0) < threshold:
            return False, "hysteresis_not_met"
        node_filter = params.get("node") or params.get("stableId")
        if node_filter and node_filter not in (
            event.get("node", ""),
            event.get("stableId", ""),
            event.get("name", ""),
        ):
            return False, "node_mismatch"
        return True, "trigger_ok"

    if ttype == "webhook":
        want_event = (params.get("event") or "").strip()
        if want_event and want_event != event.get("event", ""):
            return False, "event_mismatch"
        want_scope = (params.get("scope") or "").strip()
        if want_scope and want_scope != event.get("scope", ""):
            return False, "scope_mismatch"
        return True, "trigger_ok"

    if ttype == "cron":
        # The loop already gates the schedule; the engine only enforces cooldown.
        return True, "trigger_ok"

    return False, "unknown_trigger"


# ── condition matching (and / or chain) ───────────────────────
def _conditions_match(conditions: list[dict], event: dict) -> bool:
    if not conditions:
        return True
    ctx = _flatten(event)
    result = _one_condition(conditions[0], ctx)
    for i in range(1, len(conditions)):
        # Each condition's `join` field says how it connects to the NEXT one.
        join = (conditions[i - 1].get("join") or "and").lower()
        nxt = _one_condition(conditions[i], ctx)
        result = (result or nxt) if join == "or" else (result and nxt)
    return result


def _flatten(event: dict) -> dict[str, Any]:
    """Field lookup namespace: top-level event keys + nested `data.*` keys, each
    reachable both bare (`nodeName`) and dotted (`data.nodeName`)."""
    ctx: dict[str, Any] = {}
    data = event.get("data")
    if isinstance(data, dict):
        for k, v in data.items():
            ctx[k] = v
            ctx[f"data.{k}"] = v
    for k, v in event.items():
        ctx[k] = v
    return ctx


def _one_condition(cond: dict, ctx: dict) -> bool:
    field = cond.get("field", "")
    op = (cond.get("op") or "eq").lower()
    expected = cond.get("value")
    actual = ctx.get(field)
    try:
        if op == "exists":
            return actual is not None
        if op == "eq":
            return _eq(actual, expected)
        if op == "ne":
            return not _eq(actual, expected)
        if op in ("gt", "gte", "lt", "lte"):
            a, b = float(actual), float(expected)
            return {
                "gt": a > b,
                "gte": a >= b,
                "lt": a < b,
                "lte": a <= b,
            }[op]
        if op == "contains":
            return str(expected) in str(actual)
        if op == "in":
            return actual in (expected or [])
    except (TypeError, ValueError):
        return False
    return False


def _eq(actual: Any, expected: Any) -> bool:
    if actual == expected:
        return True
    if actual is None or expected is None:
        return False
    return str(actual) == str(expected)
