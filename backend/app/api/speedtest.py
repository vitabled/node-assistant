"""«Тесты скорости» — any-to-any iperf3 matrix + xray-link speed test (Ф2b, wave1).

Two synchronous endpoints (creds-per-request, like /api/stats/node-speedtest):

  POST /api/speedtest/pair  — iperf3 between two resources A and B. A is always
      the iperf3 CLIENT (SSH → `iperf3 -c`). B is the receiver: a *testserver*
      already runs a permanent `iperf3 -s`, so we connect straight to
      `B.ip:B.iperf_port`; a *node/panel* B gets an EPHEMERAL `iperf3 -s` on a
      free port + a UFW allow from A's IP — torn down in the finally block
      (idempotent) once the port is known. If the SSH to B fails AFTER the server
      starts but BEFORE its port is read back, the finally block can't target it;
      that residue is bounded by a `timeout 300` self-kill on the server itself
      (the port frees; the narrow UFW rule, scoped to A's IP, may linger).

  POST /api/speedtest/xray  — speed through an xray share-link's tunnel, measured
      from a chosen source (node/panel). The link is validated BEFORE any SSH
      and is NEVER logged/echoed (it can carry credentials).

  GET  /api/speedtest/history — recent pair + xray runs (speedtest_store).

The iperf/ping/traceroute/xray output parsers are reused from api.stats (Ф2) —
one source of truth. Every benchmark script runs SILENTLY over get_script_output
(stdin, not argv) so the xray config's creds never reach the remote process list.
"""

from __future__ import annotations

import ipaddress
import re
import shlex
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.api.stats import _parse_iperf, _parse_ping, _parse_traceroute, _parse_xray
from app.services import accounts, speedtest_store, test_tools
from app.services.ssh_manager import SSHSession

router = APIRouter(prefix="/api/speedtest")


def _validate_ip(v: str) -> str:
    # Normalise to the canonical form so the A==B guard can't be bypassed by two
    # different spellings of the same address (e.g. ::1 vs 0:0:0:0:0:0:0:1).
    try:
        return str(ipaddress.ip_address(v.strip()))
    except ValueError:
        raise ValueError("Некорректный IP-адрес")


class Endpoint(BaseModel):
    kind: str = "node"  # 'node' | 'panel' (SSH client/server) | 'testserver' (receiver)
    ip: str
    ssh_user: str = "root"
    ssh_password: str = ""
    ssh_port: int = Field(22, ge=1, le=65535)
    iperf_port: int = Field(5201, ge=1, le=65535)  # receiver port for a testserver B

    _ip = field_validator("ip")(_validate_ip)


class PairRequest(BaseModel):
    a: Endpoint
    b: Endpoint
    # Cumulative metric levels: 1=iperf throughput, 2=+ping/jitter, 3=+traceroute.
    metrics: list[int] = [1]


class XrayRequest(BaseModel):
    source: Endpoint
    xray_link: str  # vless/trojan/vmess/ss share-link — validated, never logged
    metrics: list[int] = [1]


# ── ephemeral iperf3 server scripts (node/panel B side) ────────


def _ensure_iperf() -> str:
    """One-line, idempotent iperf3 presence guard (lazy-install if missing)."""
    return (
        "command -v iperf3 >/dev/null 2>&1 || "
        "{ export DEBIAN_FRONTEND=noninteractive; apt-get install -y -qq iperf3 "
        "|| { apt-get update -qq; apt-get install -y -qq iperf3; }; }\n"
    )


def _server_up_script(client_ip: str) -> str:
    """Start an ephemeral `iperf3 -s` on the first free port (5201..5210), open
    UFW to the client IP only, and print `IPERF_PORT=<port>` (or `=0` if none
    free). The pid is stored so cleanup is deterministic."""
    cip = shlex.quote(client_ip)
    return (
        _ensure_iperf()
        + f"""\
set -u
PORT=0
for p in 5201 5202 5203 5204 5205 5206 5207 5208 5209 5210; do
    if ! ss -Hltn 2>/dev/null | grep -q ":$p "; then PORT=$p; break; fi
done
if [ "$PORT" = 0 ]; then echo "IPERF_PORT=0"; exit 0; fi
if command -v ufw >/dev/null 2>&1; then
    ufw allow from {cip} to any port $PORT proto tcp comment 'iperf3-pair' >/dev/null 2>&1 || true
fi
# `timeout 300` is a backstop: even if the client never connects (so `-1`
# never fires) AND the cleanup SSH later fails, the server self-terminates and
# frees the port instead of lingering forever.
nohup timeout 300 iperf3 -s -p $PORT -1 >/tmp/iperf3-pair-$PORT.log 2>&1 &
echo $! > /tmp/iperf3-pair-$PORT.pid
sleep 1
# Verify the bind actually took (TOCTOU: the port could have been grabbed
# between the `ss` scan and this start). If not listening → report 0 so the
# caller degrades to a warning instead of trusting a server that never came up.
if ss -Hltn 2>/dev/null | grep -q ":$PORT "; then
    echo "IPERF_PORT=$PORT"
else
    kill "$(cat /tmp/iperf3-pair-$PORT.pid 2>/dev/null)" 2>/dev/null || true
    rm -f /tmp/iperf3-pair-$PORT.pid
    ufw delete allow from {cip} to any port $PORT proto tcp 2>/dev/null || true
    echo "IPERF_PORT=0"
fi
"""
    )


def _server_down_script(port: int, client_ip: str) -> str:
    """Kill the ephemeral iperf3 server and drop its UFW rule — idempotent, so a
    double cleanup (or a run where the server never started) is harmless."""
    cip = shlex.quote(client_ip)
    p = int(port)
    return f"""\
if [ -f /tmp/iperf3-pair-{p}.pid ]; then
    kill "$(cat /tmp/iperf3-pair-{p}.pid)" 2>/dev/null || true
    rm -f /tmp/iperf3-pair-{p}.pid
fi
pkill -f "iperf3 -s -p {p}" 2>/dev/null || true
if command -v ufw >/dev/null 2>&1; then
    ufw delete allow from {cip} to any port {p} proto tcp 2>/dev/null || true
fi
rm -f /tmp/iperf3-pair-{p}.log
echo "IPERF_CLEANUP_DONE"
"""


def _parse_iperf_port(out: str) -> Optional[int]:
    m = re.search(r"IPERF_PORT=(\d+)", out or "")
    if not m:
        return None
    p = int(m.group(1))
    return p if 1 <= p <= 65535 else None


# In-flight pair/xray runs keyed by (account_id, resource_key): a run saturates
# the uplink for minutes, so a concurrent identical run is rejected (409).
_INFLIGHT: set[tuple[str, str]] = set()


@router.post("/pair")
async def pair(req: PairRequest) -> dict:
    account_id = accounts.current_account.get() or ""
    a, b = req.a, req.b

    if a.ip == b.ip:
        raise HTTPException(400, "Стороны A и B не могут совпадать")
    if not a.ssh_password:
        raise HTTPException(400, "Для стороны A нужны SSH-данные (она — клиент iperf3)")
    if b.kind != "testserver" and not b.ssh_password:
        raise HTTPException(400, "Для стороны B (ноды/панели) нужны SSH-данные")

    metrics = {m for m in req.metrics if m in (1, 2, 3)} or {1}
    with_ping, with_tr = 2 in metrics, 3 in metrics
    resource_key = f"{a.ip}→{b.ip}"
    row: dict = {"resource_key": resource_key, "kind": "pair"}
    warnings: list[str] = []

    key = (account_id, resource_key)
    if key in _INFLIGHT:
        raise HTTPException(409, "Тест этой пары уже выполняется")
    _INFLIGHT.add(key)

    ssh_a = SSHSession(a.ip, a.ssh_port, a.ssh_user, a.ssh_password)
    ssh_b: Optional[SSHSession] = None
    b_port: Optional[int] = None
    b_ephemeral = False
    try:
        # 1. Bring up the receiver on B.
        if b.kind == "testserver":
            b_port = b.iperf_port  # permanent iperf3 service — connect directly
        else:
            ssh_b = SSHSession(b.ip, b.ssh_port, b.ssh_user, b.ssh_password)
            try:
                await ssh_b.connect(timeout=10)
                out = await ssh_b.get_script_output(
                    _server_up_script(a.ip), timeout=300
                )
                b_port = _parse_iperf_port(out)
                if b_port:
                    b_ephemeral = True
                else:
                    warnings.append(
                        "Не удалось запустить временный iperf3-сервер на стороне B "
                        "(нет свободного порта)"
                    )
            except Exception as exc:
                warnings.append(f"Сторона B недоступна: {str(exc)[:150]}")

        # 2. Run the client on A → B.
        if b_port:
            try:
                await ssh_a.connect(timeout=10)
                script = _ensure_iperf() + test_tools.iperf_client_script(
                    b.ip, b_port, with_ping=with_ping, with_traceroute=with_tr
                )
                budget = 60 + (60 if with_ping else 0) + (60 if with_tr else 0)
                out = await ssh_a.get_script_output(script, timeout=budget)
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
            except Exception as exc:
                warnings.append(f"Сторона A недоступна: {str(exc)[:150]}")
    finally:
        # 3. Tear down the ephemeral server + UFW rule — ALWAYS, even on error.
        if ssh_b is not None:
            if b_ephemeral and b_port:
                try:
                    await ssh_b.get_script_output(
                        _server_down_script(b_port, a.ip), timeout=60
                    )
                except Exception:
                    pass
            await ssh_b.close()
        await ssh_a.close()
        _INFLIGHT.discard(key)

    await speedtest_store.record_run(account_id, row)
    hist = await speedtest_store.history_by_kind(account_id, ("pair", "xray"))
    return {"current": row, "history": hist, "warnings": warnings}


@router.post("/xray")
async def xray(req: XrayRequest) -> dict:
    account_id = accounts.current_account.get() or ""
    src = req.source

    if not src.ssh_password:
        raise HTTPException(400, "Для источника нужны SSH-данные")

    # Validate the link BEFORE any SSH. Fixed 422; the raw link is never logged
    # nor echoed into the detail (it can carry credentials).
    try:
        xray_script = test_tools.xray_link_speedtest_script(req.xray_link.strip())
    except ValueError:
        raise HTTPException(
            422, "Некорректная xray-ссылка (ожидается vless/trojan/vmess/ss)"
        )

    row: dict = {"resource_key": src.ip, "kind": "xray"}
    warnings: list[str] = []

    key = (account_id, f"xray:{src.ip}")
    if key in _INFLIGHT:
        raise HTTPException(409, "Xray-тест этого источника уже выполняется")
    _INFLIGHT.add(key)

    ssh = SSHSession(src.ip, src.ssh_port, src.ssh_user, src.ssh_password)
    try:
        try:
            await ssh.connect(timeout=10)
        except Exception as exc:
            raise HTTPException(
                502,
                f"Не удалось подключиться к источнику {src.ip}:{src.ssh_port}: "
                f"{str(exc)[:150]}",
            )

        # Lazy-install the test tools when xray-core is missing (idempotent).
        try:
            have = await ssh.get_output(
                "[ -x /usr/local/bin/xray ] && echo yes || echo no"
            )
            if have.strip() != "yes":
                await ssh.get_script_output(
                    test_tools.test_tools_install_script(), timeout=600
                )
        except Exception:
            warnings.append("Не удалось проверить/доустановить xray-инструменты")

        try:
            out = await ssh.get_script_output(xray_script, timeout=240)
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
    hist = await speedtest_store.history_by_kind(account_id, ("pair", "xray"))
    return {"current": row, "history": hist, "warnings": warnings}


@router.get("/history")
async def history(limit: int = 30) -> dict:
    """Recent pair + xray runs (newest first) — read-only, no SSH."""
    account_id = accounts.current_account.get() or ""
    limit = max(1, min(limit, 100))
    return {
        "history": await speedtest_store.history_by_kind(
            account_id, ("pair", "xray"), limit
        )
    }
