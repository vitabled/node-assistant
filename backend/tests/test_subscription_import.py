"""Wave-7 Plan B Ф1 — parsing a subscription into monitor candidates."""
import base64
import json

from app.services import subscription_import as si

VLESS = "vless://11111111-2222-3333-4444-555555555555@node1.example.com:443?type=tcp&security=tls#%F0%9F%87%B3%F0%9F%87%B1%20Amsterdam"
TROJAN = "trojan://secretpw@node2.example.com:8443?security=tls#Германия-01"


def _vmess(host: str, port: int, ps: str) -> str:
    payload = {"v": "2", "ps": ps, "add": host, "port": port, "id": "1" * 8 + "-2222-3333-4444-555555555555",
               "aid": 0, "net": "ws", "type": "none", "host": "", "path": "/", "tls": "tls"}
    return "vmess://" + base64.b64encode(json.dumps(payload).encode()).decode()


# ── decoding ──────────────────────────────────────────────────
def test_decodes_a_base64_subscription():
    body = base64.b64encode(f"{VLESS}\n{TROJAN}\n".encode()).decode()
    assert si.decode_subscription(body) == [VLESS, TROJAN]


def test_decodes_a_plain_text_subscription():
    assert si.decode_subscription(f"{VLESS}\n\n{TROJAN}") == [VLESS, TROJAN]


def test_ignores_non_link_lines():
    assert si.decode_subscription(f"# comment\n{VLESS}\nnot a link") == [VLESS]


def test_empty_body_yields_nothing():
    assert si.decode_subscription("") == []
    assert si.decode_subscription("   \n  ") == []


# ── one link → candidate ──────────────────────────────────────
def test_vless_candidate():
    c = si.link_to_candidate(VLESS)
    assert c["host"] == "node1.example.com" and c["port"] == 443
    assert c["country"] == "NL"          # from the flag emoji in the fragment
    assert "Amsterdam" in c["name"]


def test_trojan_candidate_country_from_russian_name():
    c = si.link_to_candidate(TROJAN)
    assert c["host"] == "node2.example.com" and c["port"] == 8443
    assert c["country"] == "DE"


def test_vmess_candidate():
    c = si.link_to_candidate(_vmess("node3.example.com", 2053, "🇫🇮 Helsinki"))
    assert c["host"] == "node3.example.com" and c["port"] == 2053
    assert c["country"] == "FI"


def test_broken_link_returns_none_and_does_not_raise():
    assert si.link_to_candidate("vless://garbage") is None
    assert si.link_to_candidate("nonsense") is None
    assert si.link_to_candidate("") is None


def test_a_broken_link_does_not_poison_the_batch():
    links = si.decode_subscription(f"{VLESS}\nvless://garbage\n{TROJAN}")
    cands = [c for c in (si.link_to_candidate(l) for l in links) if c]
    assert [c["host"] for c in cands] == ["node1.example.com", "node2.example.com"]


# ── secrets ───────────────────────────────────────────────────
def test_errors_never_echo_the_link():
    """Links carry credentials (the trojan password IS the link). A parser that
    puts the input in its message would leak it into task logs and toasts."""
    from app.services.test_tools import parse_xray_link
    for bad in ("vless://garbage", "trojan://p@ss@nohost", "ss://zzz"):
        try:
            parse_xray_link(bad)
        except ValueError as exc:
            msg = str(exc)
            assert "garbage" not in msg and "p@ss" not in msg and "zzz" not in msg


# ── country guessing ──────────────────────────────────────────
def test_country_of():
    assert si.country_of("🇳🇱 Amsterdam") == "NL"
    assert si.country_of("Нидерланды-01") == "NL"
    assert si.country_of("NL premium") == "NL"
    assert si.country_of("Марс") == ""
    assert si.country_of("") == ""
