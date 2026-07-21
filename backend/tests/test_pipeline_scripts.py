"""Ф5 — unit smoke of the pipeline's bash-script generators.

Exercises the pure string builders (no SSH / no network) to prove the new
whitelist / allow_ssh_all / cert-provider behavior emits the expected script
fragments. `asyncssh` is stubbed so the module imports without the SSH stack.
"""
import asyncio
import sys
import types

import pytest

# Stub asyncssh (imported transitively via ssh_manager) before importing pipeline.
sys.modules.setdefault("asyncssh", types.ModuleType("asyncssh"))

import os  # noqa: E402
import re  # noqa: E402

import app.services.pipeline as pipeline  # noqa: E402
from app.services.pipeline import (  # noqa: E402
    _parse_ip_list,
    _fail2ban_setup,
    _firewall_extra_script,
    step_ssl,
)
from app.services.task_store import STEP_LABELS  # noqa: E402


# ── Ф6 step-index consistency (guards against renumber desync) ────

def test_every_step_index_is_begun_and_in_range():
    """Every 1..len(STEP_LABELS) is emitted by some _begin_step call, none is out
    of range, and no stray old index survives a future renumber."""
    src = os.path.join(os.path.dirname(pipeline.__file__), "pipeline.py")
    with open(src, encoding="utf-8") as fh:
        used = sorted({int(m) for m in re.findall(r"_begin_step\(task, (\d+)", fh.read())})
    assert used == list(range(1, len(STEP_LABELS) + 1)), (
        f"begin_step indices {used} must exactly cover 1..{len(STEP_LABELS)}"
    )


def test_docker_mirror_script():
    # E1 (Plan B): idempotent registry-mirror merge into daemon.json.
    s = pipeline._docker_mirror_script()
    assert "registry-mirrors" in s
    assert "mirror.gcr.io" in s and "dockerhub.timeweb.cloud" in s
    assert "/etc/docker/daemon.json" in s
    assert "systemctl restart docker" in s


def test_vanilla_compose():
    # Plan B 2b: official remnawave/node only — no nginx/masking/cert volume.
    c = pipeline._render_vanilla_compose(2222, "SECRET.tok")
    assert "remnawave/node:latest" in c
    assert "NODE_PORT=2222" in c
    assert "SECRET_KEY=SECRET.tok" in c
    assert "network_mode: host" in c
    assert "remnawave-nginx" not in c        # no masking front
    assert "/etc/letsencrypt" not in c        # no local cert bridge


def test_step_labels_count_and_key_renames():
    assert len(STEP_LABELS) == 14
    assert STEP_LABELS[4] == "Тест-инструменты"         # step 5 (Ф2 wave1, in «Оптимизация ОС»)
    assert STEP_LABELS[9] == "Cloudflare DNS + SSL"     # step 10, standalone
    assert STEP_LABELS[11] == "Уникализация маскировочного сайта"  # step 12 (before WARP)
    assert STEP_LABELS[12] == "WARP Native"             # step 13
    assert STEP_LABELS[13] == "Hysteria2"               # step 14 (renamed from SSL Certbot)


class _Task:
    """Fake Task capturing logs; satisfies _begin_step + step_ssl."""
    total_steps = 11

    def __init__(self):
        self.logs = []

    def set_step(self, *a, **k):
        pass

    def add_log(self, line):
        self.logs.append(line)


class _SSH:
    """Fake SSHSession capturing the last script passed to run_script."""
    def __init__(self):
        self.scripts = []

    async def run_script(self, script, task, **kw):
        self.scripts.append(script)


async def _run_ssl(monkeypatch, provider):
    upserts = []

    async def _fake_upsert(cf, domain, ip):
        upserts.append((domain, ip))

    monkeypatch.setattr(pipeline, "upsert_a_record", _fake_upsert)
    ssh, task = _SSH(), _Task()
    await step_ssl(ssh, task, "node1.example.com", "a@b.co", "cf-tok", "1.2.3.4", provider)
    return ssh.scripts[-1], upserts


class _Req:
    """Minimal stand-in for DeployRequest (the script builders only read a few
    attrs; avoids constructing the full pydantic model)."""
    def __init__(self, **kw):
        self.change_ssh_port = kw.get("change_ssh_port", True)
        self.current_ssh_port = kw.get("current_ssh_port", 22)
        self.new_ssh_port = kw.get("new_ssh_port", 2222)
        self.allow_ssh_all = kw.get("allow_ssh_all", False)
        self.whitelist_ips = kw.get("whitelist_ips", "")
        self.install_test_tools = kw.get("install_test_tools", True)


# ── step_test_tools (Ф2 wave1: step 5, optional + non-fatal) ──

def test_step_test_tools_skips_when_disabled():
    ssh, task = _SSH(), _Task()
    asyncio.run(pipeline.step_test_tools(ssh, task, _Req(install_test_tools=False)))
    assert ssh.scripts == []  # installer never ran
    assert any("install_test_tools=false" in ln for ln in task.logs)


def test_step_test_tools_runs_shared_installer():
    ssh, task = _SSH(), _Task()
    asyncio.run(pipeline.step_test_tools(ssh, task, _Req()))
    assert ssh.scripts, "installer script must run"
    s = ssh.scripts[-1]
    assert "iperf3" in s and "speedtest" in s  # the Ф1 shared installer


def test_step_test_tools_failure_is_nonfatal():
    class _BoomSSH:
        async def run_script(self, *a, **k):
            raise RuntimeError("network down")

    task = _Task()
    # must NOT raise — test tools are optional, the deploy continues
    asyncio.run(pipeline.step_test_tools(_BoomSSH(), task, _Req()))
    assert any("ПРЕДУПРЕЖДЕНИЕ" in ln for ln in task.logs)


# ── _parse_ip_list ────────────────────────────────────────────

def test_parse_ip_list_mixed_separators_and_cidr():
    got = _parse_ip_list("1.2.3.4, 10.0.0.0/24\n192.168.1.1 8.8.8.8")
    assert got == ["1.2.3.4", "10.0.0.0/24", "192.168.1.1", "8.8.8.8"]


def test_parse_ip_list_empty():
    assert _parse_ip_list("") == []
    assert _parse_ip_list("   \n , ") == []


def test_parse_ip_list_drops_garbage_and_ipv6_and_dedups():
    got = _parse_ip_list("garbage 1.2.3.4 999.1.1.1 1.2.3.4 ::1 2001:db8::1 10/8 10.0.0.0/8")
    # keeps valid IPv4/CIDR only, dedups 1.2.3.4, drops garbage / 999.x / ipv6.
    # "10/8" is NOT a valid ipaddress string (needs a dotted quad) → dropped;
    # the proper CIDR "10.0.0.0/8" is kept.
    assert got == ["1.2.3.4", "10.0.0.0/8"]


def test_parse_ip_list_normalizes_cidr_host_bits():
    # strict=False → host bits are masked off.
    assert _parse_ip_list("10.0.0.5/24") == ["10.0.0.0/24"]


# ── _fail2ban_setup ───────────────────────────────────────────

def test_fail2ban_ignoreip_includes_backend_and_whitelist():
    s = _fail2ban_setup("203.0.113.9", ["1.2.3.4", "10.0.0.0/24"])
    assert "ignoreip  = 127.0.0.1/8 ::1 203.0.113.9 1.2.3.4 10.0.0.0/24" in s


def test_fail2ban_ignoreip_backend_only_when_no_whitelist():
    s = _fail2ban_setup("203.0.113.9", [])
    assert "ignoreip  = 127.0.0.1/8 ::1 203.0.113.9" in s


def test_fail2ban_ignoreip_localhost_only_when_nothing():
    s = _fail2ban_setup("", [])
    assert "ignoreip  = 127.0.0.1/8 ::1\n" in s


def test_fail2ban_sshd_maxretry_default_and_doubled():
    assert "maxretry = 4\nbantime  = 86400" in _fail2ban_setup("ip", [], ssh_maxretry=4)
    assert "maxretry = 8\nbantime  = 86400" in _fail2ban_setup("ip", [], ssh_maxretry=8)


# ── _firewall_extra_script ────────────────────────────────────

def test_firewall_whitelist_ufw_allow_from():
    s = _firewall_extra_script(_Req(), ["1.2.3.4", "10.0.0.0/24"])
    assert "ufw allow from 1.2.3.4 to any comment 'deploy-whitelist'" in s
    assert "ufw allow from 10.0.0.0/24 to any comment 'deploy-whitelist'" in s


def test_firewall_allow_ssh_all_opens_port_only_when_no_port_change():
    # change_ssh_port ON → the dual-port script already opens the new port to all,
    # so we must NOT add a duplicate rule (a Scenario-Б rollback wouldn't remove it).
    s = _firewall_extra_script(_Req(allow_ssh_all=True, change_ssh_port=True, new_ssh_port=2222), [])
    assert "SSH open (all)" not in s
    # change_ssh_port OFF → the dual-port script didn't run, so we open the
    # (current) SSH port to all here.
    s2 = _firewall_extra_script(_Req(allow_ssh_all=True, change_ssh_port=False, current_ssh_port=22), [])
    assert "ufw allow 22/tcp comment 'SSH open (all)'" in s2


def test_firewall_no_rules_when_empty_and_ssh_all_off():
    s = _firewall_extra_script(_Req(allow_ssh_all=False), [])
    assert "нет доп. правил" in s
    assert "ufw allow" not in s


# ── step_ssl cert-provider branching ──────────────────────────

def test_ssl_cloudflare_uses_dns01_and_upserts_a_record(monkeypatch):
    script, upserts = asyncio.run(_run_ssl(monkeypatch, "cloudflare"))
    assert upserts == [("node1.example.com", "1.2.3.4")]   # CF manages DNS
    assert 'export CF_Token="cf-tok"' in script
    assert "--dns dns_cf --server letsencrypt" in script
    assert "--standalone" not in script


def test_ssl_letsencrypt_uses_http01_standalone_no_upsert(monkeypatch):
    script, upserts = asyncio.run(_run_ssl(monkeypatch, "letsencrypt"))
    assert upserts == []                                    # no CF DNS management
    assert "fuser -k 80/tcp" in script
    assert "--standalone --server letsencrypt" in script
    assert "dns_cf" not in script


def test_ssl_zerossl_registers_eab_by_email_and_standalone(monkeypatch):
    script, upserts = asyncio.run(_run_ssl(monkeypatch, "zerossl"))
    assert upserts == []
    assert "--register-account --server zerossl -m \"a@b.co\"" in script
    assert "--standalone --server zerossl" in script
    assert "fuser -k 80/tcp" in script


def test_ssl_skip_guard_keys_off_ca_marker(monkeypatch):
    # cloudflare + letsencrypt share the Let's Encrypt CA → marker "letsencrypt";
    # zerossl → marker "zerossl". The skip guard greps the acme conf for the
    # marker so a cross-CA provider switch on retry re-issues instead of reusing.
    cf, _ = asyncio.run(_run_ssl(monkeypatch, "cloudflare"))
    le, _ = asyncio.run(_run_ssl(monkeypatch, "letsencrypt"))
    zs, _ = asyncio.run(_run_ssl(monkeypatch, "zerossl"))
    assert "grep -qi 'letsencrypt'" in cf
    assert "grep -qi 'letsencrypt'" in le
    assert "grep -qi 'zerossl'" in zs
    # the guard gates the skip on CA_OK
    for s in (cf, le, zs):
        assert '[ "$CA_OK" = "1" ]' in s


def test_ssl_all_providers_issue_per_fqdn_not_wildcard(monkeypatch):
    for prov in ("cloudflare", "letsencrypt", "zerossl"):
        script, _ = asyncio.run(_run_ssl(monkeypatch, prov))
        assert '-d "node1.example.com"' in script
        # the -d domain arg is never a wildcard (the rate-limit note in the
        # comment mentions "*.root" — assert on the actual issued identifier).
        assert '-d "*' not in script
