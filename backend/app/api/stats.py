"""
Lightweight per-node security-stats endpoint.

Used by the DEPLOY CARDS (not the status page): a SUCCESS node's card polls this
every ~2.5 min, passing the node's SSH creds from its own localStorage. Creds are
used only within the request scope and never persisted (project rule: no SSH
passwords at rest — which is also why this is a per-request poll, not a
server-side background worker with stored credentials).

Parses Fail2Ban (SSH jail) + na-ctguard/TrafficGuard iptables rules.
"""
import asyncio
import json
import math
import re
from typing import Any, Optional

import re as _re

from fastapi import APIRouter
from pydantic import BaseModel, field_validator

from app.services.ssh_manager import SSHSession

# Hostname charset guard — `domain` is interpolated into a root SSH script in
# `_cert_expiry`, so reject anything outside [A-Za-z0-9.-] (empty is allowed:
# haproxy nodes have no cert and just skip the probe). Mirrors the Ф5 validator
# on DeployRequest.domain — NodeStatsRequest is a separate model that also
# reaches root bash.
_HOSTNAME_RE = _re.compile(
    r"^[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)*\.[A-Za-z]{2,}$"
)

router = APIRouter(prefix="/api/stats")

# fail2ban-client status sshd prints e.g. "   |- Currently banned:\t2" and
# "   |- Total banned:\t89". Capture both counters.
_RE_CURRENT = re.compile(r"Currently banned:\s*(\d+)")
_RE_TOTAL   = re.compile(r"Total banned:\s*(\d+)")


class NodeStatsRequest(BaseModel):
    ip: str
    ssh_port: int = 22
    ssh_user: str = "root"
    ssh_password: str
    domain: str = ""   # FQDN whose cert to probe; empty → skip cert check

    @field_validator("domain")
    @classmethod
    def _validate_domain(cls, v: str) -> str:
        if not v:
            return v
        if not _HOSTNAME_RE.match(v):
            raise ValueError("Invalid domain (hostname expected)")
        return v


class SecurityStats(BaseModel):
    fail2banActive: int = 0      # Currently banned (right now)
    fail2banTotal: int = 0       # Total banned (all-time)
    trafficGuardActive: int = 0  # active na-ctguard iptables rules


class TrafficBucket(BaseModel):
    rx: int = 0     # bytes received (incoming)
    tx: int = 0     # bytes sent (outgoing)
    total: int = 0  # rx + tx


class TrafficStats(BaseModel):
    today: TrafficBucket = TrafficBucket()
    week: TrafficBucket = TrafficBucket()
    month: TrafficBucket = TrafficBucket()


class CertInfo(BaseModel):
    daysLeft: int          # whole days until the cert's notAfter (may be negative = expired)
    notAfter: str          # raw openssl notAfter string, e.g. "Jul 15 12:00:00 2026 GMT"


class NodeStatsResponse(BaseModel):
    ip: str
    online: bool
    securityStats: Optional[SecurityStats] = None
    trafficStats: Optional[TrafficStats] = None
    certInfo: Optional[CertInfo] = None
    error: Optional[str] = None


@router.post("/node", response_model=NodeStatsResponse)
async def node_stats(req: NodeStatsRequest) -> NodeStatsResponse:
    ssh = SSHSession(req.ip, req.ssh_port, req.ssh_user, req.ssh_password)
    try:
        await ssh.connect(timeout=10)
        # One SSH session, read-only probes in parallel.
        f2b, tg, traffic, cert = await asyncio.gather(
            _fail2ban_sshd(ssh),
            _ctguard_rules(ssh),
            _vnstat_traffic(ssh),
            _cert_expiry(ssh, req.domain),
            return_exceptions=True,
        )
        active, total = f2b if isinstance(f2b, tuple) else (0, 0)
        return NodeStatsResponse(
            ip=req.ip,
            online=True,
            securityStats=SecurityStats(
                fail2banActive=active,
                fail2banTotal=total,
                trafficGuardActive=tg if isinstance(tg, int) else 0,
            ),
            trafficStats=traffic if isinstance(traffic, TrafficStats) else None,
            certInfo=cert if isinstance(cert, CertInfo) else None,
        )
    except Exception as exc:
        return NodeStatsResponse(ip=req.ip, online=False, error=str(exc)[:200])
    finally:
        await ssh.close()


async def _fail2ban_sshd(ssh: SSHSession) -> tuple[int, int]:
    """Return (currently_banned, total_banned) from the sshd jail (0,0 if absent)."""
    raw = await ssh.get_output("fail2ban-client status sshd 2>/dev/null")
    if not raw.strip():
        return (0, 0)  # fail2ban not installed / sshd jail missing
    cur = _RE_CURRENT.search(raw)
    tot = _RE_TOTAL.search(raw)
    return (int(cur.group(1)) if cur else 0, int(tot.group(1)) if tot else 0)


async def _ctguard_rules(ssh: SSHSession) -> int:
    """Count active na-ctguard / TrafficGuard iptables rules."""
    raw = await ssh.get_output(
        "iptables -L -n 2>/dev/null | grep -c 'na-ctguard' || echo 0"
    )
    raw = raw.strip()
    return int(raw) if raw.isdigit() else 0


async def _vnstat_traffic(ssh: SSHSession) -> TrafficStats:
    """Parse `vnstat --json` (native JSON — no regex needed).

    vnstat 2.x reports rx/tx in BYTES under interfaces[].traffic.{day,month}.
    We aggregate across all interfaces: today = latest day, week = last 7 days,
    month = latest month. Older key names (days/months) are handled too.
    """
    raw = await ssh.get_output("vnstat --json 2>/dev/null")
    if not raw.strip():
        return TrafficStats()  # vnstat not installed / no data yet
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return TrafficStats()

    t_rx = t_tx = w_rx = w_tx = m_rx = m_tx = 0
    for iface in data.get("interfaces", []) or []:
        traffic = iface.get("traffic", {}) or {}
        days = traffic.get("day") or traffic.get("days") or []
        months = traffic.get("month") or traffic.get("months") or []
        if days:
            t_rx += int(days[-1].get("rx", 0)); t_tx += int(days[-1].get("tx", 0))
            for e in days[-7:]:
                w_rx += int(e.get("rx", 0)); w_tx += int(e.get("tx", 0))
        if months:
            m_rx += int(months[-1].get("rx", 0)); m_tx += int(months[-1].get("tx", 0))

    def _b(rx: int, tx: int) -> TrafficBucket:
        return TrafficBucket(rx=rx, tx=tx, total=rx + tx)

    return TrafficStats(today=_b(t_rx, t_tx), week=_b(w_rx, w_tx), month=_b(m_rx, m_tx))


async def _cert_expiry(ssh: SSHSession, domain: str) -> Optional[CertInfo]:
    """Days-until-expiry for the node's installed cert. Computes on the node
    (openssl notAfter + date arithmetic) so no server-side clock/parse is needed.
    Returns None when domain is empty, the cert is missing, or openssl fails —
    the card then shows "неизвестно" rather than an error (degrade, never 500)."""
    domain = (domain or "").strip()
    if not domain:
        return None
    # Emit "<delta_seconds>|<notAfter>" or nothing (all failures → empty output).
    # We floor the days in Python (bash `/` truncates toward zero, which would
    # report a just-expired cert as "0 дн." instead of a negative day).
    script = (
        f'CERT="/etc/ssl/certs/{domain}_fullchain.pem"; '
        'if [ -s "$CERT" ]; then '
        'END=$(openssl x509 -enddate -noout -in "$CERT" 2>/dev/null | cut -d= -f2); '
        'if [ -n "$END" ]; then '
        'ETS=$(date -d "$END" +%s 2>/dev/null); NTS=$(date +%s); '
        'if [ -n "$ETS" ]; then echo "$(( ETS - NTS ))|$END"; fi; '
        'fi; fi'
    )
    raw = (await ssh.get_output(script)).strip()
    if not raw or "|" not in raw:
        return None
    delta_str, _, not_after = raw.partition("|")
    try:
        days = math.floor(int(delta_str.strip()) / 86400)
    except ValueError:
        return None
    return CertInfo(daysLeft=days, notAfter=not_after.strip())
