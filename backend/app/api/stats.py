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

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from app.services import accounts, speedtest_store, test_tools, testserver_registry
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
_RE_TOTAL = re.compile(r"Total banned:\s*(\d+)")


class NodeStatsRequest(BaseModel):
    ip: str
    ssh_port: int = 22
    ssh_user: str = "root"
    ssh_password: str
    domain: str = ""  # FQDN whose cert to probe; empty → skip cert check

    @field_validator("domain")
    @classmethod
    def _validate_domain(cls, v: str) -> str:
        if not v:
            return v
        if not _HOSTNAME_RE.match(v):
            raise ValueError("Invalid domain (hostname expected)")
        return v


class SecurityStats(BaseModel):
    fail2banActive: int = 0  # Currently banned (right now)
    fail2banTotal: int = 0  # Total banned (all-time)
    trafficGuardActive: int = 0  # active na-ctguard iptables rules


class TrafficBucket(BaseModel):
    rx: int = 0  # bytes received (incoming)
    tx: int = 0  # bytes sent (outgoing)
    total: int = 0  # rx + tx


class TrafficStats(BaseModel):
    today: TrafficBucket = TrafficBucket()
    week: TrafficBucket = TrafficBucket()
    month: TrafficBucket = TrafficBucket()


class CertInfo(BaseModel):
    daysLeft: int  # whole days until the cert's notAfter (may be negative = expired)
    notAfter: str  # raw openssl notAfter string, e.g. "Jul 15 12:00:00 2026 GMT"


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
            t_rx += int(days[-1].get("rx", 0))
            t_tx += int(days[-1].get("tx", 0))
            for e in days[-7:]:
                w_rx += int(e.get("rx", 0))
                w_tx += int(e.get("tx", 0))
        if months:
            m_rx += int(months[-1].get("rx", 0))
            m_tx += int(months[-1].get("tx", 0))

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
        "fi; fi"
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


# ══════════════════════════════════════════════════════════════
# Ф2 (wave1) — node speed-test probes + history
#
# POST /api/stats/node-speedtest — creds-per-request (same rule as /node): one
# SSH session runs characteristics + speedtest (+ iperf3 to a registered test
# server, + speed through an xray link). Every probe is individually best-effort:
# a failure leaves its fields null and adds a human warning — never a 500. The
# run is recorded in the per-account speedtest_store (no creds at rest).
#
# All benchmark scripts run SILENTLY via get_output (no Task log): the xray
# script embeds the parsed link config, which carries credentials.
# ══════════════════════════════════════════════════════════════


class NodeSpeedtestRequest(BaseModel):
    ip: str
    ssh_port: int = 22
    ssh_user: str = "root"
    ssh_password: str
    testserver_id: Optional[str] = None  # iperf3 target from the account's registry
    xray_link: Optional[str] = None  # vless/trojan/vmess/ss share-link (never logged)
    # Cumulative metric levels: 1=iperf throughput, 2=+ping/jitter, 3=+traceroute.
    metrics: list[int] = [1]


# Marker-tagged one-liner: nproc / lscpu model / RAM total MB / root-fs usage.
_CHAR_SCRIPT = (
    'echo "CHAR_NPROC=$(nproc 2>/dev/null)"; '
    "echo \"CHAR_MODEL=$(lscpu 2>/dev/null | grep 'Model name' | head -1 "
    "| cut -d: -f2- | sed 's/^[[:space:]]*//')\"; "
    "echo \"CHAR_RAM_MB=$(free -m 2>/dev/null | awk '/^Mem:/{print $2}')\"; "
    'echo "CHAR_DISK=$(df -h / 2>/dev/null '
    '| awk \'NR==2{print $2" "$3" "$5}\')"'
)


async def _run_quiet(ssh: SSHSession, script: str, timeout: float) -> str:
    """Run a multi-line bash script silently (no Task log — benchmark scripts may
    embed parsed xray-link credentials) with a hard timeout. The script is piped
    over stdin so its credentials never reach the remote process argv."""
    return await ssh.get_script_output(script, timeout=timeout)


def _extract_marker(out: str, start: str, end: str) -> Optional[str]:
    """Text between two marker lines, or None when either marker is absent."""
    if start not in out or end not in out:
        return None
    return out.split(start, 1)[1].split(end, 1)[0].strip()


def _parse_characteristics(
    out: str,
) -> tuple[Optional[str], Optional[int], Optional[str]]:
    """CHAR_* marker lines → (cpu «N × Model», ram_mb, disk «total · использовано used (use%)»)."""

    def grab(key: str) -> str:
        m = re.search(rf"^{key}=(.*)$", out or "", re.MULTILINE)
        return m.group(1).strip() if m else ""

    nproc, model = grab("CHAR_NPROC"), grab("CHAR_MODEL")
    cpu = None
    if nproc and model:
        cpu = f"{nproc} × {model}"
    elif model or nproc:
        cpu = model or f"{nproc} × CPU"

    ram_raw = grab("CHAR_RAM_MB")
    ram_mb = int(ram_raw) if ram_raw.isdigit() else None

    disk_raw = grab("CHAR_DISK")
    parts = disk_raw.split()
    if len(parts) >= 3:
        disk = f"{parts[0]} · использовано {parts[1]} ({parts[2]})"
    else:
        disk = disk_raw or None
    return cpu, ram_mb, disk


def _parse_speedtest(out: str) -> tuple[Optional[dict], Optional[str]]:
    """Marker-delimited speedtest JSON → ({st_down, st_up (Мбит/с), st_ping (мс)},
    warning). Both CLI shapes: Ookla `bandwidth` is BYTES/s (×8/1e6); the python
    speedtest-cli reports BITS/s (/1e6). Kind from SPEEDTEST_KIND, shape fallback."""
    out = out or ""
    if "SPEEDTEST_NONE" in out:
        return None, "speedtest не установлен на ноде (ни Ookla, ни python-версия)"
    body = _extract_marker(out, "SPEEDTEST_JSON_START", "SPEEDTEST_JSON_END")
    if not body:
        return None, "speedtest не вернул результат"
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None, "speedtest вернул невалидный JSON"
    kind_m = re.search(r"SPEEDTEST_KIND=(\w+)", out)
    kind = (
        kind_m.group(1)
        if kind_m
        else ("ookla" if isinstance(data.get("download"), dict) else "python")
    )
    try:
        if kind == "ookla":
            down = float(data["download"]["bandwidth"]) * 8 / 1e6
            up = float(data["upload"]["bandwidth"]) * 8 / 1e6
            ping = data.get("ping", {}).get("latency")
        else:
            down = float(data["download"]) / 1e6
            up = float(data["upload"]) / 1e6
            ping = data.get("ping")
        ping = float(ping) if ping is not None else None
    except (KeyError, TypeError, ValueError):
        return None, "speedtest вернул неожиданный формат JSON"
    return {
        "st_down": round(down, 2),
        "st_up": round(up, 2),
        "st_ping": round(ping, 1) if ping is not None else None,
    }, None


def _parse_iperf(out: str) -> tuple[Optional[float], Optional[str]]:
    """iperf3 -J output between markers → Мбит/с from end.sum_received.bits_per_second."""
    body = _extract_marker(out or "", "IPERF_JSON_START", "IPERF_JSON_END")
    if not body:
        return None, "iperf3 не вернул результат"
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None, "iperf3 вернул невалидный JSON"
    if isinstance(data, dict) and data.get("error"):
        return None, f"iperf3: {str(data['error'])[:160]}"
    try:
        bps = float(data["end"]["sum_received"]["bits_per_second"])
    except (KeyError, TypeError, ValueError):
        return None, "iperf3 вернул неожиданный формат JSON"
    return round(bps / 1e6, 2), None


# iputils prints `rtt min/avg/max/mdev = …`; busybox/mac use round-trip/stddev.
_RE_PING_RTT = re.compile(
    r"(?:rtt|round-trip) min/avg/max/(?:mdev|stddev) = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)"
)


def _parse_ping(out: str) -> tuple[Optional[float], Optional[float]]:
    """PING section → (avg_ms, mdev_ms). mdev doubles as the TCP-run jitter."""
    body = _extract_marker(out or "", "PING_START", "PING_END")
    m = _RE_PING_RTT.search(body or "")
    if not m:
        return None, None
    return float(m.group(2)), float(m.group(4))


def _parse_traceroute(out: str) -> Optional[str]:
    """Raw traceroute text between markers (stored/shown as-is)."""
    body = _extract_marker(out or "", "TRACEROUTE_START", "TRACEROUTE_END")
    return body or None


def _parse_xray(out: str) -> tuple[Optional[dict], Optional[str]]:
    """XRAY_* markers → speeds in Мбит/с + ping in мс. curl reports BYTES/s
    (×8/1e6) and seconds (×1000); `0`/missing values mean the tunnel failed."""
    out = out or ""

    def val(key: str) -> Optional[float]:
        m = re.search(rf"{key}=([\d.,]+)", out)
        if not m:
            return None
        try:
            # some curl builds print a comma decimal separator (locale)
            return float(m.group(1).replace(",", "."))
        except ValueError:
            return None

    down, up, ping = val("XRAY_DOWN"), val("XRAY_UP"), val("XRAY_PING")
    res: dict = {}
    if down:
        res["xray_down"] = round(down * 8 / 1e6, 2)
    if up:
        res["xray_up"] = round(up * 8 / 1e6, 2)
    if ping:
        res["xray_ping"] = round(ping * 1000, 1)
    if not res:
        return (
            None,
            "xray-тест не дал результата (туннель не поднялся или скорость нулевая)",
        )
    return res, None


# In-flight speedtests keyed by (account_id, ip): a speedtest saturates the
# node's uplink for minutes, so reject a concurrent run on the same node (the
# UI disables its button, but a direct re-POST / second tab would otherwise
# stack load — and two lazy-installs would collide on the dpkg lock).
_INFLIGHT: set[tuple[str, str]] = set()


@router.post("/node-speedtest")
async def node_speedtest(req: NodeSpeedtestRequest) -> dict:
    account_id = accounts.current_account.get() or ""
    warnings: list[str] = []

    # Validate the xray link BEFORE any SSH work. Fixed 422 message; the raw
    # link is never logged and never echoed into the detail (it can carry creds).
    xray_script: Optional[str] = None
    if (req.xray_link or "").strip():
        try:
            xray_script = test_tools.xray_link_speedtest_script(req.xray_link.strip())
        except ValueError:
            raise HTTPException(
                422, "Некорректная xray-ссылка (ожидается vless/trojan/vmess/ss)"
            )

    srv = None
    if req.testserver_id:
        srv = testserver_registry.get_server(req.testserver_id, account_id)
        if srv is None:
            raise HTTPException(404, "Тест-сервер не найден")

    metrics = {m for m in req.metrics if m in (1, 2, 3)} or {1}
    row: dict = {"resource_key": req.ip, "kind": "node"}

    key = (account_id, req.ip)
    if key in _INFLIGHT:
        raise HTTPException(409, "Тест этого сервера уже выполняется")
    _INFLIGHT.add(key)

    ssh = SSHSession(req.ip, req.ssh_port, req.ssh_user, req.ssh_password)
    try:
        try:
            await ssh.connect(timeout=10)
        except Exception as exc:
            raise HTTPException(
                502,
                f"Не удалось подключиться к серверу {req.ip}:{req.ssh_port}: {str(exc)[:200]}",
            )

        # 1. Lazy install — the deploy toggle may have been off / a foreign box.
        try:
            have = await asyncio.wait_for(
                ssh.get_output("command -v iperf3 2>/dev/null"), timeout=20
            )
            if not (have or "").strip():
                await _run_quiet(
                    ssh, test_tools.test_tools_install_script(), timeout=600
                )
        except Exception:
            warnings.append("Не удалось проверить/доустановить тест-инструменты")

        # 2. Characteristics (fast, read-only).
        try:
            out = await asyncio.wait_for(ssh.get_output(_CHAR_SCRIPT), timeout=30)
            cpu, ram_mb, disk = _parse_characteristics(out)
            row.update({"cpu": cpu, "ram_mb": ram_mb, "disk": disk})
        except Exception:
            warnings.append("Не удалось прочитать характеристики сервера")

        # 3. External-channel speedtest (Ookla / python fallback).
        try:
            out = await _run_quiet(ssh, test_tools.speedtest_run_script(), timeout=150)
            st, warn = _parse_speedtest(out)
            if st:
                row.update(st)
            if warn:
                warnings.append(warn)
        except Exception:
            warnings.append("speedtest не завершился (таймаут/ошибка SSH)")

        # 4. iperf3 to the chosen test server (+ ping/jitter, + traceroute).
        if srv is not None:
            with_ping = 2 in metrics
            with_tr = 3 in metrics
            script = test_tools.iperf_client_script(
                srv["ip"],
                srv["iperf_port"],
                with_ping=with_ping,
                with_traceroute=with_tr,
            )
            budget = 60 + (60 if with_ping else 0) + (60 if with_tr else 0)
            try:
                out = await _run_quiet(ssh, script, timeout=budget)
                mbps, warn = _parse_iperf(out)
                row["iperf_mbps"] = mbps
                if warn:
                    warnings.append(warn)
                if with_ping:
                    avg, mdev = _parse_ping(out)
                    row["ping_ms"] = avg
                    row["iperf_jitter"] = mdev  # TCP run: jitter from ping mdev
                if with_tr:
                    row["traceroute"] = _parse_traceroute(out)
            except Exception:
                warnings.append("iperf3-проба не завершилась (таймаут/ошибка SSH)")

        # 5. Speed through the xray tunnel — the script embeds only the PARSED
        #    config (with the link's creds), so it MUST stay out of any log.
        if xray_script is not None:
            try:
                out = await _run_quiet(ssh, xray_script, timeout=240)
                xr, warn = _parse_xray(out)
                if xr:
                    row.update(xr)
                if warn:
                    warnings.append(warn)
            except Exception:
                warnings.append("xray-тест не завершился (таймаут/ошибка SSH)")
    finally:
        await ssh.close()
        _INFLIGHT.discard(key)

    await speedtest_store.record_run(account_id, row)
    hist = await speedtest_store.history(account_id, req.ip)
    return {
        "current": hist[0] if hist else row,
        "history": hist,
        "warnings": warnings,
    }


@router.get("/node-speedtest/history")
async def node_speedtest_history(resource_key: str, limit: int = 20) -> dict:
    """Stored runs for a node (newest first) — read-only, no SSH; used by the
    deploy card on mount to show the last result."""
    account_id = accounts.current_account.get() or ""
    limit = max(1, min(limit, 100))
    return {"history": await speedtest_store.history(account_id, resource_key, limit)}
