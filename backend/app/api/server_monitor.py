"""«Server uptime» monitor — a simple by-IP availability monitor (Plan A Ф2).

Independent of the xray-checker: tracks servers (manual + deployed-node auto-sync)
and probes each by TCP connect (+ ICMP fallback), recording up/slow/down samples.
Serves a status-page payload in the SAME shape as `/api/checker/statuspage` so the
dashboard can reuse the status-page components. `monitor_loop` is a lifespan
background task (mirrors `poller_loop`/`collector_loop`): per-account, resilient,
explicit account_id (no request context).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import sys
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.services import accounts
from app.services import net_guard
from app.services import storage
from app.services import subscription_import
from app.services import worker_lease
from app.services import server_monitor_store as store

router = APIRouter(prefix="/api/server-monitor")
log = logging.getLogger("server_monitor")

_MONITOR_INTERVAL = 60  # seconds between probe sweeps
_IPV4_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")


def _valid_ipv4(v: str) -> bool:
    return bool(_IPV4_RE.fullmatch(v.strip())) and all(0 <= int(p) <= 255 for p in v.strip().split("."))


# ── request models ─────────────────────────────────────────────

class ServerCreate(BaseModel):
    name: str = ""
    country: str = ""
    ip: str
    port: int = Field(default=443, ge=1, le=65535)
    note: str = ""

    @field_validator("ip")
    @classmethod
    def _ip(cls, v: str) -> str:
        # Operator-owned servers may be in private ranges — validate FORMAT only
        # (not public-only; this isn't a user-supplied URL, so no SSRF concern).
        if not _valid_ipv4(v):
            raise ValueError("Invalid IPv4 address")
        return v.strip()


class ServerUpdate(BaseModel):
    name: Optional[str] = None
    country: Optional[str] = None
    ip: Optional[str] = None
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    note: Optional[str] = None
    # Скрытие — единственный способ убрать с глаз deployed-строку, поэтому оно
    # идёт мимо ограничения `source='manual'` остальных полей.
    hidden: Optional[bool] = None

    @field_validator("ip")
    @classmethod
    def _ip(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not _valid_ipv4(v):
            raise ValueError("Invalid IPv4 address")
        return v.strip()


class DeployedServer(BaseModel):
    name: str = ""
    country: str = ""
    ip: str
    port: int = 443


class SubscriptionImport(BaseModel):
    """Import nodes from one of the account's subscriptions, or a one-off URL."""
    subscription_id: str = ""
    url: str = ""
    dry_run: bool = True


# ── server registry (CRUD) ─────────────────────────────────────

@router.get("/servers")
async def list_servers() -> list[dict[str, Any]]:
    return await store.list_servers()


@router.post("/servers", status_code=201)
async def create_server(body: ServerCreate) -> dict[str, Any]:
    return await store.add_server(body.name, body.country, body.ip, body.port, body.note, "manual")


@router.patch("/servers/{server_id}")
async def patch_server(server_id: str, body: ServerUpdate) -> dict[str, Any]:
    fields = body.model_dump(exclude_none=True)
    # `hidden` идёт своим путём: `update_server` ограничен source='manual', а
    # скрывать нужно в первую очередь deployed-строки, которые иначе с глаз не
    # убрать вообще.
    hidden = fields.pop("hidden", None)
    updated = None
    if hidden is not None:
        updated = await store.set_hidden(server_id, hidden)
    if fields:
        updated = await store.update_server(server_id, fields)
    if updated is None:
        raise HTTPException(404, "Сервер не найден")
    return updated


@router.delete("/servers/{server_id}", status_code=204)
async def delete_server(server_id: str):
    if not await store.delete_server(server_id):
        raise HTTPException(404, "Сервер не найден")


@router.post("/servers/sync-deployed")
async def sync_deployed(body: list[DeployedServer]) -> dict[str, Any]:
    """Upsert deployed nodes (from the browser's deploy_jobs) as source='deployed'
    servers; drop deployed rows no longer present. Manual servers untouched."""
    items = [b.model_dump() for b in body if _valid_ipv4(b.ip)]
    count = await store.sync_deployed(items)
    return {"ok": True, "synced": count}


# ── import from a subscription (Wave-7 Plan B) ─────────────────

_FETCH_TIMEOUT = 15
_MAX_SUB_BYTES = 4 * 1024 * 1024   # same cap the aggregator uses
_RESOLVE_LIMIT = 16                # concurrent DNS lookups
_RESOLVE_TIMEOUT = 3               # per-host DNS resolve cap


async def _fetch_subscription(url: str) -> str:
    if not net_guard.is_safe_url(url):
        raise HTTPException(400, "URL подписки не разрешён: нужен http(s) с публичным хостом")
    try:
        # STREAM with a hard cap: `c.get()` buffers the whole body into RAM before
        # any slice, so a multi-GB response OOMs the shared backend. Read chunks,
        # stop the moment we exceed the cap. (Wave-7 review, server_monitor:152.)
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT, follow_redirects=False) as c:
            async with c.stream("GET", url) as r:
                r.raise_for_status()
                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if len(buf) > _MAX_SUB_BYTES:
                        raise HTTPException(413, "Подписка превышает лимит размера")
                return bytes(buf).decode("utf-8", "replace")
    except HTTPException:
        raise
    except Exception:
        # Never echo the URL — it is the subscription secret.
        raise HTTPException(502, "Не удалось загрузить подписку")


async def _resolve(host: str, sem: asyncio.Semaphore) -> str:
    """host → IPv4, or "" when it doesn't resolve.

    ⚠️ `getaddrinfo` runs on the default ThreadPoolExecutor shared with every
    `asyncio.to_thread` in the app; a blackhole-DNS host would pin a thread until
    the system resolver times out. Bound it so a hostile subscription can't
    starve the pool. (Wave-7 review, server_monitor:165.)"""
    async with sem:
        try:
            loop = asyncio.get_event_loop()
            infos = await asyncio.wait_for(
                loop.getaddrinfo(host, None, family=2),  # AF_INET
                timeout=_RESOLVE_TIMEOUT,
            )
            return infos[0][4][0] if infos else ""
        except Exception:
            return ""


@router.post("/import/subscription")
async def import_from_subscription(body: SubscriptionImport) -> dict[str, Any]:
    """Preview (`dry_run`) or import a subscription's nodes as monitored servers.

    Links are fetched and parsed SERVER-side: they carry credentials, and a
    browser fetch would hit CORS anyway. Hosts are resolved to IPv4 here because
    `servers.ip` is an address — the original hostname is kept in `note` so the
    operator can see where a row came from.
    """
    url = (body.url or "").strip()
    if not url:
        subs = storage.load_subscriptions()
        sub = next((s for s in subs if s.get("id") == body.subscription_id), None)
        if sub is None:
            raise HTTPException(404, "Подписка не найдена")
        url = (sub.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "У подписки нет URL")

    links = subscription_import.decode_subscription(await _fetch_subscription(url))
    candidates = [c for c in (subscription_import.link_to_candidate(l) for l in links) if c]

    # Dedup within the subscription first: one host usually appears several times
    # with different transports, and monitoring probes a host, not an inbound.
    seen: set[tuple[str, int]] = set()
    uniq = []
    for c in candidates:
        key = (c["host"], c["port"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)

    sem = asyncio.Semaphore(_RESOLVE_LIMIT)
    ips = await asyncio.gather(*(_resolve(c["host"], sem) for c in uniq))

    existing = {(s["ip"], int(s.get("port") or 0)) for s in await store.list_servers()}
    rows: list[dict[str, Any]] = []
    for c, ip in zip(uniq, ips):
        if not ip:
            status = "unresolved"
        elif (ip, c["port"]) in existing:
            status = "duplicate"
        else:
            status = "new"
        rows.append({**c, "ip": ip, "status": status})

    imported = 0
    if not body.dry_run:
        for r in rows:
            if r["status"] != "new":
                continue
            # source='manual': a dedicated source would be un-editable and
            # un-deletable (update_server is manual-only, and only 'deployed'
            # rows are re-synced) — the very dead end Wave 6 had to work around.
            await store.add_server(r["name"], r["country"], r["ip"], r["port"],
                                   f"из подписки · {r['host']}", "manual")
            imported += 1

    return {
        "total": len(links), "candidates": rows, "imported": imported,
        "dry_run": body.dry_run,
    }


# ── status page (same shape as /api/checker/statuspage) ─────────

@router.get("/statuspage")
async def statuspage(ticks: int = 30) -> dict[str, Any]:
    ticks = max(10, min(ticks, 90))
    servers = await store.list_servers()
    latest, bars, up30 = await asyncio.gather(
        store.get_latest(), store.get_bars(ticks), store.get_uptime_30d()
    )
    nodes = []
    for s in servers:
        l = latest.get(s["id"], {})
        online = bool(l.get("online"))
        nodes.append({
            "stableId":  s["id"],
            "name":      s["name"] or s["ip"],
            "groupName": s["country"] or "",
            "protocol":  "",
            "online":    online,
            "latencyMs": l.get("latency_ms", -1) if online else -1,
            "uptime30d": up30["per_node"].get(s["id"]),
            "bars":      bars.get(s["id"], []),
            # extra fields for the manual-server UI (edit/delete + labels)
            "source":  s["source"],
            "ip":      s["ip"],
            "port":    s["port"],
            "country": s["country"],
            "note":    s["note"],
            "hidden":  s.get("hidden", False),
        })
    # Счётчики и баннер здоровья — ТОЛЬКО по нескрытым: иначе скрытый мёртвый
    # сервер продолжал бы красить дэшборд в «down». Скрытые едут в ответе с
    # флагом, чтобы UI показал их в отдельном блоке. Побочно это же даёт
    # подавление вкладке «Статистика» с cid='server-monitor'.
    shown = [n for n in nodes if not n["hidden"]]
    total = len(shown)
    online = sum(1 for n in shown if n["online"])
    gstate = "unknown" if total == 0 else "ok" if online == total else "down" if online == 0 else "partial"
    own = [n["uptime30d"] for n in shown if n["uptime30d"] is not None]
    return {
        "reachable": True,
        "nodes": nodes,
        "global": {
            "state": gstate,
            "uptime30d": round(sum(own) / len(own), 1) if own else None,
            "protocols": [],
            "total": total, "online": online, "offline": total - online,
        },
    }


@router.get("/incidents")
async def incidents(days: int = 7) -> dict[str, Any]:
    days = max(1, min(days, 30))
    return {"days": days, "incidents": await store.get_incidents(days)}


# ── probing ────────────────────────────────────────────────────

async def _icmp(ip: str) -> tuple[bool, int]:
    """System ping fallback (one packet). Cross-platform arg flavours."""
    if sys.platform.startswith("win"):
        cmd = ["ping", "-n", "1", "-w", "2000", ip]
    else:
        cmd = ["ping", "-c", "1", "-W", "2", ip]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=6)
    except Exception:
        return False, -1
    if proc.returncode != 0:
        return False, -1
    m = re.search(rb"time[=<]\s*([\d.]+)\s*ms", out)
    return True, int(float(m.group(1))) if m else store.SLOW_MS


async def _probe(ip: str, port: int) -> tuple[bool, int]:
    """Online = TCP connect on `port` (or fallback 22), else ICMP ping. Returns
    (online, latency_ms). latency_ms = TCP/ICMP round-trip; -1 when down."""
    loop = asyncio.get_event_loop()
    ports = [port] if port == 22 else [port, 22]
    for p in ports:
        t0 = loop.time()
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, p), timeout=5)
            rtt = int((loop.time() - t0) * 1000)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            return True, rtt
        except Exception:
            continue
    return await _icmp(ip)


# ── background poller ──────────────────────────────────────────

async def _monitor_account(account_id: str) -> None:
    servers = await store.list_servers(account_id)
    if not servers:
        return

    async def _one(s: dict) -> dict[str, Any]:
        online, rtt = await _probe(s["ip"], int(s.get("port") or store.DEFAULT_PORT))
        return {"server_id": s["id"], "online": online, "latency_ms": rtt}

    results = await asyncio.gather(*(_one(s) for s in servers), return_exceptions=True)
    samples = [r for r in results if isinstance(r, dict)]
    await store.record_samples(samples, account_id)


async def monitor_loop() -> None:
    """Runs for the app's lifetime; probes each account's tracked servers every
    `_MONITOR_INTERVAL`s. One account's failure never kills the loop.

    Gated on the `monitoring` lease (see services/worker_lease.py)."""
    while True:
        try:
            if not worker_lease.acquire(worker_lease.MONITORING):
                await asyncio.sleep(_MONITOR_INTERVAL)
                continue
            for acc in accounts.list_accounts():
                try:
                    await _monitor_account(acc["id"])
                except Exception as exc:
                    log.warning("server_monitor.account_failed",
                                extra={"account": acc["id"], "err": str(exc)[:200]})
        except Exception:
            pass
        await asyncio.sleep(_MONITOR_INTERVAL)
