"""IShosting — баланс, счета, услуги. ТОЛЬКО ЧТЕНИЕ.

`https://api.ishosting.com`, заголовок `X-Api-Token` (выпускается в личном
кабинете).

⚠️ **Адаптер намеренно read-only, и это не «пока не сделали».** У API есть ручки,
которые ТРАТЯТ ДЕНЬГИ — оплата счёта (`POST /billing/invoice/{id}/pay`) и
пополнение баланса. Здесь их нет и быть не должно: адаптер дёргает фоновый синк
(`provider_sync.loop`) без участия человека, а списание средств по расписанию —
не то, на что подписывается пользователь, добавляя креды ради показа баланса.
Структурная гарантия: в модуле ровно ОДНА сетевая функция (`_get`), и она делает
GET. Появится вторая — правило сломается молча, поэтому проверка на это есть в
тестах (ловушка на не-GET и на путях оплаты).

⚠️ **Формы ответов на живом аккаунте не снимались.** Твёрдо известен только
неймспейс `/billing/...` (из него выведен путь оплаты выше), поэтому читатели
написаны защитно: неузнанная форма даёт `None`/`[]` и warning в лог, а не
выдуманное число. Это первое место, которое захочется поправить, когда появится
живой аккаунт.
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

log = logging.getLogger("hosting.ishosting")

_BASE = "https://api.ishosting.com"

_BALANCE_PATH = "/billing/balance"
_INVOICES_PATH = "/billing/invoice"
_SERVICES_PATH = "/services"

_AMOUNT_KEYS = ("balance", "amount", "value", "sum", "total")
_CURRENCY_KEYS = ("currency", "currency_code", "curr", "code")
_TS_KEYS = ("date", "created", "created_at", "issued_at", "due_date", "dt")
_INVOICE_AMOUNT_KEYS = ("total", "amount", "sum", "grand_total", "price")
_NAME_KEYS = ("name", "title", "hostname", "label", "domain")
_STATUS_KEYS = ("status", "state")
_IP_KEYS = ("ip", "ip_address", "ipv4", "main_ip")
_REGION_KEYS = ("location", "region", "datacenter", "dc", "country")
_PAID_TILL_KEYS = ("paid_till", "expires_at", "expire_date", "next_payment", "valid_till")


def _number(node: dict, keys: tuple[str, ...]) -> Optional[float]:
    """Первый из `keys`, который приводится к числу (суммы могут быть строками)."""
    for key in keys:
        if key in node:
            try:
                return float(str(node[key]).strip())
            except (TypeError, ValueError):
                continue
    return None


def _text(node: dict, keys: tuple[str, ...], default: str = "") -> str:
    for key in keys:
        value = str(node.get(key) or "").strip()
        if value:
            return value
    return default


def _unwrap(data: Any) -> Optional[dict]:
    """Объект ответа: сам по себе или под обёрткой `data`/`result`/`balance`."""
    if not isinstance(data, dict):
        return None
    for key in ("data", "result", "balance", "account"):
        inner = data.get(key)
        if isinstance(inner, dict):
            return inner
    return data


def _rows(data: Any, *keys: str) -> list[dict]:
    """Список записей: голый массив или обёртка `{key: [...]}`."""
    rows: Any = data
    if isinstance(data, dict):
        for key in keys + ("data", "items", "result", "list"):
            candidate = data.get(key)
            if isinstance(candidate, list):
                rows = candidate
                break
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


class IshostingAdapter(ProviderAdapter):
    KIND = "ishosting"
    TITLE = "IShosting"
    FIELDS = [CredField("api_token", "API-токен", "password")]
    CAPS = {"balance", "services", "payments"}

    async def _get(self, creds: dict, path: str) -> tuple[Any, str]:
        """ЕДИНСТВЕННАЯ сетевая функция модуля — и она только читает (см. шапку)."""
        token = str((creds or {}).get("api_token") or "").strip()
        try:
            async with self._client() as c:
                r = await c.get(f"{_BASE}{path}", headers={
                    "X-Api-Token": token,
                    "Accept": "application/json",
                })
        except httpx.HTTPError as exc:
            return None, f"IShosting недоступен: {redact(str(exc), token)}"

        if r.status_code >= 400:
            return None, map_http_error(r.status_code)
        try:
            return r.json(), ""
        except ValueError:
            return None, "IShosting вернул не-JSON ответ"

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        _data, err = await self._get(creds, _BALANCE_PATH)
        return (False, err) if err else (True, "")

    async def balance(self, creds: dict) -> Optional[Balance]:
        if self.check_fields(creds):
            return None
        data, err = await self._get(creds, _BALANCE_PATH)
        if err:
            return None
        node = _unwrap(data)
        if node is None:
            log.warning("ishosting: неожиданная форма %s", _BALANCE_PATH)
            return None
        amount = _number(node, _AMOUNT_KEYS)
        if amount is None:
            log.warning("ishosting: в ответе %s нет узнаваемой суммы", _BALANCE_PATH)
            return None
        return Balance(amount, _text(node, _CURRENCY_KEYS, "EUR").upper())

    async def services(self, creds: dict) -> list[ServiceItem]:
        if self.check_fields(creds):
            return []
        data, err = await self._get(creds, _SERVICES_PATH)
        if err:
            return []
        out: list[ServiceItem] = []
        for raw in _rows(data, "services"):
            sid = str(raw.get("id") or raw.get("uuid") or "")
            out.append(ServiceItem(
                id=sid,
                name=_text(raw, _NAME_KEYS, f"услуга {sid}".strip()),
                kind=str(raw.get("type") or raw.get("product") or "vps"),
                cost=_number(raw, ("price", "cost", "amount")),
                currency=_text(raw, _CURRENCY_KEYS, "EUR").upper(),
                period=str(raw.get("period") or raw.get("billing_cycle") or "month"),
                status=_text(raw, _STATUS_KEYS),
                ip=_text(raw, _IP_KEYS),
                region=_text(raw, _REGION_KEYS),
                paid_till=_text(raw, _PAID_TILL_KEYS),
            ))
        return out

    async def payments(self, creds: dict) -> list[dict]:
        """Счета. Счёт — это НАЧИСЛЕНИЕ, поэтому тип всегда `charge`: ручки
        «оплатить» мы не трогаем и о фактах оплаты из этого ответа не судим."""
        if self.check_fields(creds):
            return []
        data, err = await self._get(creds, _INVOICES_PATH)
        if err:
            return []
        out: list[dict] = []
        for raw in _rows(data, "invoices", "invoice"):
            out.append({
                "ts": _text(raw, _TS_KEYS),
                "amount": _number(raw, _INVOICE_AMOUNT_KEYS) or 0.0,
                "currency": _text(raw, _CURRENCY_KEYS, "EUR").upper(),
                "type": "charge",
                "note": _text(raw, ("status", "description", "number", "name")),
            })
        return out


ADAPTER = IshostingAdapter()
