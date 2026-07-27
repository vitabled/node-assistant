"""RuVDS adapter — balance, servers, payments (Wave-9 Plan C, Ф1).

`https://api.ruvds.com`, `Authorization: Bearer <token>` (Basic was dropped in
API 2.23). Token is issued at https://ruvds.com/my/settings/api.

Quirks taken from the v2 OpenAPI spec (`ruvds-api-v2.yaml`) — each one bites:

- **`currency` is an INTEGER enum**, not an ISO string: 1=RUB, 3=USD, 4=EUR. It
  appears that way in both `/v2/balance` and `/v2/payments`.
- **A server has no `name`.** The closest thing is the user's `user_comment`, so
  an uncommented server falls back to `VPS #<id>`.
- **`paid_till` and `network_v4` are `null` unless asked for**: they need
  `get_paid_till=true` / `get_network=true` in the query, otherwise the fields
  are present but empty and the UI would silently show no IP and no expiry.
- **`payment_period` is an integer enum** (2=1 month, 3=3 months, 4=6 months,
  5=1 year, 1=trial, 0=unset) → mapped to a period string, falling back to
  "month" (the common plan) when unset/unknown.
- **Per-server cost is a separate endpoint** (`/v2/servers/{id}/cost`), i.e. one
  request per server against a 120 req/min budget. We report `cost=None` rather
  than fan out; the local `services` table carries the user's own cost anyway.
- **Rate limit is advertised**: `ratelimit-remaining` / `ratelimit-reset`, and on
  429 a `retry-after`. On 429 we surface the wait in seconds instead of failing
  with a bare status.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.services.hosting_providers.base import (
    Balance,
    CredField,
    ProviderAdapter,
    ServiceItem,
    map_http_error,
    redact,
)

log = logging.getLogger("hosting.ruvds")

_BASE = "https://api.ruvds.com"

_CURRENCY = {1: "RUB", 3: "USD", 4: "EUR"}
_PERIOD = {1: "trial", 2: "month", 3: "quarter", 4: "half_year", 5: "year"}

# One page is 25 by default; ask for more and follow `pagination.next_page` so an
# account with >25 servers isn't silently truncated. Capped so a broken
# pagination cursor can't spin forever.
_PER_PAGE = 100
_MAX_PAGES = 5


def _currency(raw: Any) -> str:
    try:
        return _CURRENCY.get(int(raw), "RUB")
    except (TypeError, ValueError):
        return "RUB"


class RuvdsAdapter(ProviderAdapter):
    KIND = "ruvds"
    TITLE = "RuVDS"
    FIELDS = [CredField("token", "API-токен", "password")]
    CAPS = {"balance", "services", "payments"}

    async def _get(
        self, creds: dict, path: str, params: Optional[dict] = None
    ) -> tuple[Optional[dict], str]:
        """GET one endpoint → (json, error). `error` is "" on success; the token
        never appears in it (it travels in a header, but a proxy error string can
        still quote the request — redact defensively)."""
        token = str((creds or {}).get("token") or "").strip()
        try:
            async with self._client() as c:
                r = await c.get(
                    f"{_BASE}{path}",
                    params=params,
                    headers={"Authorization": f"Bearer {token}",
                             "Content-Type": "application/json"},
                )
        except httpx.HTTPError as exc:
            return None, f"RuVDS недоступен: {redact(str(exc), token)}"

        if r.status_code == 429:
            wait = r.headers.get("retry-after") or r.headers.get("ratelimit-reset") or ""
            suffix = f" (подождите {wait} с)" if wait.strip().isdigit() else ""
            return None, map_http_error(429) + suffix
        if r.status_code >= 400:
            return None, map_http_error(r.status_code)
        try:
            data = r.json()
        except ValueError:
            return None, "RuVDS вернул не-JSON ответ"
        return (data if isinstance(data, dict) else {}), ""

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        _data, err = await self._get(creds, "/v2/balance")
        return (False, err) if err else (True, "")

    async def balance(self, creds: dict) -> Optional[Balance]:
        if self.check_fields(creds):
            return None
        data, err = await self._get(creds, "/v2/balance")
        if err or not data:
            return None
        try:
            return Balance(float(data["amount"]), _currency(data.get("currency")))
        except (KeyError, TypeError, ValueError):
            log.warning("ruvds: unexpected /v2/balance shape")
            return None

    async def services(self, creds: dict) -> list[ServiceItem]:
        if self.check_fields(creds):
            return []
        regions = await self._datacenters(creds)
        out: list[ServiceItem] = []
        page = 1
        while page <= _MAX_PAGES:
            data, err = await self._get(creds, "/v2/servers", {
                "per_page": _PER_PAGE, "page": page,
                "get_paid_till": "true", "get_network": "true",
            })
            if err or not data:
                break
            servers = data.get("servers")
            if not isinstance(servers, list):
                break
            for raw in servers:
                if isinstance(raw, dict):
                    out.append(_server_item(raw, regions))
            nxt = (data.get("pagination") or {}).get("next_page")
            if not isinstance(nxt, int) or nxt <= page:
                break
            page = nxt
        return out

    async def payments(self, creds: dict) -> list[dict]:
        if self.check_fields(creds):
            return []
        data, err = await self._get(creds, "/v2/payments",
                                    {"per_page": _PER_PAGE, "page": 1})
        if err or not data:
            return []
        items = data.get("payments")
        if not isinstance(items, list):
            return []
        return [_payment(p) for p in items if isinstance(p, dict)]

    async def _datacenters(self, creds: dict) -> dict[int, str]:
        """id → human name, so `region` isn't a bare number in the UI. One extra
        request; a failure just leaves the numeric fallback."""
        data, err = await self._get(creds, "/v2/datacenters")
        if err or not data:
            return {}
        out: dict[int, str] = {}
        for dc in data.get("datacenters") or []:
            if isinstance(dc, dict) and isinstance(dc.get("id"), int):
                out[dc["id"]] = str(dc.get("name") or "")
        return out


def _server_item(raw: dict, regions: dict[int, str]) -> ServiceItem:
    sid = raw.get("virtual_server_id")
    nets = raw.get("network_v4") or []
    ip = ""
    if isinstance(nets, list) and nets and isinstance(nets[0], dict):
        ip = str(nets[0].get("ip_address") or "")
    dc = raw.get("datacenter")
    region = regions.get(dc, "") if isinstance(dc, int) else ""
    if not region and dc is not None:
        region = str(dc)
    return ServiceItem(
        id=str(sid if sid is not None else ""),
        name=str(raw.get("user_comment") or "").strip() or f"VPS #{sid}",
        kind="vps",
        cost=None,
        currency="RUB",
        period=_PERIOD.get(raw.get("payment_period"), "month"),
        status=str(raw.get("status") or ""),
        ip=ip,
        region=region,
        paid_till=str(raw.get("paid_till") or ""),
    )


def _payment(raw: dict) -> dict:
    try:
        amount = float(raw.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0.0
    return {
        "ts": str(raw.get("dt") or ""),
        "amount": amount,
        "currency": _currency(raw.get("currency")),
        "type": "topup" if raw.get("direction") == 1 else "charge",
        "note": str(raw.get("pay_source") or ""),
    }


ADAPTER = RuvdsAdapter()
