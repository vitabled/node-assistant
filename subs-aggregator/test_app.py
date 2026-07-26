"""Unit tests for the subs-aggregator tagging/decoding (stdlib-only, no deps).
Run: python -m pytest subs-aggregator/test_app.py  (or: python subs-aggregator/test_app.py)
"""
import base64
import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(__file__))
import app  # noqa: E402


def test_tag_vless_appends_account_sub_to_fragment():
    line = "vless://uuid@host:443?type=tcp#MyNode"
    out = app._tag_config(line, "acc1:sub1")
    base, _, frag = out.partition("#")
    assert base == "vless://uuid@host:443?type=tcp"
    assert urllib.parse.unquote(frag) == "acc1:sub1|MyNode"


def test_tag_no_fragment_uses_tag_as_remark():
    out = app._tag_config("trojan://pw@host:443", "acc1:sub1")
    assert out.endswith("#acc1%3Asub1")  # ":" percent-encoded in the fragment


def test_tag_vmess_rewrites_ps_field():
    obj = {"v": "2", "ps": "Orig", "add": "host", "port": "443", "id": "uuid"}
    line = "vmess://" + base64.b64encode(json.dumps(obj).encode()).decode()
    out = app._tag_config(line, "acc1:sub1")
    assert out.startswith("vmess://")
    dec = json.loads(base64.b64decode(out[len("vmess://"):] + "==").decode())
    assert dec["ps"] == "acc1:sub1|Orig"


def test_tag_malformed_vmess_passes_through():
    line = "vmess://not-base64!!!"
    assert app._tag_config(line, "t") == line


def test_decode_base64_subscription_body():
    raw = "vless://a@h:443#N1\nvless://b@h:443#N2"
    b64 = base64.b64encode(raw.encode())
    lines = app._decode_sub_body(b64)
    assert lines == ["vless://a@h:443#N1", "vless://b@h:443#N2"]


def test_decode_plaintext_subscription_body():
    raw = b"vless://a@h:443#N1\n\nvless://b@h:443#N2\n"
    assert app._decode_sub_body(raw) == ["vless://a@h:443#N1", "vless://b@h:443#N2"]


def test_decode_empty_body():
    assert app._decode_sub_body(b"") == []


# ── SSRF guard (_safe_fetch) ──────────────────────────────────

def test_safe_fetch_rejects_non_http_scheme():
    for bad in ["file:///etc/passwd", "ftp://host/x", "gopher://h/", "//h/x"]:
        try:
            app._safe_fetch(bad)
            assert False, f"should reject {bad}"
        except ValueError:
            pass


def test_safe_fetch_rejects_private_and_metadata_hosts():
    # _host_is_public must reject loopback / link-local (IMDS) / private ranges
    saved = app._ALLOW_PRIVATE
    app._ALLOW_PRIVATE = False
    try:
        assert app._host_is_public("127.0.0.1") is False
        assert app._host_is_public("169.254.169.254") is False   # cloud IMDS
        assert app._host_is_public("10.0.0.5") is False
        assert app._host_is_public("192.168.1.1") is False
        assert app._host_is_public("::1") is False
    finally:
        app._ALLOW_PRIVATE = saved


# ── UA fallback chain (Wave-8) ────────────────────────────────

def test_has_link_lines():
    assert app._has_link_lines(["vless://a@h:443#n"]) is True
    assert app._has_link_lines(["VMESS://xxx"]) is True          # case-insensitive
    assert app._has_link_lines(['[{"outbounds": []}]']) is False  # JSON config, no links
    assert app._has_link_lines([]) is False


def test_fetch_sub_lines_ua_fallback():
    # default UA returns a JSON config (no share links) → fall through to Happ.
    calls = []

    def fake(url, timeout=app.FETCH_TIMEOUT, user_agent="subs-aggregator/1"):
        calls.append(user_agent)
        if user_agent == "subs-aggregator/1":
            return b'[{"outbounds":[]}]'                          # JSON, no links
        return base64.b64encode(b"vless://a@h:443#N1\nvless://b@h:443#N2")

    saved = app._safe_fetch
    app._safe_fetch = fake
    try:
        lines = app._fetch_sub_lines("https://panel/x")
    finally:
        app._safe_fetch = saved
    assert calls[0] == "subs-aggregator/1" and calls[1] == "Happ/1.16.0"
    assert lines == ["vless://a@h:443#N1", "vless://b@h:443#N2"]


def test_fetch_sub_lines_stops_at_first_ua_with_links():
    calls = []

    def fake(url, timeout=app.FETCH_TIMEOUT, user_agent="subs-aggregator/1"):
        calls.append(user_agent)
        return base64.b64encode(b"vless://a@h:443#N1")

    saved = app._safe_fetch
    app._safe_fetch = fake
    try:
        lines = app._fetch_sub_lines("https://panel/x")
    finally:
        app._safe_fetch = saved
    assert calls == ["subs-aggregator/1"]                        # no client UA tried
    assert lines == ["vless://a@h:443#N1"]


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"  FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
