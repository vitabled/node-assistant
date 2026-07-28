"""VDSina adapter — баланс + список серверов.

`https://userapi.vdsina.com` (зеркало `userapi.vdsina.ru`).

Два места, где легко ошибиться:

- **⚠️ Токен идёт в `Authorization` БЕЗ схемы**: `Authorization: <token>`, а не
  `Bearer <token>`. С «Bearer» приходит 401, неотличимый от неверного токена.
- **Конверт двойной**: `{"status": "ok"|"error", "status_msg": "…", "data": …}`.
  HTTP при этом может быть 200, поэтому `status: "error"` обязан читаться как
  ошибка — иначе пустой `data` выглядел бы как «нет серверов».

Баланс берём из `/v1/account` (он приходит в теле аккаунта), а если там его в
узнаваемом виде нет — из `/v1/account.balance`. Поле `balance` бывает объектом
`{"real": …, "bonus": …, "partner": …}`: показываем **real** — это живые деньги,
а не бонусы, которыми нельзя оплатить всё.
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

log = logging.getLogger("hosting.vdsina")

_BASE = "https://userapi.vdsina.com"

# «real» первым: это деньги, а не бонусы.
_AMOUNT_KEYS = ("real", "balance", "amount", "value", "total")
_CURRENCY_KEYS = ("currency", "currency_code", "curr")


def _pick_number(node: dict, keys: tuple[str, ...]) -> Optional[float]:
    for key in keys:
        if key in node:
            try:
                return float(str(node[key]).strip())
            except (TypeError, ValueError):
                continue
    return None


def _pick_str(node: dict, keys: tuple[str, ...], default: str = "") -> str:
    for key in keys:
        value = node.get(key)
        if isinstance(value, dict):
            value = value.get("name")
        text = str(value or "").strip()
        if text:
            return text
    return default


class VdsinaAdapter(ProviderAdapter):
    KIND = "vdsina"
    TITLE = "VDSina"
    FIELDS = [CredField("token", "API-токен", "password")]
    CAPS = {"balance", "services"}

    async def _get(self, creds: dict, path: str) -> tuple[Any, str]:
        """→ (`data`, error). Снимает конверт и переводит `status: error` в текст."""
        token = str((creds or {}).get("token") or "").strip()
        try:
            async with self._client() as c:
                r = await c.get(f"{_BASE}{path}", headers={
                    # Без «Bearer» — так требует VDSina.
                    "Authorization": token,
                    "Accept": "application/json",
                })
        except httpx.HTTPError as exc:
            return None, f"VDSina недоступна: {redact(str(exc), token)}"

        if r.status_code >= 400:
            return None, map_http_error(r.status_code)
        try:
            body = r.json()
        except ValueError:
            return None, "VDSina вернула не-JSON ответ"

        if not isinstance(body, dict):
            return None, "неожиданный формат ответа VDSina"
        if str(body.get("status") or "").lower() == "error":
            text = str(body.get("status_msg") or "").strip()
            return None, redact(text, token) or "VDSina вернула ошибку без описания"
        return body.get("data"), ""

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        _data, err = await self._get(creds, "/v1/account")
        return (False, err) if err else (True, "")

    async def balance(self, creds: dict) -> Optional[Balance]:
        if self.check_fields(creds):
            return None
        data, err = await self._get(creds, "/v1/account")
        currency = ""
        amount = None
        if not err and isinstance(data, dict):
            currency = _pick_str(data, _CURRENCY_KEYS)
            node = data.get("balance")
            if isinstance(node, dict):
                amount = _pick_number(node, _AMOUNT_KEYS)
                currency = currency or _pick_str(node, _CURRENCY_KEYS)
            else:
                amount = _pick_number(data, ("balance", "amount", "value"))
        if amount is None:
            amount, extra = await self._balance_endpoint(creds)
            currency = currency or extra
        if amount is None:
            log.warning("vdsina: no recognised balance in /v1/account")
            return None
        return Balance(amount, (currency or "RUB").upper())

    async def _balance_endpoint(self, creds: dict) -> tuple[Optional[float], str]:
        data, err = await self._get(creds, "/v1/account.balance")
        if err:
            return None, ""
        if isinstance(data, dict):
            return _pick_number(data, _AMOUNT_KEYS), _pick_str(data, _CURRENCY_KEYS)
        try:
            return float(str(data).strip()), ""
        except (TypeError, ValueError):
            return None, ""

    async def services(self, creds: dict) -> list[ServiceItem]:
        if self.check_fields(creds):
            return []
        data, err = await self._get(creds, "/v1/server")
        if err:
            return []
        if not isinstance(data, list):
            log.warning("vdsina: unexpected /v1/server shape")
            return []
        return [_server_item(raw) for raw in data if isinstance(raw, dict)]


def _server_ip(raw: dict) -> str:
    nets = raw.get("ip")
    if isinstance(nets, list):
        for entry in nets:
            if isinstance(entry, dict) and str(entry.get("ip") or "").strip():
                return str(entry["ip"]).strip()
            if isinstance(entry, str) and entry.strip():
                return entry.strip()
    return str(nets or "").strip() if isinstance(nets, str) else ""


def _server_item(raw: dict) -> ServiceItem:
    sid = str(raw.get("id") or "")
    plan = raw.get("server-plan") or raw.get("server_plan") or {}
    plan = plan if isinstance(plan, dict) else {}
    return ServiceItem(
        id=sid,
        name=str(raw.get("name") or "").strip() or f"сервер #{sid}",
        kind=str(plan.get("name") or "vps").strip() or "vps",
        cost=_pick_number(plan, ("cost", "price")),
        currency="RUB",
        period=str(plan.get("period") or "month").strip() or "month",
        status=str(raw.get("status") or ""),
        ip=_server_ip(raw),
        region=_pick_str(raw, ("datacenter", "location")),
        # `end` — дата окончания оплаченного периода.
        paid_till=str(raw.get("end") or "").strip(),
    )


ADAPTER = VdsinaAdapter()
