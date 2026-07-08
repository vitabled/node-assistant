"""Ф1 (wave1) — test_tools: xray-link parser + bash script generators.

The parser turns a vless/trojan/vmess/ss share-link into a full xray-client
config (socks inbound 127.0.0.1:10808 + the parsed outbound). Links can carry
credentials — error messages/scripts must never contain the raw link.
"""

import base64
import json

import pytest

from app.services import test_tools as tt

UUID = "b831381d-6324-4d53-ad4f-8cda48b30811"


def _assert_inbound(cfg: dict) -> None:
    assert cfg["log"] == {"loglevel": "warning"}
    inb = cfg["inbounds"][0]
    assert inb["listen"] == "127.0.0.1"
    assert inb["port"] == 10808
    assert inb["protocol"] == "socks"
    assert inb["settings"]["udp"] is True
    assert len(cfg["outbounds"]) == 1


# ── vless ─────────────────────────────────────────────────────


def test_vless_reality_full():
    link = (
        f"vless://{UUID}@example.com:443"
        "?type=tcp&security=reality&sni=cdn.example.com"
        "&pbk=PUBKEY123abc&sid=6ba85179&fp=chrome&flow=xtls-rprx-vision#MyNode"
    )
    cfg = tt.parse_xray_link(link)
    _assert_inbound(cfg)
    ob = cfg["outbounds"][0]
    assert ob["protocol"] == "vless"
    vnext = ob["settings"]["vnext"][0]
    assert vnext["address"] == "example.com"
    assert vnext["port"] == 443
    user = vnext["users"][0]
    assert user["id"] == UUID
    assert user["encryption"] == "none"
    assert user["flow"] == "xtls-rprx-vision"
    ss = ob["streamSettings"]
    assert ss["network"] == "tcp"
    assert ss["security"] == "reality"
    rs = ss["realitySettings"]
    assert rs["serverName"] == "cdn.example.com"
    assert rs["publicKey"] == "PUBKEY123abc"
    assert rs["shortId"] == "6ba85179"
    assert rs["fingerprint"] == "chrome"


def test_vless_ws_tls():
    link = (
        f"vless://{UUID}@1.2.3.4:8443"
        "?type=ws&security=tls&sni=w.example.com&path=%2Fws&host=cdn.host.com#W"
    )
    cfg = tt.parse_xray_link(link)
    ob = cfg["outbounds"][0]
    ss = ob["streamSettings"]
    assert ss["network"] == "ws"
    assert ss["security"] == "tls"
    assert ss["tlsSettings"]["serverName"] == "w.example.com"
    assert ss["wsSettings"]["path"] == "/ws"
    assert ss["wsSettings"]["headers"]["Host"] == "cdn.host.com"


def test_vless_grpc():
    link = f"vless://{UUID}@1.2.3.4:2053?type=grpc&security=none&serviceName=grpcsvc#G"
    ss = tt.parse_xray_link(link)["outbounds"][0]["streamSettings"]
    assert ss["network"] == "grpc"
    assert ss["grpcSettings"]["serviceName"] == "grpcsvc"


def test_vless_xhttp():
    link = f"vless://{UUID}@1.2.3.4:443?type=xhttp&security=tls&sni=x.example.com&path=%2Fxh#X"
    ss = tt.parse_xray_link(link)["outbounds"][0]["streamSettings"]
    assert ss["network"] == "xhttp"
    assert ss["xhttpSettings"]["path"] == "/xh"


# ── trojan ────────────────────────────────────────────────────


def test_trojan():
    link = "trojan://s3cretPW@5.5.5.5:443?security=tls&sni=tj.example.com&type=tcp#TJ"
    cfg = tt.parse_xray_link(link)
    _assert_inbound(cfg)
    ob = cfg["outbounds"][0]
    assert ob["protocol"] == "trojan"
    srv = ob["settings"]["servers"][0]
    assert srv["address"] == "5.5.5.5"
    assert srv["port"] == 443
    assert srv["password"] == "s3cretPW"
    assert ob["streamSettings"]["security"] == "tls"
    assert ob["streamSettings"]["tlsSettings"]["serverName"] == "tj.example.com"


# ── vmess ─────────────────────────────────────────────────────


def test_vmess_base64_json():
    vm = {
        "v": "2",
        "ps": "node",
        "add": "6.6.6.6",
        "port": "443",
        "id": UUID,
        "aid": "0",
        "net": "ws",
        "type": "none",
        "host": "vm.host.com",
        "path": "/vm",
        "tls": "tls",
        "sni": "vm.sni.com",
    }
    link = "vmess://" + base64.b64encode(json.dumps(vm).encode()).decode()
    cfg = tt.parse_xray_link(link)
    _assert_inbound(cfg)
    ob = cfg["outbounds"][0]
    assert ob["protocol"] == "vmess"
    vnext = ob["settings"]["vnext"][0]
    assert vnext["address"] == "6.6.6.6"
    assert vnext["port"] == 443
    assert vnext["users"][0]["id"] == UUID
    ss = ob["streamSettings"]
    assert ss["network"] == "ws"
    assert ss["security"] == "tls"
    assert ss["tlsSettings"]["serverName"] == "vm.sni.com"
    assert ss["wsSettings"]["path"] == "/vm"
    assert ss["wsSettings"]["headers"]["Host"] == "vm.host.com"


# ── ss (both forms) ───────────────────────────────────────────


def test_ss_userinfo_base64():
    link = (
        "ss://"
        + base64.b64encode(b"aes-256-gcm:ssPassw0rd").decode()
        + "@7.7.7.7:8388#S1"
    )
    cfg = tt.parse_xray_link(link)
    _assert_inbound(cfg)
    ob = cfg["outbounds"][0]
    assert ob["protocol"] == "shadowsocks"
    srv = ob["settings"]["servers"][0]
    assert srv["address"] == "7.7.7.7"
    assert srv["port"] == 8388
    assert srv["method"] == "aes-256-gcm"
    assert srv["password"] == "ssPassw0rd"


def test_ss_fully_base64():
    link = (
        "ss://"
        + base64.b64encode(b"chacha20-ietf-poly1305:pw2@8.8.4.4:9000").decode()
        + "#S2"
    )
    srv = tt.parse_xray_link(link)["outbounds"][0]["settings"]["servers"][0]
    assert srv["address"] == "8.8.4.4"
    assert srv["port"] == 9000
    assert srv["method"] == "chacha20-ietf-poly1305"
    assert srv["password"] == "pw2"


# ── invalid input (ValueError, no link leak) ──────────────────


def test_invalid_scheme_raises():
    for bad in ("http://example.com", "socks5://1.2.3.4:1080", "", "garbage"):
        with pytest.raises(ValueError):
            tt.parse_xray_link(bad)


def test_garbage_payload_raises_without_leaking():
    for bad in (
        "vmess://%%%notbase64",
        "vless://no-host-here",
        f"vless://{UUID}@host:notaport?type=tcp",
        "ss://!!!",
        "trojan://@:443",
    ):
        with pytest.raises(ValueError) as ei:
            tt.parse_xray_link(bad)
        # error text must never contain link fragments (creds may ride in them)
        assert "notbase64" not in str(ei.value)
        assert "notaport" not in str(ei.value)


# ── script generators ─────────────────────────────────────────


def test_install_script_essentials():
    s = tt.test_tools_install_script()
    assert "iperf3" in s
    assert "packagecloud.io/install/repositories/ookla/speedtest-cli" in s
    assert "speedtest-cli" in s  # python fallback
    assert "Xray-linux-64.zip" in s and "/usr/local/bin" in s
    assert "[warn]" in s  # non-fatal branches
    assert "exit 1" not in s


def test_speedtest_run_script_markers_and_fallback():
    s = tt.speedtest_run_script()
    assert "SPEEDTEST_JSON_START" in s and "SPEEDTEST_JSON_END" in s
    assert "SPEEDTEST_KIND=ookla" in s and "SPEEDTEST_KIND=python" in s
    assert "SPEEDTEST_NONE" in s  # neither CLI installed → marker, not an error
    assert "--accept-license" in s and "--accept-gdpr" in s and "-f json" in s
    assert "speedtest-cli --json" in s
    assert "exit 1" not in s  # never fatal


def test_iperf_server_script():
    s = tt.iperf_server_script(5999)
    assert "iperf3-server.service" in s
    assert "iperf3 -s -p 5999" in s
    assert "daemon-reload" in s and "enable --now" in s


def test_iperf_client_script_quotes_host():
    hostile = "1.2.3.4; rm -rf /"
    s = tt.iperf_client_script(hostile, 5201, with_ping=True, with_traceroute=True)
    assert "IPERF_JSON_START" in s and "IPERF_JSON_END" in s
    assert "PING_START" in s and "PING_END" in s
    assert "TRACEROUTE_START" in s and "TRACEROUTE_END" in s
    assert f"iperf3 -c '{hostile}' -p 5201 -J -t 10" in s  # shlex-quoted
    assert "; rm -rf /'" in s and "4; rm -rf / " not in s


def test_iperf_client_script_optional_sections_off():
    s = tt.iperf_client_script("9.9.9.9", 5201, with_ping=False, with_traceroute=False)
    assert "PING_START" not in s and "TRACEROUTE_START" not in s
    assert "IPERF_JSON_START" in s


def test_xray_speedtest_script():
    link = (
        f"vless://{UUID}@example.com:443"
        "?type=tcp&security=reality&sni=cdn.example.com&pbk=PUBKEY123abc&sid=6ba85179#N"
    )
    s = tt.xray_link_speedtest_script(link)
    assert link not in s  # raw link never in the script
    assert "XRAYCFG" in s  # heredoc-written config
    assert "trap" in s and "rm -f" in s  # cleanup always runs
    assert "XRAY_DOWN=" in s and "XRAY_UP=" in s and "XRAY_PING=" in s
    assert "127.0.0.1:10808" in s
    assert "speed.cloudflare.com/__down" in s and "speed.cloudflare.com/__up" in s


def test_xray_speedtest_script_rejects_bad_link():
    with pytest.raises(ValueError):
        tt.xray_link_speedtest_script("http://nope")
