"""Latitude.sh — серверы (bare metal) + использование.

База `https://api.latitude.sh`, заголовок `Authorization: Bearer <токен>`
(личный кабинет → API Keys).

⚠️ **Ответы в формате JSON:API**, а не «плоским» объектом: строка выглядит как
`{"id": …, "type": "servers", "attributes": {…}}`, и все реальные поля лежат
именно в `attributes`. Читать `row["hostname"]` бесполезно — вернётся пусто, а не
ошибка, поэтому распаковка вынесена в `_attrs()` и применяется ко ВСЕМ строкам.

**Баланса нет и он не заявлен в CAPS.** У Latitude постоплата по использованию:
остатка средств в API нет, есть агрегат потребления — он и уходит в `payments()`.

⚠️ Форма ответа `/billing/usage` на живом аккаунте не снималась: суммы и период
читаются из списка правдоподобных имён, незнакомая форма даёт `[]` и warning в
лог. Стоимость конкретного сервера в `/servers` не приходит (цена живёт в плане,
это отдельная ручка `/plans`), поэтому `cost` у услуг честно `None`, а не ноль.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.services.hosting_providers.base import (
    CredField,
    ProviderAdapter,
    ServiceItem,
    map_http_error,
    redact,
)

log = logging.getLogger("hosting.latitude")

_BASE = "https://api.latitude.sh"

_NAME_KEYS = ("hostname", "label", "name")
_STATUS_KEYS = ("status", "state")
_IP_KEYS = ("primary_ipv4", "ipv4", "ip_address", "ip")
_TS_KEYS = ("period_start", "start_date", "created_at", "date", "billing_date",
            "period")
_AMOUNT_KEYS = ("amount", "total", "price", "cost", "amount_due", "total_price")
_CURRENCY_KEYS = ("currency", "currency_code")
_NOTE_KEYS = ("description", "name", "product", "type", "status")


def _num(value: Any) -> Optional[float]:
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def _pick_number(node: dict, keys: tuple[str, ...]) -> Optional[float]:
    for key in keys:
        if key in node:
            found = _num(node[key])
            if found is not None:
                return found
    return None


def _pick_str(node: dict, keys: tuple[str, ...], default: str = "") -> str:
    for key in keys:
        value = node.get(key)
        if isinstance(value, (dict, list)):
            continue
        text = str(value or "").strip()
        if text:
            return text
    return default


def _rows(payload: Any) -> list[dict]:
    """Строки JSON:API-коллекции (`{"data": [...]}`) либо голого списка."""
    raw = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [row for row in raw if isinstance(row, dict)]


def _attrs(row: dict) -> dict:
    """Поля записи: JSON:API прячет их в `attributes` (см. докстроку)."""
    attrs = row.get("attributes")
    return {**row, **attrs} if isinstance(attrs, dict) else row


class LatitudeAdapter(ProviderAdapter):
    KIND = "latitude"
    TITLE = "Latitude.sh"
    FIELDS = [CredField("token", "API-токен", "password")]
    # Без "balance": у Latitude постоплата, остатка средств в API нет.
    CAPS = {"services", "payments"}

    async def _get(self, creds: dict, path: str) -> tuple[Any, str]:
        token = str((creds or {}).get("token") or "").strip()
        try:
            async with self._client() as c:
                r = await c.get(f"{_BASE}{path}", headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                })
        except httpx.HTTPError as exc:
            return None, "Latitude.sh недоступен: " + redact(str(exc), token)

        if r.status_code >= 400:
            return None, map_http_error(r.status_code)
        try:
            return r.json(), ""
        except ValueError:
            return None, "Latitude.sh вернул не-JSON ответ"

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        _data, err = await self._get(creds, "/servers")
        return (False, err) if err else (True, "")

    async def services(self, creds: dict) -> list[ServiceItem]:
        if self.check_fields(creds):
            return []
        data, err = await self._get(creds, "/servers")
        if err:
            return []
        out: list[ServiceItem] = []
        for row in _rows(data):
            attrs = _attrs(row)
            sid = str(row.get("id") or attrs.get("id") or "")
            plan = attrs.get("plan")
            plan_name = ""
            if isinstance(plan, dict):
                plan_name = _pick_str(plan, ("name", "slug"))
            region = attrs.get("region")
            region_name = ""
            if isinstance(region, dict):
                site = region.get("site")
                if isinstance(site, dict):
                    region_name = _pick_str(site, ("slug", "name", "facility"))
                region_name = region_name or _pick_str(region, ("city", "country"))
            out.append(ServiceItem(
                id=sid,
                name=_pick_str(attrs, _NAME_KEYS) or f"server {sid}",
                kind=plan_name or "bare-metal",
                # Цена лежит в плане, а не в сервере — см. докстроку.
                cost=None,
                currency="USD",
                period="month",
                status=_pick_str(attrs, _STATUS_KEYS),
                ip=_pick_str(attrs, _IP_KEYS),
                region=region_name,
            ))
        return out

    async def payments(self, creds: dict) -> list[dict]:
        if self.check_fields(creds):
            return []
        data, err = await self._get(creds, "/billing/usage")
        if err:
            return []
        rows = _rows(data)
        if not rows:
            if data:
                log.warning("latitude: незнакомая форма ответа /billing/usage")
            return []
        out: list[dict] = []
        for row in rows:
            attrs = _attrs(row)
            out.append({
                "ts": _pick_str(attrs, _TS_KEYS),
                "amount": _pick_number(attrs, _AMOUNT_KEYS),
                "currency": _pick_str(attrs, _CURRENCY_KEYS, "USD").upper(),
                # Использование — это начисление, а не платёж.
                "type": "charge",
                "note": _pick_str(attrs, _NOTE_KEYS),
            })
        return out


ADAPTER = LatitudeAdapter()
