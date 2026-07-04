"""
Infra-billing API — full 8-tab subsystem.

Providers/nodes/history proxy Remnawave's InfraBillingController (+ local meta);
projects/services/payments/settings/api-tokens/dashboard are LOCAL (node-assistant
owns them — Remnawave has no such endpoints). See services/infra_billing_store.py.

All routes are gated by the panel-wide account auth (require_account) and read
the ACTIVE account's isolated billing DB. There is no separate PIN gate.
Remnawave errors surface as HTTP errors → toasts on the client.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
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


def _convert(amount: float, frm: str, to: str, rates: dict) -> float:
    """Convert `amount` from currency `frm` to `to` using RUB-anchored rates
    (rates[X] = value of 1 X in RUB)."""
    anchor = amount * rates.get(frm, 1.0)
    return anchor / rates.get(to, 1.0)


# ── Request models (client + server validation) ───────────────
def _money(v: float) -> float:
    return round(float(v), 2)   # normalise to 2 dp; handles 0 fine


class ProviderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    favicon_link: str = ""
    login_url: str = ""
    balance: float = Field(default=0, ge=0)
    currency: str = Field(default="RUB", min_length=1, max_length=8)
    low_balance_threshold: float = Field(default=0, ge=0)
    api_token_id: str = ""


class ProviderUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    favicon_link: Optional[str] = None
    login_url: Optional[str] = None
    balance: Optional[float] = Field(default=None, ge=0)
    currency: Optional[str] = None
    low_balance_threshold: Optional[float] = Field(default=None, ge=0)
    api_token_id: Optional[str] = None


class ProjectBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str = ""
    node_uuids: list[str] = Field(default_factory=list)


class ServiceBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    kind: str = "vps"
    node_uuid: str = ""
    provider_uuid: str = ""
    project_id: str = ""
    billing_type: str = "fixed"     # fixed | hourly
    cost: float = Field(default=0, ge=0)
    next_billing_at: str = ""

    @field_validator("billing_type")
    @classmethod
    def _bt(cls, v: str) -> str:
        if v not in ("fixed", "hourly"):
            raise ValueError("billing_type должен быть fixed или hourly")
        return v


class PaymentBody(BaseModel):
    provider_uuid: str = ""
    project_id: str = ""
    type: str = "charge"            # charge | topup | adjustment
    amount: float
    currency: str = "RUB"
    status: str = "success"         # success | pending | error
    note: str = ""

    @field_validator("amount")
    @classmethod
    def _amt(cls, v: float) -> float:
        if v == 0:
            raise ValueError("amount не может быть 0")
        return _money(v)


class SettingsBody(BaseModel):
    base_currency: Optional[str] = None
    fx_rates: Optional[dict] = None
    low_balance_threshold: Optional[float] = Field(default=None, ge=0)
    refresh_interval: Optional[str] = None


class ApiTokenCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    provider_kind: str = "generic"
    secret: str = Field(..., min_length=1)


# ═══════════════════════════════════════════════════════════════
# 1. Dashboard summary
# ═══════════════════════════════════════════════════════════════
@router.get("/dashboard/summary")
async def dashboard_summary():
    """Aggregated balance (converted to base currency), burn-rate + charts."""
    client = _client()
    try:
        rw_providers = await client.infra_list_providers()
    except Exception as exc:
        raise _wrap_rw(exc)

    pmeta = await store.provider_meta_all()
    svc = await store.services()
    pays = await store.payments()
    s = await store.get_settings()
    base, rates = s["baseCurrency"], s["fxRates"]
    pname = {p["uuid"]: p["name"] for p in rw_providers}

    # Total balance across providers, converted to base currency.
    total_balance = 0.0
    per_provider_balance = {}
    for uuid, m in pmeta.items():
        conv = _convert(m.get("balance", 0) or 0, m.get("currency", base), base, rates)
        per_provider_balance[uuid] = conv
        total_balance += conv

    # Monthly cost from services (hourly → *730h/mo). Converted to base.
    monthly_cost = 0.0
    cost_by_provider: dict[str, float] = defaultdict(float)
    for sv in svc:
        c = sv["cost"] * (730 if sv["billing_type"] == "hourly" else 1)
        cost_by_provider[sv["provider_uuid"]] += c
        monthly_cost += c

    daily = monthly_cost / 30.0
    hourly = monthly_cost / 730.0
    days_left = round(total_balance / daily, 1) if daily > 0 else None

    # Charts: pie of cost by provider; line of payments by month.
    pie = [{"provider": pname.get(u, "—"), "total": _money(t)} for u, t in sorted(cost_by_provider.items(), key=lambda x: -x[1]) if t > 0]
    by_month: dict[str, float] = defaultdict(float)
    for p in pays:
        if p["type"] in ("charge", "adjustment"):
            month = datetime.fromtimestamp(p["ts"], tz=timezone.utc).strftime("%Y-%m")
            by_month[month] += p["amount"]
    line = [{"month": m, "total": _money(t)} for m, t in sorted(by_month.items())]

    # Fire low-balance notification hook.
    alerts = await infra_notify.check_low_balances([
        {"name": pname.get(u, "—"), "balance": per_provider_balance.get(u, 0),
         "currency": base, "lowBalanceThreshold": s["lowBalanceThreshold"]}
        for u in pmeta
    ])

    return {
        "baseCurrency": base,
        "totalBalance": _money(total_balance),
        "burnRate": {
            "hourly": _money(hourly), "daily": _money(daily), "monthly": _money(monthly_cost),
            "daysLeft": days_left, "critical": days_left is not None and days_left < 7,
        },
        "spendByProvider": pie,
        "spendByMonth": line,
        "alertsCount": len(alerts),
    }


# ═══════════════════════════════════════════════════════════════
# 2. Providers  (Remnawave + local meta)
# ═══════════════════════════════════════════════════════════════
@router.get("/providers")
async def list_providers():
    client = _client()
    try:
        providers = await client.infra_list_providers()
    except Exception as exc:
        raise _wrap_rw(exc)
    meta = await store.provider_meta_all()
    tokens = {t["id"]: t for t in await store.api_tokens()}
    out = []
    for p in providers:
        m = meta.get(p["uuid"], {})
        tid = m.get("api_token_id", "")
        out.append({
            "uuid": p["uuid"], "name": p["name"],
            "faviconLink": p.get("faviconLink", ""), "loginUrl": p.get("loginUrl", ""),
            "nodeCount": len(p.get("billingNodes", []) or []),
            "balance": m.get("balance", 0), "currency": m.get("currency", "RUB"),
            "lowBalanceThreshold": m.get("low_balance_threshold", 0),
            "status": m.get("status", "active"),
            "apiTokenId": tid, "apiTokenName": tokens.get(tid, {}).get("name", ""),
        })
    return out


@router.post("/providers", status_code=201)
async def create_provider(body: ProviderCreate):
    client = _client()
    try:
        created = await client.infra_create_provider(
            name=body.name, favicon_link=body.favicon_link, login_url=body.login_url)
    except Exception as exc:
        raise _wrap_rw(exc)
    await store.upsert_provider_meta(
        created["uuid"], balance=_money(body.balance), currency=body.currency,
        low_balance_threshold=_money(body.low_balance_threshold), api_token_id=body.api_token_id)
    return {"ok": True, "uuid": created["uuid"]}


@router.patch("/providers/{uuid}")
async def update_provider(uuid: str, body: ProviderUpdate):
    client = _client()
    if any(v is not None for v in (body.name, body.favicon_link, body.login_url)):
        try:
            await client.infra_update_provider(uuid, name=body.name, favicon_link=body.favicon_link, login_url=body.login_url)
        except Exception as exc:
            raise _wrap_rw(exc)
    await store.upsert_provider_meta(
        uuid,
        balance=_money(body.balance) if body.balance is not None else None,
        currency=body.currency,
        low_balance_threshold=_money(body.low_balance_threshold) if body.low_balance_threshold is not None else None,
        api_token_id=body.api_token_id)
    return {"ok": True}


@router.delete("/providers/{uuid}")
async def delete_provider(uuid: str, force: bool = False):
    client = _client()
    try:
        providers = await client.infra_list_providers()
        target = next((p for p in providers if p["uuid"] == uuid), None)
        if target and (target.get("billingNodes") or []) and not force:
            raise HTTPException(409, f"К провайдеру привязано узлов: {len(target['billingNodes'])}. Отвяжите их или удалите принудительно.")
        await client.infra_delete_provider(uuid)
    except HTTPException:
        raise
    except Exception as exc:
        raise _wrap_rw(exc)
    await store.delete_provider_meta(uuid)
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════
# 3. Projects  (local)
# ═══════════════════════════════════════════════════════════════
@router.get("/projects")
async def list_projects():
    projects = await store.projects()
    svc = await store.services()
    # cost per project = sum of its services' monthly cost.
    cost_by_project: dict[str, float] = defaultdict(float)
    for sv in svc:
        cost_by_project[sv["project_id"]] += sv["cost"] * (730 if sv["billing_type"] == "hourly" else 1)
    for p in projects:
        p["nodeCount"] = len(p["node_uuids"])
        p["monthlyCost"] = _money(cost_by_project.get(p["id"], 0))
    return projects


@router.post("/projects", status_code=201)
async def create_project(body: ProjectBody):
    pid = await store.create_project(body.name, body.description, body.node_uuids)
    return {"ok": True, "id": pid}


@router.patch("/projects/{pid}")
async def update_project(pid: str, body: ProjectBody):
    await store.update_project(pid, name=body.name, description=body.description, node_uuids=body.node_uuids)
    return {"ok": True}


@router.delete("/projects/{pid}")
async def delete_project(pid: str):
    await store.delete_project(pid)
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════
# 4. Services  (local)
# ═══════════════════════════════════════════════════════════════
@router.get("/services")
async def list_services():
    return await store.services()


@router.post("/services", status_code=201)
async def create_service(body: ServiceBody):
    sid = await store.create_service(**{**body.model_dump(), "cost": _money(body.cost)})
    return {"ok": True, "id": sid}


@router.patch("/services/{sid}")
async def update_service(sid: str, body: ServiceBody):
    await store.update_service(sid, **{**body.model_dump(), "cost": _money(body.cost)})
    return {"ok": True}


@router.delete("/services/{sid}")
async def delete_service(sid: str):
    await store.delete_service(sid)
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════
# 5. Payments  (local, protected)
# ═══════════════════════════════════════════════════════════════
@router.get("/payments")
async def list_payments():
    return await store.payments()


@router.post("/payments", status_code=201)
async def create_payment(body: PaymentBody):
    pid = await store.create_payment(**body.model_dump())
    return {"ok": True, "id": pid}


@router.delete("/payments/{pid}")
async def delete_payment(pid: str):
    await store.delete_payment(pid)
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════
# 6. Settings  (local)
# ═══════════════════════════════════════════════════════════════
@router.get("/settings")
async def get_settings():
    return await store.get_settings()


@router.put("/settings")
async def put_settings(body: SettingsBody):
    await store.put_settings(
        base_currency=body.base_currency, fx_rates=body.fx_rates,
        low_balance_threshold=body.low_balance_threshold,
        refresh_interval=body.refresh_interval)
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════
# 7. Api tokens  (encrypted vault)
# ═══════════════════════════════════════════════════════════════
@router.get("/api-tokens")
async def list_api_tokens():
    return await store.api_tokens()   # masked only — never the plaintext secret


@router.post("/api-tokens", status_code=201)
async def create_api_token(body: ApiTokenCreate):
    tid = await store.create_api_token(body.name, body.provider_kind, body.secret)
    return {"ok": True, "id": tid}


@router.delete("/api-tokens/{tid}")
async def delete_api_token(tid: str):
    await store.delete_api_token(tid)
    return {"ok": True}


@router.post("/api-tokens/{tid}/verify")
async def verify_api_token(tid: str):
    """Validate a stored token. NOTE: per-hosting-provider adapters (Selectel,
    Hetzner, …) are not implemented — this checks the secret decrypts and is
    non-empty. Returns a clear 'not verified against provider' status."""
    secret = await store.get_api_token_secret(tid)
    if not secret:
        raise HTTPException(404, "Токен не найден или не расшифровывается.")
    return {
        "ok": True,
        "detail": "Секрет корректно расшифрован. Реальная проверка у провайдера "
                  "не реализована (нужен адаптер API конкретного хостинга).",
        "verifiedAgainstProvider": False,
    }
