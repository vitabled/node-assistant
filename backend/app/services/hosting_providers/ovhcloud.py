"""OVHcloud — счета (`/me/bill`). Самая сложная авторизация в пакете.

Каждый запрос подписывается тремя ключами (Application Key / Application Secret /
Consumer Key, выпускаются на https://api.ovh.com/createToken/). Подпись:

    X-Ovh-Signature = "$1$" + sha1_hex(AS + "+" + CK + "+" + METHOD + "+"
                                       + FULL_URL + "+" + BODY + "+" + TS)

и рядом заголовки `X-Ovh-Application`, `X-Ovh-Consumer`, `X-Ovh-Timestamp`.
Слагаемых шесть, порядок значим, и ошибиться в нём — это ровно `401`, неотличимый
от неверных ключей. Поэтому склейка живёт в ОДНОЙ чистой функции
`ovh_signature()`, которую тест пересобирает независимо по формуле.

Три ловушки, каждая стоит 401:

- **⚠️ TS берётся с сервера OVH** (`GET /auth/time`, без подписи), НЕ локальный
  `time.time()`: расхождение часов машины ломает подпись. Разница
  «сервер − локально» кэшируется на инстанс адаптера (как в официальном SDK) —
  дёргать `/auth/time` перед каждым запросом значит удваивать трафик и упираться
  в лимиты.
- **FULL_URL подписывается целиком, вместе с query.** URL собирается один раз
  строкой и в этом же виде уходит в httpx; параметр `date.from` намеренно
  передаётся в формате `YYYY-MM-DD` — там нет символов, которые httpx мог бы
  перекодировать, иначе подписанная и отправленная строки разошлись бы.
- **METHOD в верхнем регистре**, тело для GET — пустая строка (не `None`).

**Баланса нет и он не заявлен в CAPS.** У OVH постоплата по счетам: «остатка
средств» в API нет вовсе (`/me/credit/balance` — это ваучеры и кредит-ноты, а не
счёт клиента), поэтому `balance()` отдаёт `None` — честное «баланс вручную».

⚠️ **US живёт на другом домене** — `api.us.ovhcloud.com`, а НЕ `us.api.ovh.com`.

**⚠️ Заказ: `CAPS` НЕ заявляет `order`, и это не недоделка.** У OVHcloud покупка
идёт через КОРЗИНУ, и это не один запрос, а последовательность
`POST /order/cart` → `POST /order/cart/{id}/assign` →
`POST /order/cart/{id}/{product}` → `POST …/item/{itemId}/configuration` (по
одному на каждый обязательный параметр) → `POST /order/cart/{id}/checkout`.
Состав обязательных параметров СВОЙ у каждого предложения и узнаётся отдельной
ручкой `requiredConfiguration`, то есть предсказуемой последовательности,
которую можно покрыть тестом, здесь нет. А последний шаг — checkout — уже
списывает деньги. Поэтому `create_order` отказывает СЛОВАМИ и без единого
запроса: кнопка, которая наполовину соберёт корзину и бросит её, хуже
отсутствия кнопки.

Каталог предложений при этом честный и живой: `order_options()` читает
публичный `GET /order/catalog/public/vps?ovhSubsidiary=…` — это справка, по
которой видно, что вообще предлагают и почём. Как и у `selectel`, через маршрут
он сейчас недостижим (ручка `order-options` гейтится на `CAPS`) и станет
доступен, если корзину когда-нибудь реализуют.
"""
from __future__ import annotations

import hashlib
import logging
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from app.services.hosting_providers.base import (
    CredField,
    OrderOptions,
    OrderPlan,
    ProviderAdapter,
    map_http_error,
    redact,
)

log = logging.getLogger("hosting.ovhcloud")

_ENDPOINTS = {
    "eu": "https://eu.api.ovh.com/1.0",
    "ca": "https://ca.api.ovh.com/1.0",
    # ⚠️ Не us.api.ovh.com — у US-региона отдельный домен.
    "us": "https://api.us.ovhcloud.com/1.0",
}
_DEFAULT_ENDPOINT = "eu"

# Счета за полгода: /me/bill без фильтра отдаёт идентификаторы за всё время, а
# порядок в списке не документирован — по дате отобрать надёжнее, чем «с конца».
_HISTORY_DAYS = 180
_MAX_BILLS = 24

# Каталог отдаёт цены ЦЕЛЫМИ микро-центами: 3.59 € приезжает как 359000000.
_CATALOG_UNIT = 100_000_000
_MAX_PLANS = 60

_ORDER_UNSUPPORTED = (
    "OVHcloud оформляет заказ через корзину: cart → assign → item → "
    "configuration (свой набор параметров у каждого предложения) → checkout, "
    "и checkout сразу списывает деньги. Предсказуемой одношаговой "
    "последовательности здесь нет — оформите заказ в панели OVHcloud"
)


def endpoint_base(name: str) -> str:
    """Регион → база API. Неизвестное значение → "" (адаптер откажет явно)."""
    key = str(name or "").strip().lower() or _DEFAULT_ENDPOINT
    return _ENDPOINTS.get(key, "")


def ovh_signature(app_secret: str, consumer_key: str, method: str, url: str,
                  body: str, timestamp: Any) -> str:
    """Подпись запроса. Чистая функция: порядок склейки проверяется тестом."""
    raw = "+".join([
        str(app_secret or ""),
        str(consumer_key or ""),
        str(method or "").upper(),
        str(url or ""),
        str(body or ""),
        str(timestamp),
    ])
    return "$1$" + hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _num(value: Any) -> Optional[float]:
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def catalog_price(pricings: Any) -> tuple[Optional[float], str]:
    """(цена продления, период) из блока `pricings` публичного каталога. Чистая.

    ⚠️ **Единица — микро-центы**: делитель зафиксирован тестом, потому что сумма,
    показанная в сто миллионов раз больше реальной, страшнее её отсутствия.
    Берём тариф продления (`renew`), а не установки: именно он повторяется
    каждый месяц. Форму `{"value": …}` (так отвечает корзина) тоже принимаем."""
    rows = [p for p in pricings if isinstance(p, dict)] if isinstance(pricings, list) else []
    chosen = next((p for p in rows
                   if "renew" in (p.get("capacities") or [])), None)
    if chosen is None and rows:
        chosen = rows[0]
    if chosen is None:
        return None, ""

    raw = chosen.get("price")
    if isinstance(raw, dict):
        price = _num(raw.get("value"))
    else:
        micro = _num(raw)
        price = round(micro / _CATALOG_UNIT, 2) if micro is not None else None

    unit = str(chosen.get("intervalUnit") or "").strip().lower()
    interval = _num(chosen.get("interval")) or 1
    period = unit if interval == 1 else f"{interval:g}{unit}"
    return price, period


class OvhcloudAdapter(ProviderAdapter):
    KIND = "ovhcloud"
    TITLE = "OVHcloud"
    FIELDS = [
        CredField("application_key", "Application Key"),
        CredField("application_secret", "Application Secret", "password"),
        CredField("consumer_key", "Consumer Key", "password"),
        CredField("endpoint", "Регион API: eu / ca / us", "text", required=False),
    ]
    # Без "balance": у OVH нет счёта клиента, только счета-фактуры.
    CAPS = {"payments"}

    def __init__(self) -> None:
        # Разница «сервер OVH − локальные часы», по одной на регион.
        self._delta: dict[str, int] = {}

    def _secrets(self, creds: dict) -> tuple[str, ...]:
        creds = creds or {}
        return (str(creds.get("application_secret") or ""),
                str(creds.get("consumer_key") or ""))

    def _base(self, creds: dict) -> tuple[str, str]:
        base = endpoint_base((creds or {}).get("endpoint") or "")
        if not base:
            return "", "неизвестный регион API: допустимы eu, ca, us"
        return base, ""

    async def _time(self, base: str, creds: dict) -> tuple[int, str]:
        """Метка времени по часам OVH (см. докстроку — локальные не годятся)."""
        delta = self._delta.get(base)
        if delta is None:
            try:
                async with self._client() as c:
                    r = await c.get(f"{base}/auth/time")
            except httpx.HTTPError as exc:
                return 0, "OVHcloud недоступен: " + redact(str(exc),
                                                           *self._secrets(creds))
            if r.status_code >= 400:
                return 0, map_http_error(r.status_code)
            try:
                server = int(r.text.strip())
            except (TypeError, ValueError):
                return 0, "OVHcloud вернул нечисловое время"
            delta = server - int(time.time())
            self._delta[base] = delta
        return int(time.time()) + delta, ""

    async def _request(self, creds: dict, method: str,
                       path: str) -> tuple[Any, str, int]:
        missing = self.check_fields(creds)
        if missing:
            return None, missing, 0
        base, err = self._base(creds)
        if err:
            return None, err, 0
        timestamp, err = await self._time(base, creds)
        if err:
            return None, err, 0

        creds = creds or {}
        app_key = str(creds.get("application_key") or "").strip()
        app_secret = str(creds.get("application_secret") or "").strip()
        consumer = str(creds.get("consumer_key") or "").strip()
        url = f"{base}{path}"
        # Тело только у POST/PUT; для GET подписывается пустая строка.
        body = ""
        headers = {
            "X-Ovh-Application": app_key,
            "X-Ovh-Consumer": consumer,
            "X-Ovh-Timestamp": str(timestamp),
            "X-Ovh-Signature": ovh_signature(app_secret, consumer, method, url,
                                             body, timestamp),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            async with self._client() as c:
                r = await c.request(method.upper(), url, headers=headers)
        except httpx.HTTPError as exc:
            return None, ("OVHcloud недоступен: "
                          + redact(str(exc), *self._secrets(creds))), 0

        if r.status_code >= 400:
            return None, map_http_error(r.status_code), r.status_code
        try:
            return r.json(), "", r.status_code
        except ValueError:
            return None, "OVHcloud вернул не-JSON ответ", r.status_code

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        _data, err, _status = await self._request(creds, "GET", "/me")
        return (False, err) if err else (True, "")

    async def payments(self, creds: dict) -> list[dict]:
        if self.check_fields(creds):
            return []
        since = (datetime.now(timezone.utc)
                 - timedelta(days=_HISTORY_DAYS)).strftime("%Y-%m-%d")
        ids, err, status = await self._request(
            creds, "GET", f"/me/bill?date.from={since}")
        if status == 400:
            # Фильтр не принят этой версией API — берём список целиком.
            ids, err, status = await self._request(creds, "GET", "/me/bill")
        if err or not isinstance(ids, list):
            return []

        out: list[dict] = []
        for bill_id in ids[-_MAX_BILLS:]:
            quoted = urllib.parse.quote(str(bill_id), safe="")
            if not quoted:
                continue
            data, err, _status = await self._request(
                creds, "GET", f"/me/bill/{quoted}")
            if err or not isinstance(data, dict):
                continue
            price = data.get("priceWithTax")
            if not isinstance(price, dict):
                price = data.get("priceWithoutTax")
            price = price if isinstance(price, dict) else {}
            out.append({
                "ts": str(data.get("date") or ""),
                "amount": _num(price.get("value")),
                "currency": str(price.get("currencyCode") or "EUR").upper(),
                # /me/bill — это счета, то есть всегда начисления.
                "type": "charge",
                "note": str(data.get("billId") or bill_id),
            })
        out.sort(key=lambda row: str(row.get("ts") or ""), reverse=True)
        return out

    # ── Заказ ──────────────────────────────────────────────────
    async def order_options(self, creds: dict) -> Optional[OrderOptions]:
        """Справочный каталог VPS. Заказ отсюда НЕ оформляется (см. докстринг)."""
        if self.check_fields(creds):
            return None
        me, err, _status = await self._request(creds, "GET", "/me")
        if err or not isinstance(me, dict):
            return None
        subsidiary = str(me.get("ovhSubsidiary") or "").strip().upper()
        if not subsidiary.isalpha() or len(subsidiary) > 5:
            # Подставлять «FR» нельзя: это чужие цены в чужой валюте.
            log.warning("ovhcloud: непонятный ovhSubsidiary — каталог не строим")
            return None

        data, err, _status = await self._request(
            creds, "GET", f"/order/catalog/public/vps?ovhSubsidiary={subsidiary}")
        if err or not isinstance(data, dict):
            return None
        locale = data.get("locale") if isinstance(data.get("locale"), dict) else {}
        currency = str(locale.get("currencyCode")
                       or ((me.get("currency") or {}).get("code")
                           if isinstance(me.get("currency"), dict) else "")
                       or "").upper()

        plans: list[OrderPlan] = []
        for row in (data.get("plans") or []):
            if not isinstance(row, dict):
                continue
            code = str(row.get("planCode") or "").strip()
            if not code:
                continue
            price, period = catalog_price(row.get("pricings"))
            plans.append(OrderPlan(
                id=code,
                name=str(row.get("invoiceName") or code),
                specs=str(row.get("description") or "").strip(),
                price=price,
                currency=currency,
                period=period or "month",
            ))
        del plans[_MAX_PLANS:]
        # Площадка и ОС у OVH — это `configuration` позиции корзины, а корзины у
        # нас нет: списки пустые, чтобы форма не обещала выбор, которого нет.
        return OrderOptions(plans=plans, regions=[], images=[], custom=None)

    async def create_order(self, creds: dict, spec: dict) -> dict:
        """Честный отказ БЕЗ единого запроса — см. докстринг модуля."""
        return {"ok": False, "id": "", "name": "", "price": None, "currency": "",
                "error": _ORDER_UNSUPPORTED}


ADAPTER = OvhcloudAdapter()
