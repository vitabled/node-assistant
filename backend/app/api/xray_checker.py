"""
API bridge + analytics endpoints for the xray-checker integration.

Routes (all under /api/checker):
  GET  /status        — container state + live summary (total/online/avg latency) + per-proxy list
  GET  /history       — time-series (avg latency + availability) for the graph
  GET  /logs          — tail of the checker's container logs
  POST /check         — force a deep (live) re-check of all proxies
  POST /update        — pull a new checker image and recreate (auto-update pipeline)
  POST /start         — (re)create + start the container from settings
  POST /stop          — stop the container

The background poller (`poller_loop`) samples the checker every `poll_interval`
seconds and appends to the SQLite metrics store so /history has data to plot.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException

from app.services import xray_checker as xc
from app.services import metrics_store
from app.services import storage
from app.services import accounts
from app.models.settings import AppSettings

router = APIRouter(prefix="/api/checker")


# ── per-account tag filtering (Ф9) ────────────────────────────
# The Ф8 aggregator tags each proxy's remark `<account_id>:<sub_id>|<orig>`, which
# the checker surfaces as the proxy `name`. We filter the shared checker's output
# to the ACTIVE account and strip the tag for display. Fallback: when NO proxy is
# tagged (single-subscription / bare-metal mode, aggregator not in use), show all
# — so the dashboard still works without the aggregator.

def _parse_tag(name: str) -> tuple[str, str]:
    """`<account>:<sub>|<orig>` → (account_id, orig_name); untagged → ("", name)."""
    head, sep, orig = name.partition("|")
    if sep and ":" in head:
        acc, _, _sub = head.partition(":")
        if acc:
            return acc, orig
    return "", name


def _filter_by_account(items: list[dict], account_id: str, name_key: str = "name") -> list[dict]:
    """Keep only items belonging to `account_id` (by the name tag) and strip the
    tag from the display name. If nothing is tagged, return items unchanged."""
    any_tagged = any(_parse_tag(str(i.get(name_key, "")))[0] for i in items)
    if not any_tagged:
        return items
    out = []
    for i in items:
        acc, orig = _parse_tag(str(i.get(name_key, "")))
        if acc == account_id:
            i = {**i, name_key: orig}
            out.append(i)
    return out


@router.get("/status")
async def checker_status() -> dict[str, Any]:
    """Container state + a live snapshot of the checker's summary + proxies."""
    state = await xc.container_state()
    result: dict[str, Any] = {"container": state, "reachable": False}
    if state != "running":
        return result
    try:
        summary, proxies, info = await asyncio.gather(
            xc.fetch_status(), xc.fetch_proxies(), xc.fetch_system_info(),
            return_exceptions=True,
        )
        if isinstance(summary, dict):
            result["reachable"] = True
        if isinstance(proxies, list):
            proxies = _filter_by_account(proxies, accounts.current_account.get() or "")
            # Attach per-node uptime (24h availability) from the stored samples.
            uptime = await metrics_store.get_node_uptime(24)
            for p in proxies:
                u = uptime.get(p.get("stableId", ""))
                p["uptimePct"]  = u["uptime_pct"] if u else None
                p["lastSeen"]   = u["last_seen"] if u else None
            result["proxies"] = proxies
            # Recompute the summary from THIS account's proxies — the checker's
            # own /api/v1/status is a cross-account aggregate (total/online across
            # all tenants) and must not be surfaced (same leak class as
            # statuspage's global uptime).
            online = [p for p in proxies if p.get("online")]
            lats = [p.get("latencyMs", -1) for p in online if p.get("latencyMs", -1) >= 0]
            result["summary"] = {
                "total": len(proxies),
                "online": len(online),
                "offline": len(proxies) - len(online),
                "avgLatencyMs": round(sum(lats) / len(lats)) if lats else 0,
            }
        if isinstance(info, dict):
            result["system"] = info
    except Exception as exc:  # pragma: no cover — defensive
        result["error"] = str(exc)[:200]
    return result


@router.get("/history")
async def checker_history(hours: int = 24) -> dict[str, Any]:
    # NOTE: this is a GLOBAL aggregate (avg latency/availability across ALL
    # accounts) — it carries no node names/ids so it can't leak identifiable
    # per-account data, and the dashboard doesn't render it (uses statuspage +
    # incidents, both per-account filtered). Per-account history would need a
    # name-tag column in the bucketed SQL; deferred.
    hours = max(1, min(hours, 168))
    return await metrics_store.get_history(hours)


@router.get("/statuspage")
async def checker_statuspage(ticks: int = 30) -> dict[str, Any]:
    """Aggregate everything the status-page UI needs: a global health summary +
    per-node rows with their uptime-bar tick history and 30-day uptime."""
    ticks = max(10, min(ticks, 90))
    state = await xc.container_state()
    result: dict[str, Any] = {"container": state, "reachable": False, "nodes": [], "global": {}}
    if state != "running":
        return result
    try:
        proxies = await xc.fetch_proxies()
    except Exception as exc:
        result["error"] = str(exc)[:200]
        return result

    # Filter to the active account (tag) + strip the tag from display names, so
    # global counts below are also per-account.
    proxies = _filter_by_account(proxies, accounts.current_account.get() or "")

    bars, up30 = await asyncio.gather(
        metrics_store.get_bars(ticks), metrics_store.get_uptime_30d()
    )
    protocols = sorted({p.get("protocol", "") for p in proxies if p.get("protocol")})
    total = len(proxies)
    online = sum(1 for p in proxies if p.get("online"))

    # Global health state.
    if total == 0:
        gstate = "unknown"
    elif online == total:
        gstate = "ok"
    elif online == 0:
        gstate = "down"
    else:
        gstate = "partial"

    nodes = [
        {
            "stableId":  p.get("stableId", ""),
            "name":      p.get("name", ""),
            "groupName": p.get("groupName", "") or "",
            "protocol":  p.get("protocol", "") or "",
            "online":    bool(p.get("online")),
            "latencyMs": p.get("latencyMs", -1),
            "uptime30d": up30["per_node"].get(p.get("stableId", "")),
            "bars":      bars.get(p.get("stableId", ""), []),
        }
        for p in proxies
    ]
    # Global 30d uptime scoped to THIS account's nodes (up30["global"] is the
    # whole shared DB across all accounts — don't leak that coarse aggregate).
    own_up = [n["uptime30d"] for n in nodes if n["uptime30d"] is not None]
    global_uptime = round(sum(own_up) / len(own_up), 1) if own_up else None

    result.update({
        "reachable": True,
        "global": {
            "state": gstate,
            "uptime30d": global_uptime,
            "protocols": protocols,
            "total": total, "online": online, "offline": total - online,
        },
        "nodes": nodes,
    })
    return result


@router.get("/incidents")
async def checker_incidents(days: int = 7) -> dict[str, Any]:
    days = max(1, min(days, 30))
    incidents = await metrics_store.get_incidents(days)
    incidents = _filter_by_account(incidents, accounts.current_account.get() or "")
    return {"days": days, "incidents": incidents}


@router.get("/logs")
async def checker_logs(tail: int = 200) -> dict[str, Any]:
    return {"logs": await xc.get_logs(tail=max(1, min(tail, 2000)))}


@router.post("/check")
async def checker_deep_check() -> dict[str, Any]:
    """Force a live re-check, then immediately sample so the UI updates at once."""
    try:
        res = await xc.trigger_deep_check()
    except Exception as exc:
        raise HTTPException(502, f"Не удалось запустить проверку: {exc}")
    await _sample_once()  # persist a fresh sample right away
    return res


@router.post("/update")
async def checker_update() -> dict[str, Any]:
    try:
        return await xc.update()
    except xc.CheckerError as exc:
        raise HTTPException(502, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"Обновление не удалось: {exc}")


@router.post("/start")
async def checker_start() -> dict[str, Any]:
    try:
        await xc.start()
        return {"ok": True}
    except xc.CheckerError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"Запуск не удался: {exc}")


@router.post("/stop")
async def checker_stop() -> dict[str, Any]:
    await xc.stop()
    return {"ok": True}


# ── Background poller ─────────────────────────────────────────

async def _sample_once() -> int:
    """Scrape the checker's proxies and append a sample row. Returns proxy count."""
    try:
        proxies = await xc.fetch_proxies()
    except Exception:
        return 0
    await metrics_store.record_samples(proxies)
    return len(proxies)


async def poller_loop() -> None:
    """Runs for the app's lifetime; samples the (shared) checker on its poll
    interval. The xray-checker container + metrics store are global; each
    account's xray config is per-account, so we scan all accounts and sample
    whenever ANY account has the checker enabled and the container is running.
    Interval = the smallest enabled poll_interval (min 15s)."""
    while True:
        interval = 60
        try:
            enabled_intervals = []
            for acc in accounts.list_accounts():
                try:
                    cfg = AppSettings(**storage.load_settings(acc["id"])).xray_checker
                except Exception:
                    continue
                if cfg.enabled:
                    enabled_intervals.append(max(15, cfg.poll_interval))
            if enabled_intervals:
                interval = min(enabled_intervals)
                if await xc.container_state() == "running":
                    await _sample_once()
        except Exception:
            interval = 60  # back off on unexpected errors
        await asyncio.sleep(interval)
