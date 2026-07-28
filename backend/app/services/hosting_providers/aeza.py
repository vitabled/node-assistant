"""Aeza adapter — balance, services, transactions.

`https://my.aeza.net/api`, header `X-API-Key: <ключ>` (личный кабинет → API).

⚠️ **Публичная документация Aeza заархивирована (август 2023)**, поэтому каждый
читатель здесь написан «терпимо»: ключи ищутся по списку правдоподобных написаний,
конверт `{"data": …}` снимается если он есть, а неузнанная форма даёт `None`/`[]`
и запись в лог — но НИКОГДА не выдуманное число и не исключение.

Что именно неизвестно и как это обходится:

- **Путь баланса в архиве не зафиксирован.** Пробуем короткий список известных
  вариантов и останавливаемся на первом, который вообще ответил; 404/405 →
  следующий, 401/403 → сразу выходим (это ответ про креды, а не про путь).
  Ничего не ответило → `None`, то есть «баланс вручную», а не ложный ноль.
  Поэтому же `verify()` проверяется по `/services` — иначе неверно угаданный путь
  баланса выглядел бы как неверные креды.
- **⚠️ Единицы денег НЕ преобразуются.** Есть основания считать, что Aeza отдаёт
  суммы в минорных единицах (копейках), но проверить это не на чем. Делить на 100
  вслепую — это гарантированная ошибка в 100 раз, если предположение неверно, а
  показать сырое число хотя бы честно. Если на живом аккаунте баланс окажется в
  100 раз больше — правка ровно в `_money()`.
- **Время** приходит epoch-ом (секунды или миллисекунды) — переводится в ISO-UTC,
  иначе в интерфейсе была бы строка «1690000000000».
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
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

log = logging.getLogger("hosting.aeza")

_BASE = "https://my.aeza.net/api"

# Кандидаты пути баланса: берём первый ответивший (см. докстроку).
_BALANCE_PATHS = ("/account", "/customer", "/user")

_AMOUNT_KEYS = ("balance", "amount", "value", "sum", "money", "funds", "total")
_CURRENCY_KEYS = ("currency", "currencyCode", "currency_code", "curr")
_NAME_KEYS = ("name", "displayName", "hostname", "title", "label")
_STATUS_KEYS = ("status", "state")
_IP_KEYS = ("ip", "ipv4", "primaryIp", "primary_ip", "address")
_TS_KEYS = ("createdAt", "created_at", "created", "date", "ts", "time")
_EXPIRE_KEYS = ("expiresAt", "expires_at", "expireAt", "expire", "paidTill",
                "paid_till", "endAt")
_PERIOD_KEYS = ("paymentTerm", "payment_term", "period", "term", "billingPeriod")


def _money(raw: Any) -> Optional[float]:
    """Число как есть. Никаких делений на 100 — см. предупреждение в докстроке."""
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _pick_number(node: dict, keys: tuple[str, ...]) -> Optional[float]:
    for key in keys:
        if key in node:
            value = _money(node[key])
            if value is not None:
                return value
    return None


def _pick_str(node: dict, keys: tuple[str, ...], default: str = "") -> str:
    for key in keys:
        value = node.get(key)
        if isinstance(value, dict):          # {"name": …} у вложенных объектов
            value = value.get("name") or value.get("title")
        text = str(value or "").strip()
        if text:
            return text
    return default


def _ts(raw: Any) -> str:
    """epoch (сек или мс) → ISO-UTC; строка остаётся строкой."""
    if isinstance(raw, bool) or raw is None:
        return ""
    if isinstance(raw, (int, float)):
        seconds = float(raw)
        # Порог отделяет миллисекунды от секунд: 1e11 с — это год 5138.
        if seconds > 1e11:
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return ""
    return str(raw).strip()


def _unwrap(data: Any) -> Any:
    """Снимает конверт `{"data": …}`, если он есть."""
    if isinstance(data, dict) and isinstance(data.get("data"), (dict, list)):
        return data["data"]
    return data


def _items(payload: Any) -> list[dict]:
    """Список сущностей из `[…]`, `{"items": […]}` или `{"data": {"items": […]}}`."""
    node = _unwrap(payload)
    if isinstance(node, dict):
        for key in ("items", "list", "results", "services", "transactions"):
            if isinstance(node.get(key), list):
                node = node[key]
                break
    return [row for row in node if isinstance(row, dict)] if isinstance(node, list) else []


def _api_error(data: Any) -> str:
    """Текст ошибки из тела ответа, "" если тело выглядит нормальным."""
    if not isinstance(data, dict):
        return ""
    err = data.get("error")
    if isinstance(err, dict):
        return (str(err.get("message") or err.get("slug") or "").strip()
                or "Aeza вернула ошибку без описания")
    if isinstance(err, str) and err.strip():
        return err.strip()
    return ""


class AezaAdapter(ProviderAdapter):
    KIND = "aeza"
    TITLE = "Aeza"
    FIELDS = [CredField("api_key", "API-ключ", "password")]
    CAPS = {"balance", "services", "payments"}

    async def _get(self, creds: dict, path: str) -> tuple[Any, str, int]:
        """→ (payload, error, http-статус). Статус нужен вызывающему, чтобы
        отличить «не тот путь» (404) от «не те креды» (401)."""
        key = str((creds or {}).get("api_key") or "").strip()
        try:
            async with self._client() as c:
                r = await c.get(f"{_BASE}{path}", headers={
                    "X-API-Key": key,
                    "Accept": "application/json",
                })
        except httpx.HTTPError as exc:
            return None, f"Aeza недоступна: {redact(str(exc), key)}", 0

        if r.status_code >= 400:
            try:
                text = _api_error(r.json())
            except ValueError:
                text = ""
            return None, redact(text, key) or map_http_error(r.status_code), r.status_code
        try:
            data = r.json()
        except ValueError:
            return None, "Aeza вернула не-JSON ответ", r.status_code

        err = _api_error(data)
        if err:
            return None, redact(err, key), r.status_code
        return data, "", r.status_code

    async def _balance_payload(self, creds: dict) -> tuple[Any, str]:
        """Первый путь из `_BALANCE_PATHS`, который ответил."""
        last = "Aeza не отдала баланс ни по одному известному пути"
        for path in _BALANCE_PATHS:
            data, err, status = await self._get(creds, path)
            if not err:
                return data, ""
            if status in (401, 403):
                return None, err          # это про креды — дальше искать бессмысленно
            if status in (404, 405):
                continue                  # не тот путь — пробуем следующий
            return None, err              # сеть/500 — тоже терминально
        return None, last

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        # Именно /services: это единственный путь, в котором мы уверены, а значит
        # неверно угаданный путь баланса не превратится в «неверные креды».
        _data, err, _status = await self._get(creds, "/services")
        return (False, err) if err else (True, "")

    async def balance(self, creds: dict) -> Optional[Balance]:
        if self.check_fields(creds):
            return None
        data, err = await self._balance_payload(creds)
        if err or data is None:
            return None
        node = _unwrap(data)
        if not isinstance(node, dict):
            log.warning("aeza: unexpected balance shape")
            return None
        amount = _pick_number(node, _AMOUNT_KEYS)
        currency = _pick_str(node, _CURRENCY_KEYS)
        if amount is None:
            # Баланс может лежать вложенным объектом: {"balance": {"value": …}}.
            nested = node.get("balance")
            if isinstance(nested, dict):
                amount = _pick_number(nested, _AMOUNT_KEYS)
                currency = currency or _pick_str(nested, _CURRENCY_KEYS)
        if amount is None:
            log.warning("aeza: no recognised amount key in balance payload")
            return None
        return Balance(amount, (currency or "RUB").upper())

    async def services(self, creds: dict) -> list[ServiceItem]:
        if self.check_fields(creds):
            return []
        data, err, _status = await self._get(creds, "/services")
        if err:
            return []
        rows = _items(data)
        if not rows:
            log.warning("aeza: no recognised items in /services")
        return [_service_item(raw) for raw in rows]

    async def payments(self, creds: dict) -> list[dict]:
        if self.check_fields(creds):
            return []
        data, err, _status = await self._get(creds, "/transactions")
        if err:
            return []
        return [_transaction(raw) for raw in _items(data)]


def _period(raw: dict) -> str:
    text = _pick_str(raw, _PERIOD_KEYS).lower()
    if not text:
        return "month"
    for needle, period in (("hour", "hour"), ("час", "hour"),
                           ("year", "year"), ("год", "year"),
                           ("quarter", "quarter"), ("week", "week"),
                           ("day", "day"), ("month", "month"), ("мес", "month")):
        if needle in text:
            return period
    return text


def _service_ip(raw: dict) -> str:
    ip = _pick_str(raw, _IP_KEYS)
    if ip:
        return ip
    nets = raw.get("ips") or raw.get("addresses") or raw.get("network")
    if isinstance(nets, list) and nets:
        first = nets[0]
        if isinstance(first, dict):
            return _pick_str(first, _IP_KEYS)
        return str(first or "").strip()
    return ""


def _service_item(raw: dict) -> ServiceItem:
    sid = raw.get("id") or raw.get("uuid") or ""
    product = raw.get("product")
    kind = ""
    if isinstance(product, dict):
        kind = str(product.get("type") or product.get("group") or "").strip()
    return ServiceItem(
        id=str(sid),
        name=_pick_str(raw, _NAME_KEYS) or f"услуга #{sid}",
        kind=kind or "vps",
        cost=_pick_number(raw, ("summaryPrice", "price", "cost", "sum")),
        currency=_pick_str(raw, _CURRENCY_KEYS, "RUB").upper(),
        period=_period(raw),
        status=_pick_str(raw, _STATUS_KEYS),
        ip=_service_ip(raw),
        region=_pick_str(raw, ("location", "region", "locationCode", "datacenter")),
        paid_till=_ts(next((raw[k] for k in _EXPIRE_KEYS if k in raw), None)),
    )


def _transaction(raw: dict) -> dict:
    amount = _pick_number(raw, _AMOUNT_KEYS) or 0.0
    kind = _pick_str(raw, ("type", "kind", "operation")).lower()
    topup = amount > 0 or any(w in kind for w in ("top", "deposit", "refill", "in"))
    return {
        "ts": _ts(next((raw[k] for k in _TS_KEYS if k in raw), None)),
        "amount": abs(amount),
        "currency": _pick_str(raw, _CURRENCY_KEYS, "RUB").upper(),
        "type": "topup" if topup else "charge",
        "note": _pick_str(raw, ("description", "comment", "note", "name")),
    }


ADAPTER = AezaAdapter()
