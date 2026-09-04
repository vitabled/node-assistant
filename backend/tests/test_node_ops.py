"""Ф7 — per-component node management endpoint + uninstall script builders."""
import asyncio
import typing
import uuid
from typing import Any, cast

import pytest
import httpx
from fastapi import BackgroundTasks, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.api.node_ops as node_ops
from app.api.node_ops import (
    NodeOpRequest, Component, Action,
    _UNINSTALL_SCRIPTS, _COMPONENT_LABEL, _effective_port,
)
from app.main import app


client = TestClient(app)


def _auth():
    response = client.post(
        "/api/auth/register",
        json={"login": f"no-{uuid.uuid4().hex[:8]}", "password": "pw"},
    )
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _req(**over):
    base = dict(
        ip="1.2.3.4", ssh_password="pw", domain="n.example.com",
        cloudflare_api_key="cf", email="a@b.co", remnanode_token="t",
        country_code="DE", open_ports="80",
        component="warp", action="uninstall",
    )
    base.update(over)
    return NodeOpRequest(**base)


def test_registry_covers_every_component():
    comps = set(typing.get_args(Component))
    assert comps == set(_UNINSTALL_SCRIPTS)
    assert comps == set(_COMPONENT_LABEL)


def test_component_and_action_are_constrained():
    with pytest.raises(ValidationError):
        _req(component="bogus")
    with pytest.raises(ValidationError):
        _req(action="nuke")


def test_effective_port_picks_new_when_changing():
    assert _effective_port(_req(change_ssh_port=True, new_ssh_port=2222, current_ssh_port=22)) == 2222
    assert _effective_port(_req(change_ssh_port=False, new_ssh_port=2222, current_ssh_port=22)) == 22


def test_inherits_deploy_validators_shell_safety():
    # domain/email shell-safety from DeployRequest still applies to ops
    with pytest.raises(ValidationError):
        _req(domain='n.example.com"; reboot #')


def test_remnanode_reinstall_passes_selected_image_tag(monkeypatch):
    captured = {}

    async def fake_step_remnanode(*_args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(node_ops.pipeline, "step_remnanode", fake_step_remnanode)
    asyncio.run(node_ops._reinstall(
        cast(Any, object()), object(),
        _req(component="remnanode", action="reinstall", version="v2.8.0"),
    ))
    assert captured["image_tag"] == "v2.8.0"


def test_remnanode_reinstall_rejects_tag_absent_from_registry(monkeypatch):
    async def fake_versions():
        return ["latest", "v2.8.0"], "registry"

    monkeypatch.setattr(node_ops, "_list_remnanode_versions", fake_versions)
    with pytest.raises(HTTPException) as error:
        asyncio.run(node_ops.node_step(
            _req(component="remnanode", action="reinstall", version="v0.0.0"),
            BackgroundTasks(),
        ))
    assert error.value.status_code == 422
    assert "недоступна" in error.value.detail


def test_remnanode_versions_reads_registry_tags_and_current_image(monkeypatch):
    class FakeSSH:
        def __init__(self, *_args, **_kwargs):
            pass

        async def connect(self):
            pass

        async def get_output(self, _command):
            return "remnawave/node:v2.8.0\n"

        async def close(self):
            pass

    async def fake_versions():
        return ["latest", "v2.8.0"], "registry"

    monkeypatch.setattr(node_ops, "SSHSession", FakeSSH)
    monkeypatch.setattr(node_ops, "_list_remnanode_versions", fake_versions)
    response = client.request(
        "GET", "/api/node/remnanode/versions", headers=_auth(),
        json={"ip": "1.2.3.4", "ssh_password": "pw"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "versions": ["latest", "v2.8.0"],
        "current": "v2.8.0",
        "source": "registry",
    }


def test_remnanode_versions_use_snapshot_when_registry_is_unavailable(monkeypatch):
    class FailingAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def get(self, *_args, **_kwargs):
            raise httpx.ConnectError("Docker Hub unavailable")

    monkeypatch.setattr(node_ops.httpx, "AsyncClient", FailingAsyncClient)

    versions, source = asyncio.run(node_ops._list_remnanode_versions())

    assert source == "snapshot"
    assert "latest" in versions
    assert "sni-2.1.0" in versions


def test_remnanode_versions_return_snapshot_when_node_is_unreachable(monkeypatch):
    class UnreachableSSH:
        def __init__(self, *_args, **_kwargs):
            pass

        async def connect(self):
            raise OSError("node unavailable")

        async def close(self):
            pass

    async def fake_versions():
        return ["latest"], "snapshot"

    monkeypatch.setattr(node_ops, "SSHSession", UnreachableSSH)
    monkeypatch.setattr(node_ops, "_list_remnanode_versions", fake_versions)

    response = client.request(
        "GET", "/api/node/remnanode/versions", headers=_auth(),
        json={"ip": "1.2.3.4", "ssh_password": "pw"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "versions": ["latest"],
        "current": None,
        "source": "snapshot",
    }


# ── uninstall scripts: right teardown command per component, idempotent ──

def test_warp_uninstall_downs_and_removes_conf():
    s = _UNINSTALL_SCRIPTS["warp"](_req())
    assert "wg-quick down warp" in s
    assert "rm -f /etc/wireguard/warp.conf" in s


def test_haproxy_uninstall_purges():
    s = _UNINSTALL_SCRIPTS["haproxy"](_req(component="haproxy"))
    assert "apt-get purge -y haproxy" in s
    assert "systemctl stop haproxy" in s


def test_remnanode_uninstall_compose_down_and_rm():
    s = _UNINSTALL_SCRIPTS["remnanode"](_req(component="remnanode"))
    assert "docker compose down -v" in s
    assert "rm -rf /opt/remnanode" in s


def test_ssl_uninstall_interpolates_domain():
    s = _UNINSTALL_SCRIPTS["ssl"](_req(component="ssl", domain="node1.example.com"))
    assert "node1.example.com" in s
    assert "--remove" in s


def test_masking_uninstall_restores_default_page():
    s = _UNINSTALL_SCRIPTS["masking"](_req(component="masking"))
    assert "/var/www/html" in s
    assert "index.html" in s


def test_test_tools_uninstall_removes_tools_and_iperf_service():
    s = _UNINSTALL_SCRIPTS["test_tools"](_req(component="test_tools"))
    assert "apt-get remove -y iperf3 speedtest speedtest-cli" in s
    assert "rm -f /usr/local/bin/xray" in s
    assert "iperf3-server" in s
    assert "rm -f /etc/systemd/system/iperf3-server.service" in s


def test_all_uninstall_scripts_are_idempotent_guarded():
    # every uninstall must tolerate an absent component (|| true / 2>/dev/null)
    for c in typing.get_args(Component):
        s = _UNINSTALL_SCRIPTS[c](_req(component=c))
        assert "|| true" in s or "2>/dev/null" in s, c
