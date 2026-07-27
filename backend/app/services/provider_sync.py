"""Hosting-provider balance sync: adapter → local `provider_meta` (Wave-9 Plan C).

Two entry points:
- `sync_one()` — behind the «Синхронизировать» button (and the provider editor's test);
- `loop()` — the background refresher, gated on the `monitoring` worker-lease like
  every other loop, so `--profile split` still runs it exactly once.

Design notes:
- The synced balance goes into the **existing** `provider_meta.balance/currency`
  column. `dashboard/summary` (total, burn-rate, days-left) and the low-balance
  notifier already read it, so one write lights up the whole subsystem — no
  parallel "synced balance" field to keep consistent.
- Service listings are NOT persisted: they are a live view at the vendor, and the
  local `services` table is the user's own bookkeeping (import is an explicit
  button in the API layer, never automatic).
- A provider whose credentials are rejected is put on an exponential backoff
  instead of being retried every tick: wrong creds don't fix themselves, and
  hammering a vendor's auth endpoint is how an account gets rate-limited.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from typing import Any, Optional

from app.services import accounts, infra_billing_store as store, vault_store, worker_lease
from app.services.hosting_providers import registry

log = logging.getLogger("provider_sync")

_TICK = 300               # how often the loop wakes up
_MIN_INTERVAL = 300       # floor for the per-account refresh interval
_DEFAULT_INTERVAL = 900
_BACKOFF_START = 900
_BACKOFF_MAX = 6 * 3600

# (account_id, provider_uuid) -> (retry_not_before_ts, current_delay)
_backoff: dict[tuple[str, str], tuple[int, int]] = {}
# (account_id, provider_uuid) -> last successful sync ts (the loop's own clock;
# provider_meta.balance_synced_at is the persisted one shown in the UI)
_last_sync: dict[tuple[str, str], int] = {}


def _adapter(kind: str):
    """Resolve a registry kind to an adapter INSTANCE.

    The registry may hold either instances or classes depending on how an adapter
    module declares itself; normalise here so callers never care."""
    found = registry.ADAPTERS.get(kind)
    if isinstance(found, type):
        return found()
    return found


async def _interval(account_id: str) -> int:
    """Per-account refresh interval from the existing billing settings."""
    try:
        raw = await store.get_settings(account_id) or {}
        val = int(float(raw.get("refresh_interval") or _DEFAULT_INTERVAL))
    except Exception:
        val = _DEFAULT_INTERVAL
    return max(_MIN_INTERVAL, val)


async def _auto_sync_enabled(account_id: str) -> bool:
    try:
        raw = await store.get_settings(account_id) or {}
    except Exception:
        return False
    # Absent key → off: a background job that reaches third-party APIs with the
    # user's credentials must be opted into, not inherited by every account.
    return str(raw.get("auto_sync") or "").lower() in ("1", "true", "yes", "on")


async def _fail(provider_uuid: str, account_id: Optional[str], error: str,
                *, status: str = "unknown") -> dict:
    await store.upsert_provider_meta(provider_uuid, account_id,
                                     status=status, last_error=error[:300])
    return {"ok": False, "error": error}


async def sync_one(provider_uuid: str, account_id: Optional[str] = None,
                   *, want_services: bool = True) -> dict:
    """Verify credentials, pull balance (and optionally services) for ONE provider.

    Never raises: adapters are contractually silent, and everything else lands in
    `last_error` so the UI can show it. Returns
    {ok, balance?, currency?, services?, error?}."""
    meta = (await store.provider_meta_all(account_id)).get(provider_uuid) or {}
    kind = (meta.get("adapter_kind") or "").strip()
    if not kind:
        return {"ok": False, "error": "Адаптер API не выбран"}
    adapter = _adapter(kind)
    if adapter is None:
        return await _fail(provider_uuid, account_id,
                           f"Адаптер «{kind}» недоступен в этой сборке")

    ref = (meta.get("vault_entry_id") or "").strip()
    creds: Optional[dict[str, Any]] = None
    if ref:
        creds = await vault_store.a_read_fields(ref, account_id)
    if not creds:
        return await _fail(provider_uuid, account_id,
                           "Креды из Хранилища недоступны — проверьте запись",
                           status="auth_error")

    ok, err = await adapter.verify(creds)
    if not ok:
        return await _fail(provider_uuid, account_id, err or "проверка не удалась",
                           status="auth_error")

    out: dict[str, Any] = {"ok": True, "caps": sorted(adapter.CAPS)}
    now = int(time.time())
    bal = await adapter.balance(creds) if "balance" in adapter.CAPS else None
    if bal is not None:
        await store.upsert_provider_meta(
            provider_uuid, account_id, balance=round(float(bal.amount), 2),
            currency=bal.currency or "RUB", status="active",
            balance_synced_at=now, last_error="")
        out["balance"], out["currency"] = round(float(bal.amount), 2), bal.currency
    else:
        # No balance endpoint (VK/Procloud/Oracle) — creds are still valid, so the
        # provider is healthy; the UI shows «баланс вручную» from CAPS.
        await store.upsert_provider_meta(provider_uuid, account_id, status="active",
                                         balance_synced_at=now, last_error="")

    if want_services and "services" in adapter.CAPS:
        out["services"] = [dataclasses.asdict(s) for s in await adapter.services(creds)]
    if "payments" in adapter.CAPS:
        out["payments"] = await adapter.payments(creds)
    return out


async def _sync_account(account_id: str, now: int) -> None:
    interval = await _interval(account_id)
    for uuid, meta in (await store.provider_meta_all(account_id)).items():
        if not (meta.get("adapter_kind") or "").strip():
            continue
        key = (account_id, uuid)
        retry_at, delay = _backoff.get(key, (0, 0))
        if now < retry_at:
            continue
        if now - _last_sync.get(key, 0) < interval:
            continue
        res = await sync_one(uuid, account_id, want_services=False)
        _last_sync[key] = now
        if res.get("ok"):
            _backoff.pop(key, None)
        else:
            nxt = min(max(delay * 2, _BACKOFF_START), _BACKOFF_MAX)
            _backoff[key] = (now + nxt, nxt)


async def loop() -> None:
    """Background balance refresh. Per-account explicit account_id (no request
    context here — same rule as collector_loop/auto_backup)."""
    while True:
        try:
            if not worker_lease.acquire(worker_lease.MONITORING):
                await asyncio.sleep(_TICK)
                continue
            now = int(time.time())
            for acc in accounts.list_accounts():
                aid = acc["id"]
                try:
                    if not await _auto_sync_enabled(aid):
                        continue
                    await _sync_account(aid, now)
                except Exception as exc:
                    log.warning("provider_sync.account_failed: %s", str(exc)[:200])
        except Exception:
            pass  # never let the loop die
        await asyncio.sleep(_TICK)
