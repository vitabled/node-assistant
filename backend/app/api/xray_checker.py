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
            result["summary"] = summary
            result["reachable"] = True
        if isinstance(proxies, list):
            # Attach per-node uptime (24h availability) from the stored samples.
            uptime = await metrics_store.get_node_uptime(24)
            for p in proxies:
                u = uptime.get(p.get("stableId", ""))
                p["uptimePct"]  = u["uptime_pct"] if u else None
                p["lastSeen"]   = u["last_seen"] if u else None
            result["proxies"] = proxies
        if isinstance(info, dict):
            result["system"] = info
    except Exception as exc:  # pragma: no cover — defensive
        result["error"] = str(exc)[:200]
    return result


@router.get("/history")
async def checker_history(hours: int = 24) -> dict[str, Any]:
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
    result.update({
        "reachable": True,
        "global": {
            "state": gstate,
            "uptime30d": up30["global"],
            "protocols": protocols,
            "total": total, "online": online, "offline": total - online,
        },
        "nodes": nodes,
    })
    return result


@router.get("/incidents")
async def checker_incidents(days: int = 7) -> dict[str, Any]:
    days = max(1, min(days, 30))
    return {"days": days, "incidents": await metrics_store.get_incidents(days)}


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
