"""
Infra-billing API — merges Remnawave's InfraBillingController with our local
metadata store (balances/cost/currency) and computes analytics + burn-rate.

All routes are under /api/infra-billing and are OUR backend endpoints (the SPA
calls these; we in turn call Remnawave + the local store). Remnawave errors are
surfaced as HTTP errors with a clear `detail` → shown as toasts on the client.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.services import storage
from app.services import infra_billing_store as store
from app.services import infra_notify
from app.models.settings import AppSettings
from app.services.remnawave_client import RemnavaveClient, RemnavaveError

router = APIRouter(prefix="/api/infra-billing")


def _client() -> RemnavaveClient:
    cfg = AppSettings(**storage.load_settings()).remnawave
    if not cfg.panel_url or not cfg.api_token:
        raise HTTPException(400, "Remnawave не настроен — укажите URL и токен в Настройках.")
    return RemnavaveClient(cfg.panel_url, cfg.api_token)


def _wrap_rw(exc: Exception) -> HTTPException:
    if isinstance(exc, RemnavaveError):
        return HTTPException(exc.status or 502, exc.detail)
    return HTTPException(502, str(exc))


# ── Request models (client + server validation) ───────────────

class ProviderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    favicon_link: str = ""
    login_url: str = ""
    balance: float = Field(default=0, ge=0)
    currency: str = Field(default="RUB", min_length=1, max_length=8)
    low_balance_threshold: float = Field(default=0, ge=0)


class ProviderUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    favicon_link: Optional[str] = None
    login_url: Optional[str] = None
    balance: Optional[float] = Field(default=None, ge=0)
    currency: Optional[str] = None
    low_balance_threshold: Optional[float] = Field(default=None, ge=0)


class BillingNodeCreate(BaseModel):
    provider_uuid: str
    node_uuid: str
    name: str = Field(..., min_length=1)
    next_billing_at: str  # ISO date-time
    monthly_cost: float = Field(default=0, ge=0)


class BillingNodeUpdate(BaseModel):
    uuids: list[str] = Field(..., min_length=1)
    next_billing_at: str
    monthly_cost: Optional[float] = Field(default=None, ge=0)


class HistoryCreate(BaseModel):
    provider_uuid: str
    amount: float
    billed_at: str

    @field_validator("amount")
    @classmethod
    def _amt(cls, v: float) -> float:
        if v == 0:
            raise ValueError("amount не может быть 0")
        return v


# ── Providers ─────────────────────────────────────────────────

@router.get("/providers")
async def list_providers():
    """Remnawave providers merged with local balance/currency/threshold + node count."""
    client = _client()
    try:
        providers = await client.infra_list_providers()
    except Exception as exc:
        raise _wrap_rw(exc)
    meta = await store.provider_meta_all()
    out = []
    for p in providers:
        m = meta.get(p["uuid"], {})
        out.append({
            "uuid": p["uuid"],
            "name": p["name"],
            "faviconLink": p.get("faviconLink", ""),
            "loginUrl": p.get("loginUrl", ""),
            "nodeCount": len(p.get("billingNodes", []) or []),
            "balance": m.get("balance", 0),
            "currency": m.get("currency", "RUB"),
            "lowBalanceThreshold": m.get("low_balance_threshold", 0),
        })
    return out


@router.post("/providers", status_code=201)
async def create_provider(body: ProviderCreate):
    client = _client()
    try:
        created = await client.infra_create_provider(
            name=body.name, favicon_link=body.favicon_link, login_url=body.login_url,
        )
    except Exception as exc:
        raise _wrap_rw(exc)
    await store.upsert_provider_meta(
        created["uuid"], balance=body.balance, currency=body.currency,
        threshold=body.low_balance_threshold,
    )
    return {"ok": True, "uuid": created["uuid"]}


@router.patch("/providers/{uuid}")
async def update_provider(uuid: str, body: ProviderUpdate):
    client = _client()
    # Only push identity fields to Remnawave when provided.
    if any(v is not None for v in (body.name, body.favicon_link, body.login_url)):
        try:
            await client.infra_update_provider(
                uuid, name=body.name, favicon_link=body.favicon_link, login_url=body.login_url,
            )
        except Exception as exc:
            raise _wrap_rw(exc)
    await store.upsert_provider_meta(
        uuid, balance=body.balance, currency=body.currency, threshold=body.low_balance_threshold,
    )
    return {"ok": True}


@router.delete("/providers/{uuid}")
async def delete_provider(uuid: str, force: bool = False):
    """Cascade guard: refuse if the provider still has billing nodes unless ?force=true."""
    client = _client()
    try:
        providers = await client.infra_list_providers()
        target = next((p for p in providers if p["uuid"] == uuid), None)
        if target and (target.get("billingNodes") or []) and not force:
            raise HTTPException(
                409,
                f"К провайдеру привязано узлов: {len(target['billingNodes'])}. "
                "Сначала отвяжите их или используйте принудительное удаление.",
            )
        await client.infra_delete_provider(uuid)
    except HTTPException:
        raise
    except Exception as exc:
        raise _wrap_rw(exc)
    await store.delete_provider_meta(uuid)
    return {"ok": True}


# ── Billing nodes ─────────────────────────────────────────────

@router.get("/nodes")
async def list_nodes():
    """Billing nodes (+ local monthly cost), available nodes, and Remnawave stats."""
    client = _client()
    try:
        data = await client.infra_list_nodes()
    except Exception as exc:
        raise _wrap_rw(exc)
    costs = await store.node_meta_all()
    billing = []
    for n in data.get("billingNodes", []) or []:
        billing.append({**n, "monthlyCost": costs.get(n["uuid"], 0)})
    return {
        "billingNodes": billing,
        "availableBillingNodes": data.get("availableBillingNodes", []) or [],
        "stats": data.get("stats", {}),
    }


@router.post("/nodes", status_code=201)
async def create_node(body: BillingNodeCreate):
    client = _client()
    try:
        created = await client.infra_create_node(
            provider_uuid=body.provider_uuid, node_uuid=body.node_uuid,
            name=body.name, next_billing_at=body.next_billing_at,
        )
    except Exception as exc:
        raise _wrap_rw(exc)
    # The created billing-node uuid may be nested; find it best-effort.
    new_uuid = created.get("uuid") if isinstance(created, dict) else None
    if new_uuid and body.monthly_cost:
        await store.set_node_cost(new_uuid, body.monthly_cost)
    return {"ok": True}


@router.patch("/nodes")
async def update_nodes(body: BillingNodeUpdate):
    client = _client()
    try:
        await client.infra_update_nodes(body.uuids, next_billing_at=body.next_billing_at)
    except Exception as exc:
        raise _wrap_rw(exc)
    if body.monthly_cost is not None:
        for u in body.uuids:
            await store.set_node_cost(u, body.monthly_cost)
    return {"ok": True}


@router.delete("/nodes/{uuid}")
async def delete_node(uuid: str):
    client = _client()
    try:
        await client.infra_delete_node(uuid)
    except Exception as exc:
        raise _wrap_rw(exc)
    await store.delete_node_meta(uuid)
    return {"ok": True}


# ── History ───────────────────────────────────────────────────

@router.get("/history")
async def list_history():
    client = _client()
    try:
        records = await client.infra_list_history()
    except Exception as exc:
        raise _wrap_rw(exc)
    return records


@router.post("/history", status_code=201)
async def create_history(body: HistoryCreate):
    client = _client()
    try:
        await client.infra_create_history(
            provider_uuid=body.provider_uuid, amount=body.amount, billed_at=body.billed_at,
        )
    except Exception as exc:
        raise _wrap_rw(exc)
    return {"ok": True}


@router.delete("/history/{uuid}")
async def delete_history(uuid: str):
    client = _client()
    try:
        await client.infra_delete_history(uuid)
    except Exception as exc:
        raise _wrap_rw(exc)
    return {"ok": True}


# ── Analytics + burn-rate ─────────────────────────────────────

@router.get("/analytics")
async def analytics():
    """Spend-by-provider (pie), monthly spend (line), and burn-rate per provider +
    globally. Also fires the low-balance notification hook."""
    client = _client()
    try:
        providers = await client.infra_list_providers()
        history = await client.infra_list_history()
        nodes_data = await client.infra_list_nodes()
    except Exception as exc:
        raise _wrap_rw(exc)

    pmeta = await store.provider_meta_all()
    costs = await store.node_meta_all()
    pname = {p["uuid"]: p["name"] for p in providers}

    # Spend by provider (sum of history amounts).
    by_provider: dict[str, float] = defaultdict(float)
    by_month: dict[str, float] = defaultdict(float)
    for r in history:
        by_provider[r.get("providerUuid", "")] += float(r.get("amount", 0))
        billed = r.get("billedAt", "")
        month = billed[:7] if len(billed) >= 7 else "?"   # YYYY-MM
        by_month[month] += float(r.get("amount", 0))

    pie = [
        {"provider": pname.get(uuid, uuid[:8]), "total": round(total, 2)}
        for uuid, total in sorted(by_provider.items(), key=lambda x: -x[1])
    ]
    line = [{"month": m, "total": round(t, 2)} for m, t in sorted(by_month.items())]

    # Per-provider monthly cost = sum of its billing nodes' local costs.
    provider_monthly_cost: dict[str, float] = defaultdict(float)
    for n in nodes_data.get("billingNodes", []) or []:
        provider_monthly_cost[n.get("providerUuid", "")] += costs.get(n["uuid"], 0)

    burn = []
    merged_for_alert = []
    total_balance = total_monthly = 0.0
    for p in providers:
        uuid = p["uuid"]
        m = pmeta.get(uuid, {})
        balance = m.get("balance", 0) or 0
        currency = m.get("currency", "RUB")
        threshold = m.get("low_balance_threshold", 0) or 0
        monthly = provider_monthly_cost.get(uuid, 0)
        daily = monthly / 30.0
        days_left = round(balance / daily, 1) if daily > 0 else None
        burn.append({
            "provider": p["name"], "balance": round(balance, 2), "currency": currency,
            "monthlyCost": round(monthly, 2), "daysLeft": days_left,
            "critical": days_left is not None and days_left < 7,
        })
        merged_for_alert.append({
            "name": p["name"], "balance": balance, "currency": currency,
            "lowBalanceThreshold": threshold,
        })
        total_balance += balance
        total_monthly += monthly

    # Fire the notification stub for any provider below its threshold.
    alerts = await infra_notify.check_low_balances(merged_for_alert)

    global_daily = total_monthly / 30.0
    global_days_left = round(total_balance / global_daily, 1) if global_daily > 0 else None

    return {
        "spendByProvider": pie,
        "spendByMonth": line,
        "burnRate": {
            "perProvider": burn,
            "global": {
                "totalBalance": round(total_balance, 2),
                "totalMonthlyCost": round(total_monthly, 2),
                "daysLeft": global_days_left,
                "critical": global_days_left is not None and global_days_left < 7,
            },
        },
        "stats": nodes_data.get("stats", {}),
        "alertsCount": len(alerts),
    }
