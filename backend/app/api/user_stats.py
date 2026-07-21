"""User statistics: per-node load history + best-effort user migrations (Ф3).

Routes serve the per-account stats store; `collector_loop` is a lifespan
background task that snapshots Remnawave's per-node `usersOnline` (+ best-effort
top-users) into that store every 5 minutes. There is NO Remnawave endpoint for
"which user is on which node right now" — `usersOnline` is a per-node count
(reliable), and sessions/migrations are approximated from top-users membership.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

from app.services import accounts, storage
from app.services import user_stats_store as store
from app.services.remnawave_client import RemnavaveClient
from app.models.settings import AppSettings

router = APIRouter(prefix="/api/stats/users")
log = logging.getLogger("user_stats")

# How often the collector snapshots (seconds). Fixed — snapshots are cheap and
# the widgets show trends, not real-time.
_COLLECT_INTERVAL = 300
# Cap stored top-users per node (defence-in-depth vs. a misbehaving/huge panel).
_TOP_USERS_CAP = 20


@router.get("/node-load")
async def node_load(hours: int = 24) -> dict[str, Any]:
    return await store.node_load(max(1, min(hours, 720)))


@router.get("/top-users")
async def top_users(hours: int = 24) -> dict[str, Any]:
    return await store.top_users(max(1, min(hours, 720)))


@router.get("/migrations")
async def migrations(hours: int = 24) -> dict[str, Any]:
    return await store.migrations(max(1, min(hours, 720)))


# ── Widget layout (Wave-5 Plan G) — per-account dashboard config ──

_WIDGET_KINDS = {
    "node-load", "avg-per-node", "top-users", "migrations",
    "stable-nodes", "fast-nodes", "uptime-summary", "speedtest-history",
}
_MAX_WIDGETS = 40


class WidgetInstance(BaseModel):
    instance_id: str = Field(..., min_length=1, max_length=64)
    kind: str
    w: int = Field(1, ge=1, le=2)
    order: int = 0
    settings: dict = Field(default_factory=dict)

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in _WIDGET_KINDS:
            raise ValueError(f"неизвестный тип виджета: {v}")
        return v


class WidgetLayout(BaseModel):
    layout: list[WidgetInstance] = Field(default_factory=list, max_length=_MAX_WIDGETS)


@router.get("/widgets")
async def get_widgets() -> dict:
    """The account's stats-widget layout ({layout: []} → frontend seeds default 6)."""
    return {"layout": storage.load_stat_widgets().get("layout", [])}


@router.put("/widgets")
async def put_widgets(body: WidgetLayout) -> dict:
    data = {"layout": [w.model_dump() for w in body.layout]}
    storage.save_stat_widgets(data)
    return data


# ── Background collector ──────────────────────────────────────

async def _collect_account(account_id: str) -> None:
    cfg = AppSettings(**storage.load_settings(account_id)).remnawave
    if not cfg.panel_url or not cfg.api_token:
        return  # Remnawave not configured for this account — nothing to snapshot
    client = RemnavaveClient(cfg.panel_url, cfg.api_token)
    try:
        nodes = await client.get_nodes_metrics()
    except Exception as exc:
        log.warning("stats.collect.nodes_failed", extra={"account": account_id, "err": str(exc)[:200]})
        return
    if not nodes:
        return

    async def _fetch_node_top(node_uuid: str) -> tuple[str, list[dict]]:
        try:
            usage = await client.get_node_users_usage(node_uuid)
            users = usage.get("topUsers", []) if isinstance(usage, dict) else []
            return node_uuid, users[:_TOP_USERS_CAP]  # cap stored length
        except Exception as exc:
            log.info("stats.collect.node_users_failed",
                     extra={"account": account_id, "node": node_uuid, "err": str(exc)[:120]})
            return node_uuid, []

    # Fetch per-node top-users concurrently — one slow/unreachable node no longer
    # stalls the whole account's snapshot (return_exceptions keeps gather resilient).
    node_uuids = [n["nodeUuid"] for n in nodes if n.get("nodeUuid")]
    results = await asyncio.gather(*(_fetch_node_top(u) for u in node_uuids), return_exceptions=True)
    top: dict[str, list[dict]] = {}
    for r in results:
        if isinstance(r, Exception):
            continue
        u, users = r
        top[u] = users
    await store.record_snapshot(nodes, top, account_id)


async def collector_loop() -> None:
    """Runs for the app's lifetime; snapshots each account's Remnawave node load
    into the per-account user_stats store. One account's failure never kills the
    loop (mirrors xray_checker.poller_loop). No request context → explicit account_id."""
    while True:
        try:
            for acc in accounts.list_accounts():
                try:
                    await _collect_account(acc["id"])
                except Exception as exc:
                    log.warning("stats.collect.account_failed",
                                extra={"account": acc["id"], "err": str(exc)[:200]})
        except Exception:
            pass  # never let the loop die
        await asyncio.sleep(_COLLECT_INTERVAL)
