"""Wave-8 §5 — pure selector helpers for `remnawave.injectHosts`."""
import copy

from app.services import xray_selector as xs


def _cfg():
    return {
        "remnawave": {
            "injectHosts": [
                {"tagPrefix": "proxy", "selector": {"type": "uuids", "values": ["a"]}},
                {"tagPrefix": "backup", "selector": {"type": "uuids", "values": []}},
                # not a uuids-selector — ignored by the helpers
                {"tagPrefix": "bytag", "selector": {"type": "tags", "values": ["x"]}},
            ]
        }
    }


def test_list_uuid_groups():
    groups = xs.list_uuid_groups(_cfg())
    assert groups == [
        {"tag_prefix": "proxy", "count": 1},
        {"tag_prefix": "backup", "count": 0},
    ]


def test_list_groups_empty_on_garbage():
    assert xs.list_uuid_groups({}) == []
    assert xs.list_uuid_groups({"remnawave": {}}) == []
    assert xs.list_uuid_groups({"remnawave": {"injectHosts": "nope"}}) == []


def test_add_uuid_appends_and_preserves_order():
    cfg, changed = xs.add_uuid(_cfg(), "proxy", "b")
    assert changed is True
    assert cfg["remnawave"]["injectHosts"][0]["selector"]["values"] == ["a", "b"]


def test_add_uuid_is_deepcopy_no_mutation():
    src = _cfg()
    snapshot = copy.deepcopy(src)
    xs.add_uuid(src, "proxy", "b")
    assert src == snapshot  # original untouched


def test_add_uuid_dedup_idempotent():
    cfg, changed = xs.add_uuid(_cfg(), "proxy", "a")
    assert changed is False
    assert cfg["remnawave"]["injectHosts"][0]["selector"]["values"] == ["a"]


def test_add_uuid_group_not_found():
    cfg, changed = xs.add_uuid(_cfg(), "nope", "b")
    assert changed is False


def test_add_uuid_ignores_non_uuids_selector():
    cfg, changed = xs.add_uuid(_cfg(), "bytag", "b")
    assert changed is False


def test_add_uuid_empty_uuid_noop():
    _, changed = xs.add_uuid(_cfg(), "proxy", "")
    assert changed is False


def test_remove_uuid():
    cfg, changed = xs.remove_uuid(_cfg(), "proxy", "a")
    assert changed is True
    assert cfg["remnawave"]["injectHosts"][0]["selector"]["values"] == []
    # removing something absent → no change
    _, changed2 = xs.remove_uuid(_cfg(), "proxy", "zzz")
    assert changed2 is False


def test_remove_uuid_everywhere():
    cfg = {
        "remnawave": {"injectHosts": [
            {"tagPrefix": "p1", "selector": {"type": "uuids", "values": ["h", "x"]}},
            {"tagPrefix": "p2", "selector": {"type": "uuids", "values": ["h"]}},
            {"tagPrefix": "p3", "selector": {"type": "uuids", "values": ["y"]}},
        ]}
    }
    out, changed = xs.remove_uuid_everywhere(cfg, "h")
    assert changed is True
    groups = out["remnawave"]["injectHosts"]
    assert groups[0]["selector"]["values"] == ["x"]
    assert groups[1]["selector"]["values"] == []
    assert groups[2]["selector"]["values"] == ["y"]
    # absent uuid → no change
    _, changed2 = xs.remove_uuid_everywhere(cfg, "absent")
    assert changed2 is False
