"""Unit tests for the PURE rule evaluator (services/rule_engine.evaluate).

No network, no store — just the trigger/condition/cooldown/hysteresis decision.
"""

import time

from app.services import rule_engine


def _rule(**over):
    r = {
        "id": "r1",
        "name": "test",
        "enabled": True,
        "trigger": {"type": "xray_down", "params": {"minutes": 5}},
        "conditions": [],
        "actions": [{"type": "telegram", "params": {"chat_id": "1", "text": "down"}}],
        "cooldown_sec": 300,
        "dry_run": False,
        "last_fired_at": None,
    }
    r.update(over)
    return r


NOW = 1_000_000


def _xray_event(down_seconds, **extra):
    e = {
        "type": "xray_down",
        "node": "de-1",
        "stableId": "s1",
        "down_seconds": down_seconds,
    }
    e.update(extra)
    return e


# ── hysteresis (N minutes consecutive down) ───────────────────
def test_xray_down_6min_fires():
    res = rule_engine.evaluate(_rule(), _xray_event(6 * 60), NOW, {})
    assert res["should_fire"] is True
    assert res["reason"] == "matched"
    assert res["matched_actions"] == _rule()["actions"]


def test_xray_down_3min_does_not_fire():
    res = rule_engine.evaluate(_rule(), _xray_event(3 * 60), NOW, {})
    assert res["should_fire"] is False
    assert res["reason"] == "hysteresis_not_met"


def test_xray_down_seconds_from_state_when_absent_in_event():
    ev = {"type": "xray_down", "node": "de-1"}
    res = rule_engine.evaluate(_rule(), ev, NOW, {"down_seconds": 6 * 60})
    assert res["should_fire"] is True


# ── cooldown ──────────────────────────────────────────────────
def test_cooldown_blocks_recent_fire():
    r = _rule(last_fired_at=NOW - 100)  # cooldown 300 > 100 elapsed
    res = rule_engine.evaluate(r, _xray_event(6 * 60), NOW, {})
    assert res["should_fire"] is False
    assert res["reason"] == "cooldown"


def test_cooldown_expired_allows_fire():
    r = _rule(last_fired_at=NOW - 400)  # 400 > cooldown 300
    res = rule_engine.evaluate(r, _xray_event(6 * 60), NOW, {})
    assert res["should_fire"] is True


# ── conditions (and / or) ─────────────────────────────────────
def _cond(field, op, value, join="and"):
    return {"field": field, "op": op, "value": value, "join": join}


def test_conditions_and_all_true_fires():
    r = _rule(
        conditions=[
            _cond("node", "eq", "de-1", "and"),
            _cond("down_seconds", "gte", 300),
        ]
    )
    assert rule_engine.evaluate(r, _xray_event(6 * 60), NOW, {})["should_fire"] is True


def test_conditions_and_one_false_blocks():
    r = _rule(
        conditions=[_cond("node", "eq", "de-1", "and"), _cond("node", "eq", "fr-2")]
    )
    res = rule_engine.evaluate(r, _xray_event(6 * 60), NOW, {})
    assert res["should_fire"] is False
    assert res["reason"] == "conditions_unmet"


def test_conditions_or_one_true_fires():
    r = _rule(
        conditions=[_cond("node", "eq", "wrong", "or"), _cond("node", "eq", "de-1")]
    )
    assert rule_engine.evaluate(r, _xray_event(6 * 60), NOW, {})["should_fire"] is True


def test_empty_conditions_fires_on_trigger():
    r = _rule(conditions=[])
    assert rule_engine.evaluate(r, _xray_event(6 * 60), NOW, {})["should_fire"] is True


# ── disabled / dry_run ────────────────────────────────────────
def test_disabled_rule_never_fires():
    r = _rule(enabled=False)
    res = rule_engine.evaluate(r, _xray_event(6 * 60), NOW, {})
    assert res["should_fire"] is False
    assert res["reason"] == "disabled"


def test_dry_run_still_decides_to_fire_but_flags_dry_run():
    r = _rule(dry_run=True)
    res = rule_engine.evaluate(r, _xray_event(6 * 60), NOW, {})
    assert res["should_fire"] is True
    assert res["dry_run"] is True


# ── node filter on the trigger ────────────────────────────────
def test_node_filter_mismatch_blocks():
    r = _rule(trigger={"type": "xray_down", "params": {"minutes": 5, "node": "fr-9"}})
    res = rule_engine.evaluate(r, _xray_event(6 * 60), NOW, {})
    assert res["should_fire"] is False
    assert res["reason"] == "node_mismatch"


def test_node_filter_match_fires():
    r = _rule(trigger={"type": "xray_down", "params": {"minutes": 5, "node": "de-1"}})
    assert rule_engine.evaluate(r, _xray_event(6 * 60), NOW, {})["should_fire"] is True


# ── webhook trigger ───────────────────────────────────────────
def _webhook_rule(**over):
    return _rule(
        trigger={"type": "webhook", "params": {"event": "node.connection_lost"}},
        **over,
    )


def test_webhook_event_match_fires():
    ev = {
        "type": "webhook",
        "event": "node.connection_lost",
        "scope": "node",
        "data": {"nodeName": "de-1"},
    }
    assert rule_engine.evaluate(_webhook_rule(), ev, NOW, {})["should_fire"] is True


def test_webhook_event_mismatch_blocks():
    ev = {"type": "webhook", "event": "node.connection_restored", "scope": "node"}
    res = rule_engine.evaluate(_webhook_rule(), ev, NOW, {})
    assert res["should_fire"] is False
    assert res["reason"] == "event_mismatch"


def test_trigger_type_mismatch_blocks():
    # A cron event should not fire an xray_down rule.
    res = rule_engine.evaluate(_rule(), {"type": "cron"}, NOW, {})
    assert res["should_fire"] is False
    assert res["reason"] == "trigger_mismatch"


def test_webhook_condition_reads_nested_data():
    ev = {
        "type": "webhook",
        "event": "node.connection_lost",
        "scope": "node",
        "data": {"nodeName": "de-1"},
    }
    r = _webhook_rule(conditions=[_cond("nodeName", "eq", "de-1")])
    assert rule_engine.evaluate(r, ev, NOW, {})["should_fire"] is True
    r2 = _webhook_rule(conditions=[_cond("nodeName", "eq", "other")])
    assert rule_engine.evaluate(r2, ev, NOW, {})["should_fire"] is False


def test_now_used_for_cooldown_math():
    # Sanity: a real timestamp cooldown works with the wall clock too.
    now = int(time.time())
    r = _rule(last_fired_at=now - 10, cooldown_sec=60)
    assert rule_engine.evaluate(r, _xray_event(6 * 60), now, {})["should_fire"] is False


# ── per-node (per-scope) cooldown ─────────────────────────────
def test_cooldown_scope_is_per_node_for_xray_down():
    assert rule_engine.cooldown_scope(_xray_event(6 * 60, stableId="s1")) == "s1"
    # falls back to node name when stableId absent
    ev = {"type": "xray_down", "node": "de-1"}
    assert rule_engine.cooldown_scope(ev) == "de-1"


def test_cooldown_scope_is_global_for_webhook_and_cron():
    assert rule_engine.cooldown_scope({"type": "webhook", "event": "x"}) == ""
    assert rule_engine.cooldown_scope({"type": "cron"}) == ""


def test_per_node_cooldown_blocks_same_node_only():
    # Node s1 recently fired; s2 has never fired → s1 blocked, s2 still fires.
    r = _rule(last_fired={"s1": NOW - 100})  # cooldown 300 > 100 elapsed
    blocked = rule_engine.evaluate(r, _xray_event(6 * 60, stableId="s1"), NOW, {})
    assert blocked["should_fire"] is False and blocked["reason"] == "cooldown"
    fresh = rule_engine.evaluate(r, _xray_event(6 * 60, stableId="s2"), NOW, {})
    assert fresh["should_fire"] is True


def test_per_node_map_takes_precedence_over_legacy_scalar():
    # A per-node entry for s1 governs s1 even if the legacy scalar says otherwise.
    r = _rule(last_fired={"s1": NOW - 100}, last_fired_at=NOW - 9999)
    assert (
        rule_engine.evaluate(r, _xray_event(6 * 60, stableId="s1"), NOW, {})[
            "should_fire"
        ]
        is False
    )


# ── operator coverage ─────────────────────────────────────────
def _op_rule(field, op, value):
    return _rule(conditions=[_cond(field, op, value)])


def test_operator_contains():
    ev = _xray_event(6 * 60, node="de-frankfurt-1")
    assert rule_engine.evaluate(_op_rule("node", "contains", "frank"), ev, NOW, {})[
        "should_fire"
    ]
    assert not rule_engine.evaluate(_op_rule("node", "contains", "paris"), ev, NOW, {})[
        "should_fire"
    ]


def test_operator_in():
    ev = _xray_event(6 * 60, node="de-1")
    assert rule_engine.evaluate(_op_rule("node", "in", ["de-1", "fr-2"]), ev, NOW, {})[
        "should_fire"
    ]
    assert not rule_engine.evaluate(_op_rule("node", "in", ["fr-2"]), ev, NOW, {})[
        "should_fire"
    ]


def test_operator_exists_and_ne():
    ev = _xray_event(6 * 60)
    assert rule_engine.evaluate(_op_rule("node", "exists", None), ev, NOW, {})[
        "should_fire"
    ]
    assert not rule_engine.evaluate(_op_rule("missing", "exists", None), ev, NOW, {})[
        "should_fire"
    ]
    assert rule_engine.evaluate(_op_rule("node", "ne", "fr-2"), ev, NOW, {})[
        "should_fire"
    ]


def test_operator_numeric_lt_gt_lte():
    ev = _xray_event(6 * 60)  # down_seconds = 360
    assert rule_engine.evaluate(_op_rule("down_seconds", "gt", 300), ev, NOW, {})[
        "should_fire"
    ]
    assert rule_engine.evaluate(_op_rule("down_seconds", "lte", 360), ev, NOW, {})[
        "should_fire"
    ]
    assert not rule_engine.evaluate(_op_rule("down_seconds", "lt", 100), ev, NOW, {})[
        "should_fire"
    ]


def test_operator_numeric_on_non_numeric_is_false_not_raise():
    ev = _xray_event(6 * 60, node="de-1")
    # gt against a non-numeric field must NOT raise, just fail the condition.
    res = rule_engine.evaluate(_op_rule("node", "gt", 5), ev, NOW, {})
    assert res["should_fire"] is False
