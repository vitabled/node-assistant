"""Veesp adapter — balance + invoices (Wave-9 Plan C, Ф2).

`https://secure.veesp.com/api`, HTTP **Basic** (email:password). Veesp also offers
`POST /login` for a JWT, but Basic needs no token lifecycle for two read calls, so
Basic it is.

Like Beget, the credentials are the customer's control-panel login — the UI must
say so. They travel in the `Authorization` header here (not the query), yet error
text is still redacted before it leaves this module.

⚠️ **The response shapes are not documented on the public overview page**, so both
readers are written defensively and are the part most likely to need a fix once
someone runs them against a live account:

- `/balance` — the amount and currency keys are looked up from a small list of
  plausible names; an unrecognised shape yields `None` (honest «no balance») and a
  warning in the log rather than a wrong number.
- `/invoice` — invoices are charges, so every row is normalised to
  `type: "charge"`; a list and a `{invoices: [...]}` wrapper are both accepted.
- **Service listing is NOT implemented**: the name of the VPS-list endpoint is
  undocumented, and guessing it would produce a silently empty list that looks
  like «no servers». Needs recon against a live account.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.services.hosting_providers.base import (
    Balance,
    CredField,
    ProviderAdapter,
    map_http_error,
    redact,
)

log = logging.getLogger("hosting.veesp")

_BASE = "https://secure.veesp.com/api"

_AMOUNT_KEYS = ("balance", "credit", "amount", "value", "total")
_CURRENCY_KEYS = ("currency", "currency_code", "curr", "code")
_TS_KEYS = ("date", "created", "created_at", "dt", "datetime", "due_date")
_INVOICE_AMOUNT_KEYS = ("total", "amount", "sum", "grand_total")


def _pick_number(node: dict, keys: tuple[str, ...]) -> Optional[float]:
    """First key present that coerces to a number (Veesp may send it as a string)."""
    for key in keys:
        if key in node:
            try:
                return float(str(node[key]).strip())
            except (TypeError, ValueError):
                continue
    return None


def _pick_str(node: dict, keys: tuple[str, ...], default: str = "") -> str:
    for key in keys:
        text = str(node.get(key) or "").strip()
        if text:
            return text
    return default


class VeespAdapter(ProviderAdapter):
    KIND = "veesp"
    TITLE = "Veesp"
    FIELDS = [
        CredField("email", "E-mail личного кабинета"),
        CredField("password", "Пароль личного кабинета", "password"),
    ]
    CAPS = {"balance", "payments"}

    async def _get(self, creds: dict, path: str) -> tuple[Any, str]:
        email = str((creds or {}).get("email") or "").strip()
        password = str((creds or {}).get("password") or "")
        try:
            async with self._client() as c:
                r = await c.get(f"{_BASE}{path}", auth=(email, password))
        except httpx.HTTPError as exc:
            return None, f"Veesp недоступен: {redact(str(exc), email, password)}"

        if r.status_code >= 400:
            return None, map_http_error(r.status_code)
        try:
            return r.json(), ""
        except ValueError:
            return None, "Veesp вернул не-JSON ответ"

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        _data, err = await self._get(creds, "/balance")
        return (False, err) if err else (True, "")

    async def balance(self, creds: dict) -> Optional[Balance]:
        if self.check_fields(creds):
            return None
        data, err = await self._get(creds, "/balance")
        if err:
            return None
        if not isinstance(data, dict):
            log.warning("veesp: unexpected /balance shape (not an object)")
            return None
        amount = _pick_number(data, _AMOUNT_KEYS)
        if amount is None:
            log.warning("veesp: no recognised amount key in /balance")
            return None
        return Balance(amount, _pick_str(data, _CURRENCY_KEYS, "EUR").upper())

    async def payments(self, creds: dict) -> list[dict]:
        if self.check_fields(creds):
            return []
        data, err = await self._get(creds, "/invoice")
        if err:
            return []
        rows = data
        if isinstance(data, dict):
            rows = data.get("invoices") or data.get("invoice") or data.get("data") or []
        if not isinstance(rows, list):
            return []
        out: list[dict] = []
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            out.append({
                "ts": _pick_str(raw, _TS_KEYS),
                "amount": _pick_number(raw, _INVOICE_AMOUNT_KEYS) or 0.0,
                "currency": _pick_str(raw, _CURRENCY_KEYS, "EUR").upper(),
                "type": "charge",
                "note": _pick_str(raw, ("status", "description", "name")),
            })
        return out


ADAPTER = VeespAdapter()
