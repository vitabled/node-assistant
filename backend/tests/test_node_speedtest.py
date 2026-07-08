"""Ф2 (wave1) — node-speedtest probes: output parsers + POST /api/stats/node-speedtest.

Parsers are exercised on fixture outputs with the Ф1/Ф2 markers (pattern of
test_stats.py); the endpoint runs against a mocked SSHSession. The xray link
must never leak into an error detail (links can carry credentials).
"""

import sys
import types
import uuid

import pytest
from fastapi.testclient import TestClient

# Stub asyncssh before importing anything that pulls in ssh_manager.
sys.modules.setdefault("asyncssh", types.ModuleType("asyncssh"))

import app.api.stats as stats  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)

UUID = "b831381d-6324-4d53-ad4f-8cda48b30811"
XRAY_LINK = f"vless://{UUID}@example.com:443?type=tcp&security=none#N"


def _auth():
    r = client.post(
        "/api/auth/register",
        json={"login": f"spt-{uuid.uuid4().hex[:8]}", "password": "pw"},
    )
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ── fixtures (marker-delimited probe outputs) ─────────────────

OOKLA_OUT = """\
SPEEDTEST_KIND=ookla
SPEEDTEST_JSON_START
{"type":"result","ping":{"jitter":0.5,"latency":9.4},"download":{"bandwidth":117500000},"upload":{"bandwidth":11750000}}
SPEEDTEST_JSON_END
"""

PYTHON_OUT = """\
SPEEDTEST_KIND=python
SPEEDTEST_JSON_START
{"download": 940000000.0, "upload": 94000000.0, "ping": 9.4}
SPEEDTEST_JSON_END
"""

IPERF_OUT = """\
IPERF_JSON_START
{"start": {}, "end": {"sum_sent": {"bits_per_second": 950000000.0}, "sum_received": {"bits_per_second": 941200000.0}}}
IPERF_JSON_END
PING_START
10 packets transmitted, 10 received, 0% packet loss, time 9012ms
rtt min/avg/max/mdev = 11.123/12.345/14.567/0.890 ms
PING_END
TRACEROUTE_START
 1  10.0.0.1  0.512 ms
 2  185.1.2.3  1.204 ms
TRACEROUTE_END
"""

XRAY_OUT = """\
[xray] Замер download...
XRAY_DOWN=12500000.000
XRAY_UP=6250000.000
XRAY_PING=0.045
"""

CHAR_OUT = """\
CHAR_NPROC=4
CHAR_MODEL=Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz
CHAR_RAM_MB=7936
CHAR_DISK=40G 12G 32%
"""


# ── parsers ───────────────────────────────────────────────────


def test_parse_speedtest_ookla_bytes_per_second():
    res, warn = stats._parse_speedtest(OOKLA_OUT)
    assert warn is None
    assert res["st_down"] == pytest.approx(940.0)  # bytes/s * 8 / 1e6
    assert res["st_up"] == pytest.approx(94.0)
    assert res["st_ping"] == pytest.approx(9.4)


def test_parse_speedtest_python_bits_per_second():
    res, warn = stats._parse_speedtest(PYTHON_OUT)
    assert warn is None
    assert res["st_down"] == pytest.approx(940.0)  # bits/s / 1e6
    assert res["st_up"] == pytest.approx(94.0)
    assert res["st_ping"] == pytest.approx(9.4)


def test_parse_speedtest_none_and_garbage_degrade_to_warning():
    res, warn = stats._parse_speedtest("SPEEDTEST_NONE\n")
    assert res is None and warn
    res, warn = stats._parse_speedtest(
        "SPEEDTEST_JSON_START\nnot json\nSPEEDTEST_JSON_END"
    )
    assert res is None and warn
    res, warn = stats._parse_speedtest("")
    assert res is None and warn


def test_parse_iperf_mbps():
    mbps, warn = stats._parse_iperf(IPERF_OUT)
    assert warn is None
    assert mbps == pytest.approx(941.2)


def test_parse_iperf_error_and_garbage():
    out = 'IPERF_JSON_START\n{"error": "unable to connect to server"}\nIPERF_JSON_END'
    mbps, warn = stats._parse_iperf(out)
    assert mbps is None and "unable to connect" in warn
    mbps, warn = stats._parse_iperf("no markers at all")
    assert mbps is None and warn


def test_parse_ping_avg_and_mdev():
    avg, mdev = stats._parse_ping(IPERF_OUT)
    assert avg == pytest.approx(12.345)
    assert mdev == pytest.approx(0.890)
    assert stats._parse_ping("PING_START\ngarbage\nPING_END") == (None, None)
    assert stats._parse_ping("") == (None, None)


def test_parse_traceroute_raw_text():
    txt = stats._parse_traceroute(IPERF_OUT)
    assert "10.0.0.1" in txt and "185.1.2.3" in txt
    assert stats._parse_traceroute("nothing") is None


def test_parse_xray_curl_bytes_and_seconds():
    res, warn = stats._parse_xray(XRAY_OUT)
    assert warn is None
    assert res["xray_down"] == pytest.approx(100.0)  # bytes/s * 8 / 1e6
    assert res["xray_up"] == pytest.approx(50.0)
    assert res["xray_ping"] == pytest.approx(45.0)  # seconds → ms


def test_parse_xray_tunnel_down_degrades():
    res, warn = stats._parse_xray("[warn] туннель не поднялся за 15 секунд\n")
    assert res is None and warn
    res, warn = stats._parse_xray("XRAY_DOWN=0\nXRAY_UP=0\nXRAY_PING=0\n")
    assert res is None and warn


def test_parse_characteristics():
    cpu, ram_mb, disk = stats._parse_characteristics(CHAR_OUT)
    assert cpu == "4 × Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz"
    assert ram_mb == 7936
    assert "40G" in disk and "12G" in disk and "32%" in disk
    assert stats._parse_characteristics("") == (None, None, None)


# ── endpoint (SSH mocked) ─────────────────────────────────────


class FakeSSH:
    """Keys canned outputs off the command/script content; records commands."""

    installed = True  # `command -v iperf3` answers non-empty
    fail_speedtest = False
    commands: list = []

    def __init__(self, *a, **k):
        pass

    async def connect(self, *a, **k):
        pass

    async def get_output(self, command: str) -> str:
        FakeSSH.commands.append(command)
        if "command -v iperf3" in command and "SPEEDTEST" not in command:
            return "/usr/bin/iperf3" if FakeSSH.installed else ""
        if "[test-tools]" in command:  # lazy installer
            FakeSSH.installed = True
            return "ok"
        if "CHAR_NPROC" in command:
            return CHAR_OUT
        if "SPEEDTEST_JSON_START" in command:
            return "SPEEDTEST_NONE" if FakeSSH.fail_speedtest else OOKLA_OUT
        if "IPERF_JSON_START" in command:
            return IPERF_OUT
        if "XRAY_DOWN" in command:
            return XRAY_OUT
        return ""

    async def get_script_output(self, script: str, timeout=None) -> str:
        # Benchmark scripts are piped over stdin (not argv) — route to the same
        # content-keyed canned outputs.
        return await self.get_output(script)

    async def close(self):
        pass


@pytest.fixture()
def fake_ssh(monkeypatch):
    FakeSSH.installed = True
    FakeSSH.fail_speedtest = False
    FakeSSH.commands = []
    monkeypatch.setattr(stats, "SSHSession", FakeSSH)
    return FakeSSH


def _body(**over):
    base = dict(ip="1.2.3.4", ssh_user="root", ssh_password="pw", ssh_port=22)
    base.update(over)
    return base


def _add_testserver(headers) -> str:
    r = client.post(
        "/api/testservers",
        headers=headers,
        json={"name": "ts1", "ip": "5.5.5.5", "iperf_port": 5201},
    )
    assert r.status_code == 200
    return r.json()["id"]


def test_endpoint_requires_auth():
    assert client.post("/api/stats/node-speedtest", json=_body()).status_code == 401


def test_full_run_metrics_123(fake_ssh):
    h = _auth()
    ts_id = _add_testserver(h)
    r = client.post(
        "/api/stats/node-speedtest",
        headers=h,
        json=_body(testserver_id=ts_id, xray_link=XRAY_LINK, metrics=[1, 2, 3]),
    )
    assert r.status_code == 200
    body = r.json()
    cur = body["current"]
    assert cur["cpu"].startswith("4 × Intel")
    assert cur["ram_mb"] == 7936
    assert "40G" in cur["disk"]
    assert cur["st_down"] == pytest.approx(940.0)
    assert cur["iperf_mbps"] == pytest.approx(941.2)
    assert cur["ping_ms"] == pytest.approx(12.345)
    assert cur["iperf_jitter"] == pytest.approx(0.890)
    assert "10.0.0.1" in cur["traceroute"]
    assert cur["xray_down"] == pytest.approx(100.0)
    assert cur["xray_ping"] == pytest.approx(45.0)
    assert body["warnings"] == []
    assert len(body["history"]) >= 1
    # iperf client was pointed at the registered test server
    assert any("5.5.5.5" in c for c in FakeSSH.commands if "iperf3 -c" in c)

    # history endpoint returns the recorded run without SSH
    r2 = client.get(
        "/api/stats/node-speedtest/history",
        headers=h,
        params={"resource_key": "1.2.3.4", "limit": 20},
    )
    assert r2.status_code == 200
    assert r2.json()["history"][0]["iperf_mbps"] == pytest.approx(941.2)


def test_metrics_1_only_skips_ping_traceroute(fake_ssh):
    h = _auth()
    ts_id = _add_testserver(h)
    r = client.post(
        "/api/stats/node-speedtest",
        headers=h,
        json=_body(testserver_id=ts_id, metrics=[1]),
    )
    assert r.status_code == 200
    iperf_cmds = [c for c in FakeSSH.commands if "iperf3 -c" in c]
    assert iperf_cmds and all(
        "PING_START" not in c and "TRACEROUTE_START" not in c for c in iperf_cmds
    )


def test_probe_failure_yields_warning_not_500(fake_ssh):
    FakeSSH.fail_speedtest = True
    h = _auth()
    r = client.post("/api/stats/node-speedtest", headers=h, json=_body())
    assert r.status_code == 200
    body = r.json()
    assert body["current"]["st_down"] is None
    assert body["warnings"]  # the failed probe is reported
    assert body["current"]["cpu"]  # other probes still succeeded


def test_foreign_testserver_404(fake_ssh):
    h1, h2 = _auth(), _auth()
    ts_id = _add_testserver(h1)
    # another account must not see h1's test server
    r = client.post(
        "/api/stats/node-speedtest", headers=h2, json=_body(testserver_id=ts_id)
    )
    assert r.status_code == 404
    r = client.post(
        "/api/stats/node-speedtest", headers=h1, json=_body(testserver_id="nope")
    )
    assert r.status_code == 404


def test_bad_xray_link_422_without_leak(fake_ssh):
    h = _auth()
    secret = "http://secret-user:secret-pass@evil.example"
    r = client.post(
        "/api/stats/node-speedtest", headers=h, json=_body(xray_link=secret)
    )
    assert r.status_code == 422
    assert "secret-" not in r.text  # the link never leaks into the detail


def test_lazy_install_when_tools_missing(fake_ssh):
    FakeSSH.installed = False
    h = _auth()
    r = client.post("/api/stats/node-speedtest", headers=h, json=_body())
    assert r.status_code == 200
    assert any("[test-tools]" in c for c in FakeSSH.commands)  # installer ran


def test_no_lazy_install_when_tools_present(fake_ssh):
    h = _auth()
    r = client.post("/api/stats/node-speedtest", headers=h, json=_body())
    assert r.status_code == 200
    assert not any("[test-tools]" in c for c in FakeSSH.commands)


def test_ssh_unreachable_502(monkeypatch):
    class DeadSSH(FakeSSH):
        async def connect(self, *a, **k):
            raise OSError("connection refused")

    monkeypatch.setattr(stats, "SSHSession", DeadSSH)
    r = client.post("/api/stats/node-speedtest", headers=_auth(), json=_body())
    assert r.status_code == 502


def test_concurrent_run_on_same_node_409(fake_ssh):
    # A run already in flight for this (account, ip) → reject the second with 409
    # (a speedtest saturates the node's uplink for minutes).
    from app.services import accounts as _acc

    h = _auth()
    aid = _acc.account_id_from_token(h["Authorization"].split()[1])
    stats._INFLIGHT.add((aid, "1.2.3.4"))
    try:
        r = client.post("/api/stats/node-speedtest", headers=h, json=_body())
        assert r.status_code == 409
    finally:
        stats._INFLIGHT.discard((aid, "1.2.3.4"))
    # once released, the same node runs fine
    assert (
        client.post("/api/stats/node-speedtest", headers=h, json=_body()).status_code
        == 200
    )
