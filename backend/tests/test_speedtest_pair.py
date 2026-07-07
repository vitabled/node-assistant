"""Ф2b (wave1) — «Тесты скорости»: POST /api/speedtest/{pair,xray} + history.

Runs against a mocked SSHSession that keys canned outputs off script content and
records every script/connect. Focus: full pair run (metrics 1/2/3), the
ALWAYS-run cleanup of the ephemeral iperf3 server + UFW rule (even when the A
side fails), A==B rejection, B=testserver (no ephemeral server), the xray link
never leaking into a 422, and pair/xray history + account isolation.
"""

import sys
import types
import uuid

import pytest
from fastapi.testclient import TestClient

# Stub asyncssh before importing anything that pulls in ssh_manager.
sys.modules.setdefault("asyncssh", types.ModuleType("asyncssh"))

import app.api.speedtest as speedtest  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)

UUID = "b831381d-6324-4d53-ad4f-8cda48b30811"
XRAY_LINK = f"vless://{UUID}@example.com:443?type=tcp&security=none#N"

A_IP = "1.1.1.1"
B_IP = "2.2.2.2"
TS_IP = "5.5.5.5"


def _auth():
    r = client.post(
        "/api/auth/register",
        json={"login": f"sp-{uuid.uuid4().hex[:8]}", "password": "pw"},
    )
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ── fixture probe outputs (Ф1/Ф2 markers) ─────────────────────

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


# ── mocked SSHSession ─────────────────────────────────────────


class FakeSSH:
    """One class, many instances (A and B). Routes by host (connect failures)
    and by script content (canned outputs); records scripts + cleanup calls."""

    dead_hosts: set = set()
    fail_client = False  # A-side client script raises mid-run
    port_busy = False  # B has no free port → server-up prints IPERF_PORT=0
    scripts: list = []
    cleanup_calls: list = []
    connects: list = []

    def __init__(self, host, port=22, username="root", password=""):
        self.host = host

    async def connect(self, *a, **k):
        FakeSSH.connects.append(self.host)
        if self.host in FakeSSH.dead_hosts:
            raise OSError("connection refused")

    async def get_output(self, command: str) -> str:
        if "/usr/local/bin/xray" in command:
            return "yes"  # xray present → skip lazy install in the xray endpoint
        return ""

    async def get_script_output(self, script: str, timeout=None) -> str:
        FakeSSH.scripts.append(script)
        if "IPERF_CLEANUP_DONE" in script:  # must precede the iperf3 -s checks
            FakeSSH.cleanup_calls.append(script)
            return "IPERF_CLEANUP_DONE"
        if "iperf3 -s -p" in script and "IPERF_PORT" in script:
            return "IPERF_PORT=0" if FakeSSH.port_busy else "IPERF_PORT=5201"
        if "iperf3 -c" in script:
            if FakeSSH.fail_client:
                raise RuntimeError("client boom")
            return IPERF_OUT
        if "XRAY_DOWN" in script:
            return XRAY_OUT
        return ""

    async def close(self):
        pass


@pytest.fixture()
def fake_ssh(monkeypatch):
    FakeSSH.dead_hosts = set()
    FakeSSH.fail_client = False
    FakeSSH.port_busy = False
    FakeSSH.scripts = []
    FakeSSH.cleanup_calls = []
    FakeSSH.connects = []
    monkeypatch.setattr(speedtest, "SSHSession", FakeSSH)
    return FakeSSH


def _node(ip):
    return {
        "kind": "node",
        "ip": ip,
        "ssh_user": "root",
        "ssh_password": "pw",
        "ssh_port": 22,
    }


def _testserver(ip, port=5201):
    return {"kind": "testserver", "ip": ip, "iperf_port": port}


# ── auth / validation ─────────────────────────────────────────


def test_pair_requires_auth():
    r = client.post("/api/speedtest/pair", json={"a": _node(A_IP), "b": _node(B_IP)})
    assert r.status_code == 401


def test_pair_same_ip_400(fake_ssh):
    r = client.post(
        "/api/speedtest/pair",
        headers=_auth(),
        json={"a": _node(A_IP), "b": _node(A_IP)},
    )
    assert r.status_code == 400


def test_pair_missing_creds_400(fake_ssh):
    h = _auth()
    a = _node(A_IP)
    a["ssh_password"] = ""
    r = client.post("/api/speedtest/pair", headers=h, json={"a": a, "b": _node(B_IP)})
    assert r.status_code == 400


# ── full pair run + cleanup ───────────────────────────────────


def test_pair_full_metrics_123_with_cleanup(fake_ssh):
    h = _auth()
    r = client.post(
        "/api/speedtest/pair",
        headers=h,
        json={"a": _node(A_IP), "b": _node(B_IP), "metrics": [1, 2, 3]},
    )
    assert r.status_code == 200
    body = r.json()
    cur = body["current"]
    assert cur["kind"] == "pair"
    assert cur["resource_key"] == f"{A_IP}→{B_IP}"
    assert cur["iperf_mbps"] == pytest.approx(941.2)
    assert cur["ping_ms"] == pytest.approx(12.345)
    assert cur["iperf_jitter"] == pytest.approx(0.890)
    assert "10.0.0.1" in cur["traceroute"]
    assert body["warnings"] == []
    # client targeted B on the ephemeral port
    assert any("iperf3 -c 2.2.2.2 -p 5201" in s for s in FakeSSH.scripts)
    # ephemeral server + UFW rule were torn down (cleanup ran with a ufw delete)
    assert FakeSSH.cleanup_calls
    assert any("ufw delete allow from" in s for s in FakeSSH.cleanup_calls)
    assert len(body["history"]) >= 1


def test_pair_metrics_1_only_skips_ping_traceroute(fake_ssh):
    h = _auth()
    r = client.post(
        "/api/speedtest/pair",
        headers=h,
        json={"a": _node(A_IP), "b": _node(B_IP), "metrics": [1]},
    )
    assert r.status_code == 200
    client_scripts = [s for s in FakeSSH.scripts if "iperf3 -c" in s]
    assert client_scripts and all(
        "PING_START" not in s and "TRACEROUTE_START" not in s for s in client_scripts
    )


def test_pair_cleanup_runs_when_client_side_fails(fake_ssh):
    """A-side client raises mid-run → 200 + warning, but the ephemeral server and
    its UFW rule are STILL torn down in the finally block."""
    FakeSSH.fail_client = True
    h = _auth()
    r = client.post(
        "/api/speedtest/pair", headers=h, json={"a": _node(A_IP), "b": _node(B_IP)}
    )
    assert r.status_code == 200
    assert r.json()["warnings"]  # the A-side failure is surfaced
    assert FakeSSH.cleanup_calls  # cleanup still ran
    assert any("ufw delete allow from" in s for s in FakeSSH.cleanup_calls)


def test_pair_cleanup_runs_when_client_side_unreachable(fake_ssh):
    """A unreachable (connect fails) but B's ephemeral server is already up →
    cleanup must still tear it down."""
    FakeSSH.dead_hosts = {A_IP}
    h = _auth()
    r = client.post(
        "/api/speedtest/pair", headers=h, json={"a": _node(A_IP), "b": _node(B_IP)}
    )
    assert r.status_code == 200
    assert any("Сторона A недоступна" in w for w in r.json()["warnings"])
    assert FakeSSH.cleanup_calls


def test_pair_no_free_port_on_b_warns_no_client_no_cleanup(fake_ssh):
    """B has no free iperf port (IPERF_PORT=0) → warning, the A-side client never
    runs, and no cleanup fires (nothing was started)."""
    FakeSSH.port_busy = True
    h = _auth()
    r = client.post(
        "/api/speedtest/pair", headers=h, json={"a": _node(A_IP), "b": _node(B_IP)}
    )
    assert r.status_code == 200
    assert any("свободного порта" in w for w in r.json()["warnings"])
    assert not any("iperf3 -c" in s for s in FakeSSH.scripts)  # client skipped
    assert FakeSSH.cleanup_calls == []  # nothing to tear down


def test_pair_cleanup_skipped_when_b_unreachable(fake_ssh):
    """B unreachable (connect fails before the server starts) → warning, no
    client run, and no cleanup attempt (the ephemeral server never came up)."""
    FakeSSH.dead_hosts = {B_IP}
    h = _auth()
    r = client.post(
        "/api/speedtest/pair", headers=h, json={"a": _node(A_IP), "b": _node(B_IP)}
    )
    assert r.status_code == 200
    assert any("Сторона B недоступна" in w for w in r.json()["warnings"])
    assert not any("iperf3 -c" in s for s in FakeSSH.scripts)
    assert FakeSSH.cleanup_calls == []


def test_pair_testserver_receiver_no_ephemeral(fake_ssh):
    """B=testserver → connect straight to its permanent iperf3, no ephemeral
    server is started on B and no cleanup is needed."""
    h = _auth()
    r = client.post(
        "/api/speedtest/pair",
        headers=h,
        json={"a": _node(A_IP), "b": _testserver(TS_IP, 5201)},
    )
    assert r.status_code == 200
    assert r.json()["current"]["iperf_mbps"] == pytest.approx(941.2)
    # client hit the testserver directly; no ephemeral server / cleanup on B
    assert any("iperf3 -c 5.5.5.5 -p 5201" in s for s in FakeSSH.scripts)
    assert not any("iperf3 -s -p" in s and "IPERF_PORT" in s for s in FakeSSH.scripts)
    assert FakeSSH.cleanup_calls == []
    assert B_IP not in FakeSSH.connects and TS_IP not in FakeSSH.connects


# ── xray endpoint ─────────────────────────────────────────────


def test_xray_full_run(fake_ssh):
    h = _auth()
    r = client.post(
        "/api/speedtest/xray",
        headers=h,
        json={"source": _node(A_IP), "xray_link": XRAY_LINK, "metrics": [1]},
    )
    assert r.status_code == 200
    cur = r.json()["current"]
    assert cur["kind"] == "xray"
    assert cur["resource_key"] == A_IP
    assert cur["xray_down"] == pytest.approx(100.0)  # bytes/s * 8 / 1e6
    assert cur["xray_ping"] == pytest.approx(45.0)  # seconds → ms


def test_xray_bad_link_422_without_leak(fake_ssh):
    h = _auth()
    secret = "http://secret-user:secret-pass@evil.example"
    r = client.post(
        "/api/speedtest/xray",
        headers=h,
        json={"source": _node(A_IP), "xray_link": secret},
    )
    assert r.status_code == 422
    assert "secret-" not in r.text  # the link never leaks into the detail


def test_xray_source_unreachable_502(fake_ssh):
    FakeSSH.dead_hosts = {A_IP}
    h = _auth()
    r = client.post(
        "/api/speedtest/xray",
        headers=h,
        json={"source": _node(A_IP), "xray_link": XRAY_LINK},
    )
    assert r.status_code == 502


# ── history + isolation ───────────────────────────────────────


def test_history_lists_pair_and_xray_isolated(fake_ssh):
    h1, h2 = _auth(), _auth()
    client.post(
        "/api/speedtest/pair", headers=h1, json={"a": _node(A_IP), "b": _node(B_IP)}
    )
    client.post(
        "/api/speedtest/xray",
        headers=h1,
        json={"source": _node(A_IP), "xray_link": XRAY_LINK},
    )
    r = client.get("/api/speedtest/history", headers=h1)
    assert r.status_code == 200
    kinds = {row["kind"] for row in r.json()["history"]}
    assert kinds == {"pair", "xray"}
    # a second account sees none of account 1's runs
    r2 = client.get("/api/speedtest/history", headers=h2)
    assert r2.json()["history"] == []
