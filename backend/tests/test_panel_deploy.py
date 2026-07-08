"""Ф4 (wave1) — Remnawave panel / subscription-page deploy.

Contract verification (the generators + model were written earlier; this locks
their behaviour):
  (a) pure config builders (`_env_file`/`_compose_yml`/`_caddyfile`/`_nginx_conf`
      /`_subpage_env`/`_subpage_compose`) — no SSH, no network;
  (b) `PanelDeployRequest` validators (target/cert/webhook/extra_env/domain/ip
      shell-safety);
  (c) the /api/panel routes with a mocked SSHSession (creds transient);
  (d) generated secrets NEVER reach the Task log — the .env goes through the
      SILENT channel only.
"""

import asyncio
import re
import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.api.panel_deploy as panel_deploy  # noqa: E402
from app.main import app
from app.models.panel_deploy import PanelDeployRequest
from app.services import panel_pipeline
from app.services.task_store import TaskStatus, task_store

client = TestClient(app)


def _auth():
    r = client.post(
        "/api/auth/register",
        json={"login": f"pd-{uuid.uuid4().hex[:8]}", "password": "pw"},
    )
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _req(**over) -> PanelDeployRequest:
    base = dict(
        target="panel",
        ip="1.2.3.4",
        ssh_password="pw",
        panel_domain="panel.example.com",
        sub_domain="sub.example.com",
        email="a@b.co",
    )
    base.update(over)
    return PanelDeployRequest(**base)


def _parse_env(text: str) -> dict:
    return dict(line.split("=", 1) for line in text.strip().splitlines() if "=" in line)


# ── (a) pure config builders ───────────────────────────────────


def test_env_file_has_all_required_keys():
    env = _parse_env(panel_pipeline._env_file(_req()))
    for k in (
        "DATABASE_URL",
        "JWT_AUTH_SECRET",
        "JWT_API_TOKENS_SECRET",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "FRONT_END_DOMAIN",
        "SUB_PUBLIC_DOMAIN",
    ):
        assert env.get(k), f"missing/empty .env key {k}"
    # the PG password is reused in the DATABASE_URL DSN
    assert env["POSTGRES_PASSWORD"] in env["DATABASE_URL"]
    assert env["FRONT_END_DOMAIN"] == "panel.example.com"


def test_env_file_webhooks_gated():
    off = _parse_env(panel_pipeline._env_file(_req()))
    assert "WEBHOOK_ENABLED" not in off
    on = _parse_env(
        panel_pipeline._env_file(
            _req(enable_webhooks=True, webhook_url="https://hook.example.com/x")
        )
    )
    assert on["WEBHOOK_ENABLED"] == "true"
    assert on["WEBHOOK_URL"] == "https://hook.example.com/x"
    assert len(on["WEBHOOK_SECRET_HEADER"]) >= 32  # generated HMAC secret


def test_env_file_extra_env_overrides_base():
    env = _parse_env(
        panel_pipeline._env_file(
            _req(
                extra_env={
                    "POSTGRES_DB": "custom",
                    "POSTGRES_USER": "ruser",
                    "FOO": "bar",
                }
            )
        )
    )
    assert env["POSTGRES_DB"] == "custom"  # overrode the base
    assert env["FOO"] == "bar"  # and added a new one
    # DATABASE_URL is derived AFTER extra_env → stays in sync with the override
    assert env["DATABASE_URL"] == (
        f"postgresql://ruser:{env['POSTGRES_PASSWORD']}@remnawave-db:5432/custom"
    )


def test_env_file_has_panel_domain():
    env = _parse_env(panel_pipeline._env_file(_req()))
    assert env["PANEL_DOMAIN"] == "panel.example.com"


def test_env_file_secrets_are_nondeterministic():
    a = _parse_env(panel_pipeline._env_file(_req()))
    b = _parse_env(panel_pipeline._env_file(_req()))
    assert a["JWT_AUTH_SECRET"] != b["JWT_AUTH_SECRET"]
    assert a["POSTGRES_PASSWORD"] != b["POSTGRES_PASSWORD"]


def test_compose_yml_shape():
    yml = panel_pipeline._compose_yml(_req())
    assert "remnawave-backend" in yml
    assert "postgres:18.4" in yml
    assert "valkey" in yml
    assert "TZ=UTC" in yml


def test_caddyfile_and_nginx_render_panel_domain():
    caddy = panel_pipeline._caddyfile(_req())
    assert caddy.strip()
    assert "panel.example.com" in caddy
    assert "reverse_proxy 127.0.0.1:3000" in caddy

    nginx = panel_pipeline._nginx_conf(_req())
    assert nginx.strip()
    assert "panel.example.com" in nginx
    assert "proxy_pass http://127.0.0.1:3000" in nginx
    assert "ssl_certificate" in nginx


def test_subpage_generators():
    req = _req(target="subpage", panel_domain="", sub_domain="sub.example.com")
    compose = panel_pipeline._subpage_compose(req)
    assert "remnawave/subscription-page" in compose
    assert "3010" in compose

    env = _parse_env(panel_pipeline._subpage_env(req))
    assert env["APP_PORT"] == "3010"
    assert "REMNAWAVE_PANEL_URL" in env

    # sub_domain is routed by the subpage box's reverse proxy
    caddy = panel_pipeline._render_caddy(
        panel_pipeline._proxy_targets(req, "sub"), req.email
    )
    assert "sub.example.com" in caddy
    assert "reverse_proxy 127.0.0.1:3010" in caddy


# ── (b) model validators ───────────────────────────────────────


def test_target_panel_requires_panel_domain():
    with pytest.raises(ValidationError):
        PanelDeployRequest(target="panel", ip="1.2.3.4", ssh_password="pw")


def test_target_subpage_requires_sub_domain():
    with pytest.raises(ValidationError):
        PanelDeployRequest(target="subpage", ip="1.2.3.4", ssh_password="pw")
    # sub_domain present → ok
    PanelDeployRequest(
        target="subpage", ip="1.2.3.4", ssh_password="pw", sub_domain="s.example.com"
    )


def test_cloudflare_requires_cf_api_key_only_for_nginx():
    # nginx (acme.sh) + cloudflare → token required
    with pytest.raises(ValidationError):
        _req(reverse_proxy="nginx", cert_provider="cloudflare")
    _req(reverse_proxy="nginx", cert_provider="cloudflare", cf_api_key="cf-token")  # ok
    # caddy manages TLS itself → cert_provider/cf_api_key are ignored, no token needed
    _req(reverse_proxy="caddy", cert_provider="cloudflare")


def test_nginx_letsencrypt_requires_email():
    with pytest.raises(ValidationError):
        _req(reverse_proxy="nginx", cert_provider="letsencrypt", email="")
    with pytest.raises(ValidationError):
        _req(reverse_proxy="nginx", cert_provider="zerossl", email="")
    _req(reverse_proxy="nginx", cert_provider="letsencrypt", email="a@b.co")  # ok
    # caddy path doesn't need an email
    _req(reverse_proxy="caddy", cert_provider="letsencrypt", email="")


def test_sub_server_only_valid_for_both_target():
    sub = dict(ip="9.9.9.9", ssh_password="pw")
    with pytest.raises(ValidationError):
        _req(target="panel", sub_server=sub)
    # target=both → allowed
    _req(target="both", sub_server=sub)


def test_webhooks_require_url():
    with pytest.raises(ValidationError):
        _req(enable_webhooks=True)
    _req(enable_webhooks=True, webhook_url="https://hook.example.com/x")  # ok


@pytest.mark.parametrize("bad", [{"bad-key": "x"}, {"lower": "x"}, {"GOOD": "a\nb"}])
def test_extra_env_rejects_bad_key_or_multiline(bad):
    with pytest.raises(ValidationError):
        _req(extra_env=bad)


@pytest.mark.parametrize(
    "key",
    ["JWT_AUTH_SECRET", "POSTGRES_PASSWORD", "DATABASE_URL", "WEBHOOK_SECRET_HEADER"],
)
def test_extra_env_rejects_protected_secret_keys(key):
    # A generated secret must not be weakenable via extra_env override.
    with pytest.raises(ValidationError):
        _req(extra_env={key: "weak"})


@pytest.mark.parametrize("bad", ["a;b.com", "$(x).com", "a b.com", "x`id`.com"])
def test_domain_rejects_shell_metacharacters(bad):
    with pytest.raises(ValidationError):
        _req(panel_domain=bad)


def test_invalid_ip_rejected():
    with pytest.raises(ValidationError):
        _req(ip="999.1.1.1")


def test_separate_sub_server_invalid_ip_rejected():
    with pytest.raises(ValidationError):
        PanelDeployRequest(
            target="both",
            ip="1.2.3.4",
            ssh_password="pw",
            panel_domain="p.example.com",
            sub_domain="s.example.com",
            sub_server={"ip": "999.1.1.1", "ssh_password": "pw"},
        )


# ── (c) routes (SSH mocked) ────────────────────────────────────


class _FullSSH:
    """A fake session that lets `run_panel_pipeline` reach SUCCESS."""

    def __init__(self, *a, **k):
        pass

    async def connect(self, timeout=30):
        pass

    async def get_output(self, command):
        return "remnawave-backend"  # OS probe + running-container check

    async def get_script_output(self, script, timeout=None):
        return "__ENV_WRITTEN__"  # .env write sentinel

    async def run_script(self, script, task, check=True, timeout=None):
        return 0

    async def close(self):
        pass


def test_deploy_requires_auth():
    r = client.post(
        "/api/panel/deploy",
        json={"ip": "1.2.3.4", "ssh_password": "pw", "panel_domain": "p.example.com"},
    )
    assert r.status_code == 401


def test_deploy_endpoint_mocked_ssh(monkeypatch):
    monkeypatch.setattr(panel_pipeline, "SSHSession", _FullSSH)
    r = client.post(
        "/api/panel/deploy",
        headers=_auth(),
        json={
            "ip": "1.2.3.4",
            "ssh_password": "pw",
            "panel_domain": "panel.example.com",
        },
    )
    assert r.status_code == 200
    assert r.json()["task_type"] == "panel"
    task = task_store.get(r.json()["task_id"])
    assert task is not None and task.status == TaskStatus.SUCCESS


def test_deploy_endpoint_invalid_ip_422():
    r = client.post(
        "/api/panel/deploy",
        headers=_auth(),
        json={"ip": "evil; rm", "ssh_password": "pw", "panel_domain": "p.example.com"},
    )
    assert r.status_code == 422


def test_detect_requires_auth():
    assert (
        client.post(
            "/api/panel/detect", json={"ip": "1.2.3.4", "ssh_password": "pw"}
        ).status_code
        == 401
    )


def test_detect_reports_components(monkeypatch):
    class DetectSSH:
        def __init__(self, *a, **k):
            pass

        async def connect(self, *a, **k):
            pass

        async def get_output(self, command):
            # docker + panel present; subpage + test_tools absent
            if "command -v docker" in command:
                return panel_deploy._DETECT_PRESENT
            if "remnawave-backend" in command:  # the panel probe
                return panel_deploy._DETECT_PRESENT
            return panel_deploy._DETECT_ABSENT

        async def close(self):
            pass

    monkeypatch.setattr(panel_deploy, "SSHSession", DetectSSH)
    r = client.post(
        "/api/panel/detect",
        headers=_auth(),
        json={"ip": "1.2.3.4", "ssh_password": "pw", "ssh_port": 22},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["installed"] is True
    comp = body["components"]
    assert comp["docker"] == "present"
    assert comp["panel"] == "present"
    assert comp["subpage"] == "absent"
    assert comp["test_tools"] == "absent"


def test_detect_connection_failure_502(monkeypatch):
    class BoomSSH:
        def __init__(self, *a, **k):
            pass

        async def connect(self, *a, **k):
            raise OSError("connection refused")

        async def close(self):
            pass

    monkeypatch.setattr(panel_deploy, "SSHSession", BoomSSH)
    r = client.post(
        "/api/panel/detect",
        headers=_auth(),
        json={"ip": "1.2.3.4", "ssh_password": "pw"},
    )
    assert r.status_code == 502
    assert "Не удалось подключиться" in r.json()["detail"]


def test_step_requires_auth():
    assert (
        client.post(
            "/api/panel/step",
            json={
                "ip": "1.2.3.4",
                "ssh_password": "pw",
                "panel_domain": "p.example.com",
                "component": "panel",
                "action": "uninstall",
            },
        ).status_code
        == 401
    )


def test_step_uninstall_panel(monkeypatch):
    captured = {}

    class FakeSSH:
        def __init__(self, *a, **k):
            pass

        async def connect(self, *a, **k):
            pass

        async def run_script(self, script, task, check=True, timeout=None):
            captured["script"] = script
            return 0

        async def close(self):
            pass

    monkeypatch.setattr(panel_deploy, "SSHSession", FakeSSH)
    r = client.post(
        "/api/panel/step",
        headers=_auth(),
        json={
            "target": "panel",
            "ip": "1.2.3.4",
            "ssh_password": "pw",
            "panel_domain": "panel.example.com",
            "component": "panel",
            "action": "uninstall",
        },
    )
    assert r.status_code == 200
    assert r.json()["task_type"] == "panel-op"
    task = task_store.get(r.json()["task_id"])
    assert task is not None and task.status == TaskStatus.SUCCESS
    assert "/opt/remnawave" in captured["script"]
    assert "docker compose down" in captured["script"]


def test_step_reinstall_panel(monkeypatch):
    monkeypatch.setattr(panel_deploy, "SSHSession", _FullSSH)
    r = client.post(
        "/api/panel/step",
        headers=_auth(),
        json={
            "target": "panel",
            "ip": "1.2.3.4",
            "ssh_password": "pw",
            "panel_domain": "panel.example.com",
            "component": "panel",
            "action": "reinstall",
        },
    )
    assert r.status_code == 200
    task = task_store.get(r.json()["task_id"])
    assert task is not None and task.status == TaskStatus.SUCCESS


def test_step_uninstall_docker_unsupported(monkeypatch):
    class FakeSSH:
        def __init__(self, *a, **k):
            pass

        async def connect(self, *a, **k):
            pass

        async def close(self):
            pass

    monkeypatch.setattr(panel_deploy, "SSHSession", FakeSSH)
    r = client.post(
        "/api/panel/step",
        headers=_auth(),
        json={
            "target": "panel",
            "ip": "1.2.3.4",
            "ssh_password": "pw",
            "panel_domain": "panel.example.com",
            "component": "docker",
            "action": "uninstall",
        },
    )
    assert r.status_code == 200
    task = task_store.get(r.json()["task_id"])
    # docker uninstall is intentionally out of scope → FAILED, not a silent no-op
    # that lies about success.
    assert task is not None and task.status == TaskStatus.FAILED


class _OpSSH:
    """A fake session that lets every reinstall/uninstall op reach SUCCESS. Its
    get_output returns BOTH container names so the panel + subpage running-checks
    pass; get_script_output returns the .env sentinel; run_script captures scripts."""

    scripts: list = []

    def __init__(self, *a, **k):
        pass

    async def connect(self, *a, **k):
        pass

    async def get_output(self, command):
        return "remnawave-backend remnawave-subscription-page"

    async def get_script_output(self, script, timeout=None):
        return "__ENV_WRITTEN__"

    async def run_script(self, script, task, check=True, timeout=None):
        _OpSSH.scripts.append(script)
        return 0

    async def close(self):
        pass


@pytest.mark.parametrize(
    "component", ["panel", "subpage", "docker", "test_tools", "reverse_proxy"]
)
def test_step_reinstall_all_components(monkeypatch, component):
    monkeypatch.setattr(panel_deploy, "SSHSession", _OpSSH)
    # target=both so subpage bundling + panel both resolve; other components ignore it
    r = client.post(
        "/api/panel/step",
        headers=_auth(),
        json={
            "target": "both",
            "ip": "1.2.3.4",
            "ssh_password": "pw",
            "panel_domain": "panel.example.com",
            "sub_domain": "sub.example.com",
            "component": component,
            "action": "reinstall",
        },
    )
    assert r.status_code == 200
    assert r.json()["task_type"] == "panel-op"
    task = task_store.get(r.json()["task_id"])
    assert task is not None and task.status == TaskStatus.SUCCESS


def test_step_uninstall_subpage(monkeypatch):
    _OpSSH.scripts = []
    monkeypatch.setattr(panel_deploy, "SSHSession", _OpSSH)
    r = client.post(
        "/api/panel/step",
        headers=_auth(),
        json={
            "target": "both",
            "ip": "1.2.3.4",
            "ssh_password": "pw",
            "panel_domain": "panel.example.com",
            "sub_domain": "sub.example.com",
            "component": "subpage",
            "action": "uninstall",
        },
    )
    assert r.status_code == 200
    task = task_store.get(r.json()["task_id"])
    assert task is not None and task.status == TaskStatus.SUCCESS
    assert any("/opt/remnawave-subpage" in s for s in _OpSSH.scripts)


def test_step_uninstall_test_tools(monkeypatch):
    # Ф7 added the test_tools teardown (was previously unsupported → FAILED).
    _OpSSH.scripts = []
    monkeypatch.setattr(panel_deploy, "SSHSession", _OpSSH)
    r = client.post(
        "/api/panel/step",
        headers=_auth(),
        json={
            "target": "panel",
            "ip": "1.2.3.4",
            "ssh_password": "pw",
            "panel_domain": "panel.example.com",
            "component": "test_tools",
            "action": "uninstall",
        },
    )
    assert r.status_code == 200
    task = task_store.get(r.json()["task_id"])
    assert task is not None and task.status == TaskStatus.SUCCESS
    assert any("iperf3" in s for s in _OpSSH.scripts)


def test_step_uninstall_reverse_proxy(monkeypatch):
    _OpSSH.scripts = []
    monkeypatch.setattr(panel_deploy, "SSHSession", _OpSSH)
    r = client.post(
        "/api/panel/step",
        headers=_auth(),
        json={
            "target": "panel",
            "ip": "1.2.3.4",
            "ssh_password": "pw",
            "panel_domain": "panel.example.com",
            "component": "reverse_proxy",
            "action": "uninstall",
        },
    )
    assert r.status_code == 200
    task = task_store.get(r.json()["task_id"])
    assert task is not None and task.status == TaskStatus.SUCCESS
    # stops the proxy service (caddy/nginx), doesn't purge anything
    assert any("systemctl stop" in s for s in _OpSSH.scripts)


# ── (d) secrets never reach the Task log ───────────────────────


def test_env_secrets_never_logged():
    captured = {"scripts": [], "silent": [], "logs": []}

    class SilentSSH:
        def __init__(self, *a, **k):
            pass

        async def connect(self, *a, **k):
            pass

        async def get_output(self, command):
            return "remnawave-backend"

        async def get_script_output(self, script, timeout=None):
            captured["silent"].append(script)
            return "__ENV_WRITTEN__"

        async def run_script(self, script, task, check=True, timeout=None):
            captured["scripts"].append(script)
            return 0

        async def close(self):
            pass

    class FakeTask:
        def set_step(self, *a, **k):
            pass

        def add_log(self, line):
            captured["logs"].append(line)

    asyncio.run(panel_pipeline._install_panel(SilentSSH(), FakeTask(), _req()))

    env_script = "\n".join(captured["silent"])
    # the whole .env (with secrets) must have been pushed via the silent channel
    m = re.search(r"JWT_AUTH_SECRET=([0-9a-f]+)", env_script)
    p = re.search(r"POSTGRES_PASSWORD=([0-9a-f]+)", env_script)
    assert m and p, "secrets were not written through get_script_output"
    for secret in (m.group(1), p.group(1)):
        assert all(secret not in s for s in captured["scripts"]), (
            "secret leaked into a streamed script"
        )
        assert all(secret not in ln for ln in captured["logs"]), (
            "secret leaked into a log line"
        )
