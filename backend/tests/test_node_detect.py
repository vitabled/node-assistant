"""Ф5 — read-only component detection + skip_components pipeline gating.

Covers:
  (a) each _DETECT_SCRIPTS[component] embeds the expected read-only probe;
  (b) the sentinel parser maps present/absent/empty outputs correctly;
  (c) `DeployRequest.skip_components` is a valid model field, and `run_pipeline`
      begins-but-skips a component listed in it (via a mock/spy proving the step
      body isn't run while the step index is still begun);
  (d) the /api/node/detect route (SSH mocked) reports per-component statuses.

`asyncssh` is stubbed (as in test_pipeline_scripts) so the SSH stack imports
without native deps.
"""
import asyncio
import sys
import types
import uuid

import pytest
from fastapi.testclient import TestClient

# Stub asyncssh before importing anything that pulls in ssh_manager.
sys.modules.setdefault("asyncssh", types.ModuleType("asyncssh"))

import app.api.node_ops as node_ops  # noqa: E402
import app.services.pipeline as pipeline  # noqa: E402
from app.models.deploy import DeployRequest  # noqa: E402
from app.services.task_store import TaskStatus  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"nd-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ── (a) probe commands ────────────────────────────────────────

def test_detect_scripts_cover_all_components():
    from app.api.node_ops import Component  # Literal
    expected = set(Component.__args__)
    assert set(node_ops._DETECT_SCRIPTS) == expected


def test_detect_scripts_contain_expected_probes():
    d = "node1.example.com"
    s = {c: b(d) for c, b in node_ops._DETECT_SCRIPTS.items()}
    assert "test -d /opt/node-accelerator" in s["node_accelerator"]
    assert "test -d /opt/TrafficGuard-auto" in s["trafficguard"]
    # test_tools: iperf3 + either speedtest CLI (Ookla `speedtest` or python
    # `speedtest-cli` — Ф1 installs whichever works, the probe accepts both)
    assert "command -v iperf3" in s["test_tools"]
    assert "command -v speedtest" in s["test_tools"]
    assert "command -v speedtest-cli" in s["test_tools"]
    assert "docker ps --filter name=remnanode" in s["remnanode"]
    assert "/var/www/html" in s["masking"]
    assert "wg show warp" in s["warp"]
    assert "/opt/certbot/certs/live/*/fullchain.pem" in s["hysteria2"]
    assert "systemctl is-active haproxy" in s["haproxy"]
    # ssl interpolates the (validated) domain into the acme.sh cert path
    assert "/root/.acme.sh/node1.example.com_ecc/node1.example.com.cer" in s["ssl"]
    # every probe echoes the sentinels
    for cmd in s.values():
        assert node_ops._DETECT_PRESENT in cmd and node_ops._DETECT_ABSENT in cmd


# ── (b) sentinel parser ───────────────────────────────────────

def test_parse_detect_present_absent_unknown():
    assert node_ops._parse_detect(node_ops._DETECT_PRESENT) == "present"
    assert node_ops._parse_detect(node_ops._DETECT_ABSENT) == "absent"
    assert node_ops._parse_detect("") == "unknown"
    assert node_ops._parse_detect("garbage") == "unknown"
    # last non-empty line wins (motd / warnings before the sentinel are ignored)
    assert node_ops._parse_detect(f"motd banner\n{node_ops._DETECT_PRESENT}") == "present"
    assert node_ops._parse_detect(f"warn\n{node_ops._DETECT_ABSENT}\n") == "absent"


# ── (c) skip_components model field + pipeline gating ─────────

def _mk_req(**over) -> DeployRequest:
    base = dict(
        mode="remnanode", ip="1.2.3.4", ssh_password="pw",
        domain="node1.example.com", email="a@b.co",
        cert_provider="letsencrypt", remnanode_token="tok",
        open_ports="80,443", create_in_remnawave=False,
        country_code="US", install_warp=False, install_trafficguard=True,
        change_ssh_port=False,
    )
    base.update(over)
    return DeployRequest(**base)


def test_skip_components_is_a_valid_field():
    assert _mk_req().skip_components == []
    r = _mk_req(skip_components=["node_accelerator", "ssl"])
    assert r.skip_components == ["node_accelerator", "ssl"]


class _Task:
    total_steps = 14

    def __init__(self):
        self.begun: list[int] = []
        self.logs: list[str] = []
        self.status = None

    def set_step(self, i, _s):
        self.begun.append(i)

    def add_log(self, line):
        self.logs.append(line)

    def finish(self, status, *_a):
        self.status = status


class _SSH:
    def __init__(self, *a, **k):
        pass

    async def connect(self, *a, **k):
        pass

    async def get_output(self, *a, **k):
        return "Ubuntu 22.04"

    async def run_script(self, *a, **k):
        return 0

    async def run(self, *a, **k):
        return 0

    async def close(self):
        pass


def _run_pipeline_with_spies(monkeypatch, req):
    """Run run_pipeline with SSH + every step_* mocked, returning the list of
    step function names that actually executed (skips never appear)."""
    called: list[str] = []

    def rec(name):
        async def f(*a, **k):
            called.append(name)
        return f

    async def dualport(ssh, task, *a, **k):
        called.append("step_ssh_dualport_verify")
        return ssh

    async def backend_ip():
        return ""

    monkeypatch.setattr(pipeline, "SSHSession", _SSH)
    monkeypatch.setattr(pipeline, "get_backend_ip", backend_ip)
    for name in (
        "step_node_accelerator", "step_traffic_guard", "step_test_tools",
        "step_system_optimize",
        "step_ssl", "step_remnanode", "step_remnanode_vanilla", "step_sni_masking",
        "step_warp", "step_certbot_ssl", "step_haproxy_deploy",
    ):
        monkeypatch.setattr(pipeline, name, rec(name))
    monkeypatch.setattr(pipeline, "step_ssh_dualport_verify", dualport)

    task = _Task()
    asyncio.run(pipeline.run_pipeline(req, task))
    return called, task


def test_run_pipeline_skips_listed_components(monkeypatch):
    req = _mk_req(skip_components=["node_accelerator", "ssl", "masking"])
    called, task = _run_pipeline_with_spies(monkeypatch, req)

    assert task.status == TaskStatus.SUCCESS
    # Skipped step bodies did NOT run …
    assert "step_node_accelerator" not in called
    assert "step_ssl" not in called
    assert "step_sni_masking" not in called
    # … but the non-skipped ones did.
    assert "step_traffic_guard" in called
    assert "step_remnanode" in called
    assert "step_certbot_ssl" in called
    # … and the skipped step indices were still begun (progress bar advances).
    # (Ф2 wave1 renumber: ssl=10, masking=12 after the step-5 «Тест-инструменты» insert.)
    for idx in (3, 10, 12):
        assert idx in task.begun
    # a skip log was emitted
    assert any("Пропущено — уже установлено" in ln for ln in task.logs)


def test_run_pipeline_skips_test_tools_component(monkeypatch):
    req = _mk_req(skip_components=["test_tools"])
    called, task = _run_pipeline_with_spies(monkeypatch, req)
    assert task.status == TaskStatus.SUCCESS
    assert "step_test_tools" not in called
    assert 5 in task.begun  # step 5 still begun (progress bar advances)


def test_run_pipeline_vanilla_variant(monkeypatch):
    # Plan B 2b: vanilla uses the official node install and skips SSL + masking.
    req = _mk_req(node_variant="vanilla")
    called, task = _run_pipeline_with_spies(monkeypatch, req)
    assert task.status == TaskStatus.SUCCESS
    assert "step_remnanode_vanilla" in called   # official install
    assert "step_remnanode" not in called       # NOT the eGames stack
    assert "step_ssl" not in called             # SSL skipped in vanilla
    assert "step_sni_masking" not in called     # masking skipped in vanilla
    # SSL (10) and masking (12) are begun-but-skipped directly (step 11's begin is
    # inside the mocked vanilla install, so it isn't recorded here).
    for idx in (10, 12):
        assert idx in task.begun


def test_run_pipeline_empty_skip_runs_everything(monkeypatch):
    req = _mk_req(skip_components=[])
    called, task = _run_pipeline_with_spies(monkeypatch, req)
    assert task.status == TaskStatus.SUCCESS
    for name in ("step_node_accelerator", "step_traffic_guard", "step_ssl",
                 "step_remnanode", "step_sni_masking", "step_certbot_ssl"):
        assert name in called


def test_run_pipeline_unknown_skip_component_is_ignored(monkeypatch):
    # A garbage component in skip_components must not break the deploy.
    req = _mk_req(skip_components=["totally_bogus"])
    called, task = _run_pipeline_with_spies(monkeypatch, req)
    assert task.status == TaskStatus.SUCCESS
    assert "step_node_accelerator" in called  # nothing actually skipped


# ── (d) /api/node/detect route (SSH mocked) ───────────────────

def test_detect_requires_auth():
    assert client.post("/api/node/detect", json={"ip": "1.2.3.4", "ssh_password": "p"}).status_code == 401


def test_detect_route_reports_statuses(monkeypatch):
    class FakeSSH:
        def __init__(self, *a, **k):
            pass

        async def connect(self, *a, **k):
            pass

        async def get_output(self, command):
            # remnanode/masking probes → present; everything else → absent.
            if "remnanode" in command or "/var/www/html" in command:
                return node_ops._DETECT_PRESENT
            return node_ops._DETECT_ABSENT

        async def close(self):
            pass

    monkeypatch.setattr(node_ops, "SSHSession", FakeSSH)
    r = client.post("/api/node/detect", headers=_auth(), json={
        "ip": "1.2.3.4", "ssh_user": "root", "ssh_password": "pw",
        "ssh_port": 22, "domain": "node1.example.com",
    })
    assert r.status_code == 200
    res = r.json()["results"]
    assert res["remnanode"] == "present"
    assert res["masking"] == "present"
    assert res["node_accelerator"] == "absent"
    assert res["ssl"] == "absent"


def test_detect_route_ssl_unknown_without_domain(monkeypatch):
    class FakeSSH:
        def __init__(self, *a, **k):
            pass

        async def connect(self, *a, **k):
            pass

        async def get_output(self, command):
            return node_ops._DETECT_ABSENT

        async def close(self):
            pass

    monkeypatch.setattr(node_ops, "SSHSession", FakeSSH)
    r = client.post("/api/node/detect", headers=_auth(), json={
        "ip": "1.2.3.4", "ssh_password": "pw",
    })
    assert r.status_code == 200
    # No domain → SSL can't be probed → unknown (operator decides).
    assert r.json()["results"]["ssl"] == "unknown"


def test_detect_route_connection_failure_502(monkeypatch):
    class FakeSSH:
        def __init__(self, *a, **k):
            pass

        async def connect(self, *a, **k):
            raise OSError("connection refused")

        async def close(self):
            pass

    monkeypatch.setattr(node_ops, "SSHSession", FakeSSH)
    r = client.post("/api/node/detect", headers=_auth(), json={
        "ip": "1.2.3.4", "ssh_password": "pw",
    })
    assert r.status_code == 502
    assert "Не удалось подключиться" in r.json()["detail"]


def test_detect_route_rejects_bad_domain():
    r = client.post("/api/node/detect", headers=_auth(), json={
        "ip": "1.2.3.4", "ssh_password": "pw", "domain": "bad;rm -rf /",
    })
    assert r.status_code == 422


# ── (e) settings autodetect (Wave-4 Plan B) ───────────────────

def test_parse_settings_types_and_omits_empty():
    out = "\n".join([
        "NIVAL:ssh_port=2222",
        "NIVAL:open_ports=80,443",
        "NIVAL:domain=node1.example.com",
        "NIVAL:remnanode_port=",       # empty → omitted
        "NIVAL:xhttp_path=/xhttp",
        "NIVAL:has_token=1",
    ])
    s = node_ops._parse_settings(out)
    assert s["ssh_port"] == 2222 and isinstance(s["ssh_port"], int)
    assert s["open_ports"] == "80,443"
    assert s["domain"] == "node1.example.com"
    assert "remnanode_port" not in s       # empty value omitted
    assert s["xhttp_path"] == "/xhttp"
    assert s["has_token"] is True
    # the raw token is NEVER surfaced — only the has_token bool
    assert all("token" not in k or k == "has_token" for k in s)


def test_parse_settings_has_token_false_and_garbage():
    assert node_ops._parse_settings("NIVAL:has_token=0")["has_token"] is False
    assert node_ops._parse_settings("random motd\nno values here") == {}


def test_detect_route_includes_settings(monkeypatch):
    class FakeSSH:
        def __init__(self, *a, **k):
            pass

        async def connect(self, *a, **k):
            pass

        async def get_output(self, command):
            if "NIVAL" in command:
                return ("NIVAL:ssh_port=2222\nNIVAL:domain=node1.example.com\n"
                        "NIVAL:has_token=1")
            return node_ops._DETECT_ABSENT

        async def close(self):
            pass

    monkeypatch.setattr(node_ops, "SSHSession", FakeSSH)
    r = client.post("/api/node/detect", headers=_auth(), json={
        "ip": "1.2.3.4", "ssh_password": "pw", "domain": "node1.example.com",
    })
    assert r.status_code == 200
    s = r.json()["settings"]
    assert s["ssh_port"] == 2222
    assert s["domain"] == "node1.example.com"
    assert s["has_token"] is True
    # components still reported alongside settings
    assert "results" in r.json()
