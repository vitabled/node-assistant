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
- `/service` — список услуг: `{"services": [{id, name, domain, total, status,
  billingcycle, next_due, category}]}`. ⚠️ Путь снят не с живого аккаунта, а с
  опубликованного разбора клиентского API (там же `/details`,
  `/service/{id}/vms`), поэтому читатель такой же защитный: неузнанная форма даёт
  пустой список, а не выдуманные услуги. IP в этот ответ не входит — он лежит в
  `/service/{id}/vms`, то есть в отдельном запросе НА КАЖДУЮ услугу; ради одной
  колонки такой веер не делаем.

⚠️ **Заказ: `CAPS` НЕ заявляет `order`, и это не недоделка.** Публичная
документация клиентского API (`secure.veesp.com/userapi`) закрыта JS-проверкой и
через неё не читается, а по косвенным упоминаниям видны только каталожные ручки
(`/category/{id}/product`) — САМОЙ ручки оформления заказа подтвердить не
удалось. Кнопка, которая молча ничего не создаёт (или, хуже, создаёт не то,
списав деньги), опаснее её отсутствия, поэтому `create_order` отказывает словами
и БЕЗ сетевого запроса. Когда появится доступ к документации, здесь нужен
каталог продуктов + одно создание — остальное в модуле уже есть.
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

log = logging.getLogger("hosting.veesp")

_BASE = "https://secure.veesp.com/api"

_ORDER_UNSUPPORTED = (
    "Veesp не подтверждает оформление заказа через API — оформите услугу в "
    "личном кабинете"
)

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


def _category(node: Any) -> str:
    """Категория услуги: строкой или объектом `{name: …}`."""
    if isinstance(node, dict):
        return str(node.get("name") or node.get("title") or "vps").strip() or "vps"
    return str(node or "vps").strip() or "vps"


class VeespAdapter(ProviderAdapter):
    KIND = "veesp"
    TITLE = "Veesp"
    FIELDS = [
        CredField("email", "E-mail личного кабинета"),
        CredField("password", "Пароль личного кабинета", "password"),
    ]
    CAPS = {"balance", "services", "payments"}

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

    async def services(self, creds: dict) -> list[ServiceItem]:
        if self.check_fields(creds):
            return []
        data, err = await self._get(creds, "/service")
        if err:
            return []
        rows = data
        if isinstance(data, dict):
            rows = data.get("services") or data.get("service") or data.get("data") or []
        if not isinstance(rows, list):
            log.warning("veesp: unexpected /service shape")
            return []
        out: list[ServiceItem] = []
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            sid = str(raw.get("id") or "")
            out.append(ServiceItem(
                id=sid,
                name=_pick_str(raw, ("name", "domain", "product"), f"услуга {sid}".strip()),
                # `category` бывает и строкой, и объектом с именем.
                kind=_category(raw.get("category")),
                cost=_pick_number(raw, ("total", "amount", "price", "cost")),
                currency=_pick_str(raw, _CURRENCY_KEYS, "EUR").upper(),
                period=_pick_str(raw, ("billingcycle", "billing_cycle", "period"),
                                 "month"),
                status=_pick_str(raw, ("status", "state")),
                # IP живёт в отдельном `/service/{id}/vms` — см. докстроку модуля.
                ip="",
                region=_pick_str(raw, ("location", "region", "datacenter")),
                paid_till=_pick_str(raw, ("next_due", "nextduedate", "paid_till")),
            ))
        return out

    async def create_order(self, creds: dict, spec: dict) -> dict:
        """Отказ БЕЗ сетевого запроса: ручка заказа не подтверждена (см. шапку)."""
        return {"ok": False, "id": "", "name": "", "price": None, "currency": "EUR",
                "error": _ORDER_UNSUPPORTED}

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
