"""Ф7 — per-component node management endpoint + uninstall script builders."""
import typing

import pytest
from pydantic import ValidationError

from app.api.node_ops import (
    NodeOpRequest, Component, Action,
    _UNINSTALL_SCRIPTS, _COMPONENT_LABEL, _effective_port,
)


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


def test_all_uninstall_scripts_are_idempotent_guarded():
    # every uninstall must tolerate an absent component (|| true / 2>/dev/null)
    for c in typing.get_args(Component):
        s = _UNINSTALL_SCRIPTS[c](_req(component=c))
        assert "|| true" in s or "2>/dev/null" in s, c
