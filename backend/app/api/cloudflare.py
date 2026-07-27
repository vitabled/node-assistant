"""
Cloudflare connection + billing API (Wave-9 Plan B Ф1–Ф2). Account-gated
(`require_account` is wired in main.py, like `haproxy.router`).

- GET/POST /api/cloudflare/config → the connection (enabled / CF account id /
  registrant contact + a WRITE-ONLY API token; the plaintext never comes back,
  only `has_token`).
- POST /api/cloudflare/test → accounts + billing/profile probe (mirrors
  `haproxy.test_connection`, but reports the failure in the body so the connect
  form can show it inline).
- GET /api/cloudflare/accounts · /billing/summary · /subscriptions · /usage · /zones.

**Cache:** `_CACHE[(account_id, key)] = (ts, value)`, TTL 15 min, `?refresh=1`
invalidates the key. Billing data moves at billing-cycle speed while the UI
re-renders constantly — without this every render would hit 3-4 external endpoints.

⚠️ `billing/summary` never fails as a whole: a token with partial scopes 403s on
individual sub-endpoints, so each one is caught and its name goes into `degraded`
(the UI shows «нет прав на X» instead of an empty screen).

⚠️ The `/domains*` routes (including the billable purchase) are a separate phase —
their gates belong with them, not here.
"""
from __future__ import annotations

import re
import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from app.models.settings import AppSettings, CloudflareConfig
from app.services import accounts, cf_client, storage

router = APIRouter(prefix="/api/cloudflare")

_NOT_CONNECTED = "Cloudflare не подключён — Настройки → Cloudflare"
_NO_ACCOUNT = "Не выбран аккаунт Cloudflare — Настройки → Cloudflare"

_TTL = 900.0  # 15 min
_CACHE: dict[tuple[str, str], tuple[float, Any]] = {}


class CloudflareConfigBody(BaseModel):
    enabled: bool = False
    account_id: str = ""
    api_token: str = ""  # write-only; blank keeps the stored one
    # None keeps the stored contact: the billing screens save the connection
    # without carrying the registrant PII around.
    default_contact: Optional[dict] = None


# ── config ────────────────────────────────────────────────────────────────────
def _cfg() -> CloudflareConfig:
    return AppSettings(**storage.load_settings()).cloudflare


def _public(cfg: CloudflareConfig) -> dict:
    return {
        "enabled": cfg.enabled,
        "account_id": cfg.account_id,
        "has_token": bool(cfg.api_token_enc),
        "default_contact": cfg.default_contact,
    }


def _account_id() -> str:
    return accounts.current_account.get() or ""


def _invalidate(account_id: str) -> None:
    for key in [k for k in _CACHE if k[0] == account_id]:
        _CACHE.pop(key, None)


def _cached(key: str, refresh: bool) -> Any:
    """Return a fresh cached value, or None (a miss, or an explicit refresh)."""
    ck = (_account_id(), key)
    if refresh:
        _CACHE.pop(ck, None)
        return None
    hit = _CACHE.get(ck)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    return None


def _put(key: str, value: Any) -> Any:
    _CACHE[(_account_id(), key)] = (time.time(), value)
    return value


def _client_or_400(cfg: CloudflareConfig) -> cf_client.CfClient:
    token = cf_client.decrypt_token(cfg.api_token_enc) or ""
    if not token:
        raise HTTPException(400, _NOT_CONNECTED)
    return cf_client.CfClient(token)


def _acc_or_400(cfg: CloudflareConfig) -> str:
    acc = (cfg.account_id or "").strip()
    if not acc:
        raise HTTPException(400, _NO_ACCOUNT)
    return acc


def _fail(exc: cf_client.CfError) -> HTTPException:
    """CfError.detail is already token-redacted by the client — safe to surface."""
    return HTTPException(exc.status or 502, exc.detail)


@router.get("/config")
async def get_config() -> dict:
    return _public(_cfg())


@router.post("/config")
async def save_config(body: CloudflareConfigBody) -> dict:
    data = storage.load_settings()
    current = AppSettings(**data).cloudflare
    cfg = {
        **current.model_dump(),
        "enabled": body.enabled,
        "account_id": (body.account_id or "").strip(),
    }
    if body.api_token.strip():
        cfg["api_token_enc"] = cf_client.encrypt_token(body.api_token.strip())
    if body.default_contact is not None:
        cfg["default_contact"] = body.default_contact
    data["cloudflare"] = cfg
    storage.save_settings(data)
    # A new token/account means the cached numbers belong to someone else.
    _invalidate(_account_id())

    result = _public(AppSettings(**data).cloudflare)
    result["ok"] = True
    return result


@router.post("/test")
async def test_connection() -> dict:
    cfg = _cfg()
    client = _client_or_400(cfg)
    try:
        accs = await client.accounts()
    except cf_client.CfError as exc:
        return {"ok": False, "accounts": [], "error": exc.detail}
    # During connect the account isn't chosen yet → probe the first one, so the
    # form can report both "token works" and "billing scope works" at once.
    acc = (cfg.account_id or "").strip()
    if not acc and accs:
        first = accs[0] if isinstance(accs[0], dict) else {}
        acc = str(first.get("id") or "")
    if not acc:
        return {"ok": False, "accounts": accs,
                "error": "Токен принят, но у него нет доступа ни к одному аккаунту"}
    try:
        await client.billing_profile(acc)
    except cf_client.CfError as exc:
        return {"ok": False, "accounts": accs, "error": exc.detail}
    return {"ok": True, "accounts": accs, "error": None}


# ── billing ───────────────────────────────────────────────────────────────────
@router.get("/accounts")
async def list_accounts(refresh: int = Query(0)) -> list[dict]:
    cached = _cached("accounts", bool(refresh))
    if cached is not None:
        return cached
    client = _client_or_400(_cfg())
    try:
        return _put("accounts", await client.accounts())
    except cf_client.CfError as exc:
        raise _fail(exc) from exc


def _profile_view(raw: dict) -> dict:
    """Read the billing profile defensively: the live shape wasn't probed (no CF
    account at build time), so an unknown/renamed field degrades to null instead
    of raising."""
    payment = raw.get("payment_method") or raw.get("payment_methods") or raw.get("card")
    return {
        "balance": raw.get("balance"),
        "currency": raw.get("currency") or "",
        "payment_method_present": bool(payment),
    }


# Cloudflare bills per period, so a raw sum of `price` would mix weekly and
# yearly plans; these are the monthly-equivalent divisors.
_FREQ_MONTHS = {
    "weekly": 1 / 4.345,
    "monthly": 1.0,
    "quarterly": 3.0,
    "semiannually": 6.0,
    "yearly": 12.0,
    "annually": 12.0,
}
# Not future spend.
_DEAD_STATES = {"cancelled", "canceled", "expired"}


def _spend_view(subs: list) -> tuple[float, Optional[str]]:
    total = 0.0
    nearest: Optional[str] = None
    for sub in subs:
        if not isinstance(sub, dict):
            continue
        if str(sub.get("state") or "").lower() in _DEAD_STATES:
            continue
        price = sub.get("price")
        months = _FREQ_MONTHS.get(str(sub.get("frequency") or "").lower())
        if isinstance(price, (int, float)) and not isinstance(price, bool) and months:
            total += float(price) / months
        end = sub.get("current_period_end")
        # Same-format ISO-8601 timestamps sort lexicographically → no parsing.
        if isinstance(end, str) and end and (nearest is None or end < nearest):
            nearest = end
    return round(total, 2), nearest


@router.get("/billing/summary")
async def billing_summary(refresh: int = Query(0)) -> dict:
    cached = _cached("billing/summary", bool(refresh))
    if cached is not None:
        return cached
    cfg = _cfg()
    client = _client_or_400(cfg)
    acc = _acc_or_400(cfg)

    out: dict[str, Any] = {
        "profile": {"balance": None, "currency": "", "payment_method_present": False},
        "paygo": {},
        "subscriptions_total_monthly": 0.0,
        "next_charge_at": None,
        "degraded": [],
    }
    # Each sub-endpoint needs its own token scope: one 403 must cost its section,
    # not the whole screen.
    try:
        out["profile"] = _profile_view(await client.billing_profile(acc) or {})
    except cf_client.CfError:
        out["degraded"].append("billing/profile")
    try:
        out["paygo"] = await client.paygo_info(acc) or {}
    except cf_client.CfError:
        out["degraded"].append("paygo-usage-info")
    try:
        total, nearest = _spend_view(await client.subscriptions(acc) or [])
        out["subscriptions_total_monthly"] = total
        out["next_charge_at"] = nearest
    except cf_client.CfError:
        out["degraded"].append("subscriptions")

    return _put("billing/summary", out)


@router.get("/subscriptions")
async def list_subscriptions(refresh: int = Query(0)) -> list[dict]:
    cached = _cached("subscriptions", bool(refresh))
    if cached is not None:
        return cached
    cfg = _cfg()
    client = _client_or_400(cfg)
    acc = _acc_or_400(cfg)
    try:
        return _put("subscriptions", await client.subscriptions(acc))
    except cf_client.CfError as exc:
        raise _fail(exc) from exc


@router.get("/usage")
async def paygo_usage(
    date_from: str = Query("", alias="from"),
    date_to: str = Query("", alias="to"),
    refresh: int = Query(0),
) -> Any:
    key = f"usage:{date_from}:{date_to}"
    cached = _cached(key, bool(refresh))
    if cached is not None:
        return cached
    cfg = _cfg()
    client = _client_or_400(cfg)
    acc = _acc_or_400(cfg)
    try:
        return _put(key, await client.paygo_usage(acc, date_from or None, date_to or None))
    except cf_client.CfError as exc:
        raise _fail(exc) from exc


# ═══════════════════════════════════════════════════════════════
# Domains — Registrar (search / check / register / manage)
# ═══════════════════════════════════════════════════════════════
_DOMAIN_RE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+")


class DomainQuery(BaseModel):
    q: str = Field(..., min_length=1, max_length=120)


class DomainCheckBody(BaseModel):
    names: list[str] = Field(..., min_length=1, max_length=25)

    @field_validator("names")
    @classmethod
    def _fqdns(cls, v: list[str]) -> list[str]:
        out = []
        for raw in v:
            name = (raw or "").strip().lower()
            if not _DOMAIN_RE.fullmatch(name):
                raise ValueError(f"Некорректное доменное имя: {raw}")
            out.append(name)
        return out


class DomainPatch(BaseModel):
    auto_renew: Optional[bool] = None
    privacy_mode: Optional[str] = None


class DomainRegister(BaseModel):
    """Purchase request. `confirm` + `expected_price`/`expected_currency` are the
    two gates that make this safe to expose: the first proves the click was
    deliberate, the second proves the user saw the price the registry is charging
    RIGHT NOW (we re-check server-side and refuse on any drift)."""

    domain_name: str
    years: int = Field(default=1, ge=1, le=10)
    privacy_mode: str = "redaction"
    # Left off by default: Cloudflare treats `true` as standing permission to
    # charge the saved payment method at renewal time.
    auto_renew: bool = False
    contacts: dict[str, Any] = Field(default_factory=dict)
    confirm: bool = False
    expected_price: Optional[float] = None
    expected_currency: str = ""

    @field_validator("domain_name")
    @classmethod
    def _fqdn(cls, v: str) -> str:
        name = (v or "").strip().lower()
        if not _DOMAIN_RE.fullmatch(name):
            raise ValueError("Некорректное доменное имя")
        return name


def _norm_check(item: Any) -> dict:
    """Normalise one domain-check row.

    The exact response shape of `POST /registrar/domain-check` is not pinned in
    the public docs and we have no live Registrar account to probe, so read
    defensively across the plausible key spellings and keep `raw` for the UI."""
    if not isinstance(item, dict):
        return {"name": str(item), "available": False, "price": None, "currency": "", "period_years": 0}
    name = item.get("name") or item.get("domain") or item.get("domain_name") or ""
    avail = item.get("available")
    if avail is None:
        avail = item.get("availability") in ("available", True)
    price = item.get("price")
    if price is None:
        price = item.get("registration_price") or item.get("amount")
    try:
        price = round(float(price), 2) if price is not None else None
    except (TypeError, ValueError):
        price = None
    return {
        "name": str(name).lower(),
        "available": bool(avail),
        "price": price,
        "currency": str(item.get("currency") or item.get("currency_code") or "USD"),
        "period_years": int(item.get("period_years") or item.get("years") or 0),
        "raw": item,
    }


@router.get("/domains")
async def list_domains(refresh: int = Query(0)) -> Any:
    cached = _cached("domains", bool(refresh))
    if cached is not None:
        return cached
    cfg = _cfg()
    client = _client_or_400(cfg)
    acc = _acc_or_400(cfg)
    try:
        return _put("domains", await client.registrations(acc))
    except cf_client.CfError as exc:
        raise _fail(exc) from exc


@router.post("/domains/search")
async def search_domains(body: DomainQuery) -> Any:
    cfg = _cfg()
    client = _client_or_400(cfg)
    acc = _acc_or_400(cfg)
    try:
        res = await client.domain_search(acc, body.q.strip())
    except cf_client.CfError as exc:
        raise _fail(exc) from exc
    rows = res if isinstance(res, list) else (res or {}).get("results") or []
    return [_norm_check(r) for r in rows]


@router.post("/domains/check")
async def check_domains(body: DomainCheckBody) -> Any:
    cfg = _cfg()
    client = _client_or_400(cfg)
    acc = _acc_or_400(cfg)
    try:
        res = await client.domain_check(acc, body.names)
    except cf_client.CfError as exc:
        raise _fail(exc) from exc
    rows = res if isinstance(res, list) else (res or {}).get("results") or []
    return [_norm_check(r) for r in rows]


@router.patch("/domains/{name}")
async def patch_domain(name: str, body: DomainPatch) -> dict:
    cfg = _cfg()
    client = _client_or_400(cfg)
    acc = _acc_or_400(cfg)
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(400, "Нечего менять")
    try:
        await client.patch_registration(acc, name.strip().lower(), **fields)
    except cf_client.CfError as exc:
        raise _fail(exc) from exc
    _invalidate(_account_id())
    return {"ok": True}


@router.post("/domains/register")
async def register_domain(body: DomainRegister) -> dict:
    """Buy a domain. THIS SPENDS MONEY — see the gates on DomainRegister."""
    if not body.confirm:
        raise HTTPException(400, "Покупка не подтверждена")
    cfg = _cfg()
    client = _client_or_400(cfg)
    acc = _acc_or_400(cfg)

    # A missing payment method fails deep inside Cloudflare's workflow with an
    # opaque error; check first so the user gets a sentence they can act on.
    try:
        profile = await client.billing_profile(acc)
    except cf_client.CfError as exc:
        raise _fail(exc) from exc
    if not _profile_view(profile if isinstance(profile, dict) else {})["payment_method_present"]:
        raise HTTPException(400, "В Cloudflare не задан способ оплаты по умолчанию — "
                                 "добавьте его в панели Cloudflare и повторите")

    try:
        checked = await client.domain_check(acc, [body.domain_name])
    except cf_client.CfError as exc:
        raise _fail(exc) from exc
    rows = checked if isinstance(checked, list) else (checked or {}).get("results") or []
    row = next((_norm_check(r) for r in rows), None)
    if row is None:
        raise HTTPException(400, "Реестр не ответил о доступности домена — попробуйте позже")
    if not row["available"]:
        raise HTTPException(400, f"Домен {body.domain_name} недоступен для регистрации")
    if row["price"] is None:
        # Fail closed: without a price from the registry we cannot prove the user
        # agreed to what will actually be charged.
        raise HTTPException(400, "Cloudflare не вернул цену — покупку через панель "
                                 "node-assistant выполнить нельзя, оформите её в Cloudflare")
    if body.expected_price is None or abs(row["price"] - body.expected_price) > 0.01 \
            or (body.expected_currency and body.expected_currency.upper() != row["currency"].upper()):
        raise HTTPException(409, f"Цена изменилась: сейчас {row['price']} {row['currency']}. "
                                 "Обновите список и подтвердите заново")

    payload: dict[str, Any] = {
        "domain_name": body.domain_name,
        "years": body.years,
        "privacy_mode": body.privacy_mode,
        "auto_renew": body.auto_renew,
    }
    if body.contacts:
        payload["contacts"] = body.contacts
    try:
        res = await client.register(acc, payload)
    except cf_client.CfError as exc:
        raise _fail(exc) from exc
    _invalidate(_account_id())
    status = res if isinstance(res, dict) else {}
    return {
        "ok": True,
        "domain": body.domain_name,
        "price": row["price"], "currency": row["currency"],
        "state": status.get("status") or status.get("state") or "submitted",
        "workflow": status,
    }


@router.get("/zones")
async def list_zones(refresh: int = Query(0)) -> list[dict]:
    cached = _cached("zones", bool(refresh))
    if cached is not None:
        return cached
    client = _client_or_400(_cfg())
    try:
        return _put("zones", await client.zones())
    except cf_client.CfError as exc:
        raise _fail(exc) from exc
