"""HostKey — баланс, услуги, счета. ТОЛЬКО ЧТЕНИЕ.

`https://invapi.hostkey.com` («inventory API»), токен из личного кабинета уходит
заголовком `Authorization: Bearer <token>`.

Две ручки закрывают все три возможности, и это не совпадение:

- `/auth/billing_list` — биллинговая сводка аккаунта: остаток на счету И перечень
  оплачиваемых услуг. Отсюда и `balance`, и `services` — отдельного «списка
  серверов» мы не выдумываем, чтобы не получить молча пустой список из
  несуществующего пути.
- `/auth/show_invoices` — счета, они же `payments`.

⚠️ **Оплата и пополнение баланса не реализованы намеренно** — ровно та же
причина, что в `ishosting.py`: адаптер вызывает фоновый синк без человека, и
тратить деньги по расписанию он не должен. В модуле одна сетевая функция, и она
делает GET.

⚠️ Не проверено на живом аккаунте: (а) префикс `/auth` у списка счетов — если
вендор отдаёт его без префикса, правится в `_INVOICES_PATH` одной строкой;
(б) точные имена полей — читатели написаны защитно, неузнанная форма даёт
`None`/`[]` и warning, а не выдуманное число.
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

log = logging.getLogger("hosting.hostkey")

_BASE = "https://invapi.hostkey.com"

# Оба пути под общим префиксом /auth (см. оговорку в шапке модуля).
_BILLING_PATH = "/auth/billing_list"
_INVOICES_PATH = "/auth/show_invoices"

_AMOUNT_KEYS = ("balance", "account_balance", "amount", "sum", "value", "money")
_CURRENCY_KEYS = ("currency", "currency_code", "curr", "code")
_TS_KEYS = ("date", "invoice_date", "created", "created_at", "dt", "due_date")
_INVOICE_AMOUNT_KEYS = ("total", "amount", "sum", "cost", "price")
_NAME_KEYS = ("name", "title", "hostname", "server_name", "description", "product")
_STATUS_KEYS = ("status", "state")
_IP_KEYS = ("ip", "ip_address", "ipv4", "main_ip")
_REGION_KEYS = ("location", "region", "datacenter", "dc", "country")
_PAID_TILL_KEYS = ("paid_till", "expires", "expire_date", "next_payment", "valid_till")


def _number(node: dict, keys: tuple[str, ...]) -> Optional[float]:
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


class HostkeyAdapter(ProviderAdapter):
    KIND = "hostkey"
    TITLE = "HostKey"
    FIELDS = [CredField("token", "API-токен", "password")]
    CAPS = {"balance", "services", "payments"}

    async def _get(self, creds: dict, path: str) -> tuple[Any, str]:
        """ЕДИНСТВЕННАЯ сетевая функция модуля — и она только читает (см. шапку)."""
        token = str((creds or {}).get("token") or "").strip()
        try:
            async with self._client() as c:
                r = await c.get(f"{_BASE}{path}", headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                })
        except httpx.HTTPError as exc:
            return None, f"HostKey недоступен: {redact(str(exc), token)}"

        if r.status_code >= 400:
            return None, map_http_error(r.status_code)
        try:
            return r.json(), ""
        except ValueError:
            return None, "HostKey вернул не-JSON ответ"

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        _data, err = await self._get(creds, _BILLING_PATH)
        return (False, err) if err else (True, "")

    async def balance(self, creds: dict) -> Optional[Balance]:
        if self.check_fields(creds):
            return None
        data, err = await self._get(creds, _BILLING_PATH)
        if err:
            return None
        node = data if isinstance(data, dict) else None
        if node is None:
            log.warning("hostkey: неожиданная форма %s", _BILLING_PATH)
            return None
        # Остаток может лежать и в корне, и во вложенном объекте аккаунта —
        # перечень услуг в том же ответе лежит рядом, поэтому корень не обязан
        # быть «плоским».
        amount = _number(node, _AMOUNT_KEYS)
        if amount is None:
            for key in ("account", "billing", "data", "result"):
                inner = node.get(key)
                if isinstance(inner, dict):
                    amount = _number(inner, _AMOUNT_KEYS)
                    if amount is not None:
                        node = inner
                        break
        if amount is None:
            log.warning("hostkey: в ответе %s нет узнаваемого остатка", _BILLING_PATH)
            return None
        return Balance(amount, _text(node, _CURRENCY_KEYS, "EUR").upper())

    async def services(self, creds: dict) -> list[ServiceItem]:
        """Оплачиваемые позиции из той же биллинговой сводки, что и баланс."""
        if self.check_fields(creds):
            return []
        data, err = await self._get(creds, _BILLING_PATH)
        if err:
            return []
        out: list[ServiceItem] = []
        for raw in _rows(data, "billing", "services", "invoices_items"):
            sid = str(raw.get("id") or raw.get("service_id") or raw.get("uuid") or "")
            out.append(ServiceItem(
                id=sid,
                name=_text(raw, _NAME_KEYS, f"услуга {sid}".strip()),
                kind=str(raw.get("type") or raw.get("product_type") or "server"),
                cost=_number(raw, ("cost", "price", "amount", "sum")),
                currency=_text(raw, _CURRENCY_KEYS, "EUR").upper(),
                period=str(raw.get("period") or raw.get("billing_cycle") or "month"),
                status=_text(raw, _STATUS_KEYS),
                ip=_text(raw, _IP_KEYS),
                region=_text(raw, _REGION_KEYS),
                paid_till=_text(raw, _PAID_TILL_KEYS),
            ))
        return out

    async def payments(self, creds: dict) -> list[dict]:
        """Счета = начисления, поэтому тип всегда `charge`. Факт оплаты из этого
        ответа не выводим: ручки оплаты адаптер не трогает (см. шапку)."""
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


ADAPTER = HostkeyAdapter()
