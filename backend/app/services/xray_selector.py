"""Pure helpers for Remnawave xray-json `remnawave.injectHosts` selectors (Wave-8 §5).

A config profile's raw xray `config` may carry a `remnawave.injectHosts` list, each
entry a "balancer group": `{ tagPrefix, selector: { type: "uuids", values: [...] } }`.
We append/remove a HOST uuid to a chosen group's `selector.values` so the host joins
that balancer's outbound pool (order of values = order of outbounds).

All functions deepcopy their input and never raise: a missing group / malformed
config simply reports "not changed". This replaces the earlier `$hostid` template
variable idea — nothing here substitutes a variable; it mutates the real profile.
"""
from __future__ import annotations

import copy


def _inject_hosts(config: dict) -> list:
    rw = (config or {}).get("remnawave")
    if not isinstance(rw, dict):
        return []
    groups = rw.get("injectHosts")
    return groups if isinstance(groups, list) else []


def list_uuid_groups(config: dict) -> list[dict]:
    """Every injectHosts group whose selector is a uuids-selector:
    `[{tag_prefix, count}]`."""
    out: list[dict] = []
    for g in _inject_hosts(config or {}):
        if not isinstance(g, dict):
            continue
        sel = g.get("selector")
        if isinstance(sel, dict) and sel.get("type") == "uuids":
            vals = sel.get("values")
            out.append({
                "tag_prefix": str(g.get("tagPrefix") or ""),
                "count": len(vals) if isinstance(vals, list) else 0,
            })
    return out


def _find_group(config: dict, tag_prefix: str):
    for g in _inject_hosts(config):
        if isinstance(g, dict) and str(g.get("tagPrefix") or "") == tag_prefix:
            sel = g.get("selector")
            if isinstance(sel, dict) and sel.get("type") == "uuids":
                return g
    return None


def add_uuid(config: dict, tag_prefix: str, uuid: str) -> tuple[dict, bool]:
    """Append `uuid` to the group's `selector.values` (dedup, order preserved).
    Returns (new_config, changed). Group not found / empty uuid → (copy, False)."""
    cfg = copy.deepcopy(config or {})
    if not uuid:
        return cfg, False
    g = _find_group(cfg, tag_prefix)
    if g is None:
        return cfg, False
    sel = g["selector"]
    vals = sel.get("values")
    if not isinstance(vals, list):
        vals = []
    if uuid in vals:
        sel["values"] = vals
        return cfg, False
    vals.append(uuid)
    sel["values"] = vals
    return cfg, True


def remove_uuid(config: dict, tag_prefix: str, uuid: str) -> tuple[dict, bool]:
    """Drop `uuid` from ONE group's selector. Returns (new_config, changed)."""
    cfg = copy.deepcopy(config or {})
    if not uuid:
        return cfg, False
    g = _find_group(cfg, tag_prefix)
    if g is None:
        return cfg, False
    sel = g["selector"]
    vals = sel.get("values")
    if not isinstance(vals, list) or uuid not in vals:
        return cfg, False
    sel["values"] = [v for v in vals if v != uuid]
    return cfg, True


def remove_uuid_everywhere(config: dict, uuid: str) -> tuple[dict, bool]:
    """Strip `uuid` from EVERY uuids-selector group in the profile — the deletion
    safety net (selector.values is the accumulated host list)."""
    cfg = copy.deepcopy(config or {})
    if not uuid:
        return cfg, False
    changed = False
    for g in _inject_hosts(cfg):
        if not isinstance(g, dict):
            continue
        sel = g.get("selector")
        if isinstance(sel, dict) and sel.get("type") == "uuids":
            vals = sel.get("values")
            if isinstance(vals, list) and uuid in vals:
                sel["values"] = [v for v in vals if v != uuid]
                changed = True
    return cfg, changed
