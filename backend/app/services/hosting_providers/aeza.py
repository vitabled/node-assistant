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

Заказ (`order`) — единственная часть этого адаптера, которая НЕ угадана:
метод описан в собственной документации вендора (`AezaGroup/dev-docs`,
`t/service.md`), поэтому `CAPS` заявляет `order`.

- **`POST /services/orders`**, тело: `count`, `term`, `name`, `productId`,
  `parameters` (для VPS — `{"os": <id>}`), `autoProlong`, `method`. Каталог —
  `GET /services/products`, список ОС — `GET /os`.
- ⚠️ **`autoProlong` у вендора по умолчанию `true`, у нас — `false`.**
  Автопродление списывает деньги в будущем без нового подтверждения; включать
  его молча нельзя. То же решение принято для покупки домена в Cloudflare.
- ⚠️ **`count` жёстко равен 1.** В контракте заказа количества нет, а
  «случайно N серверов» — это оплаченная ошибка, а не опечатка.
- **`method` по умолчанию `balance`** — оплата с баланса. Это самый
  консервативный вариант: он не инициирует внешний платёж и просто не пройдёт,
  если денег нет.
- ⚠️ **Сразу после заказа `createdServiceIds` ПУСТ** (услуга ещё создаётся;
  вендор предлагает опрашивать `GET /services/orders/{id}`). Мы делаем ровно
  один POST и возвращаем идентификатор УСЛУГИ, если он уже приехал, иначе
  идентификатор ЗАКАЗА. Опрашивать не идём: ретраев у создающей операции нет, а
  ожидание в чужом API — не наша ответственность.
- ⚠️ **Ключи `prices` неоднозначны** (в одних ответах это сроки, в других —
  валюты), поэтому цена читается через `_product_price`, который различает оба
  случая и молчит, когда цену для нужного срока не назвали. Как и во всём
  остальном модуле, суммы НЕ делятся на 100 — см. `_money()`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.services.hosting_providers.base import (
    Balance,
    CredField,
    OrderOptions,
    OrderPlan,
    ProviderAdapter,
    ServiceItem,
    map_http_error,
    redact,
)

log = logging.getLogger("hosting.aeza")

_BASE = "https://my.aeza.net/api"

# Кандидаты пути баланса: берём первый ответивший (см. докстроку).
_BALANCE_PATHS = ("/account", "/customer", "/user")

_PRODUCTS_PATH = "/services/products"
_OS_PATH = "/os"
_ORDERS_PATH = "/services/orders"

# Срок оплаты. Один и тот же резолвер используется и в расчёте цены, и в заказе,
# поэтому подтверждённая сумма всегда относится к тому сроку, который уйдёт в
# запрос. Полного перечня сроков вендор не публикует («hour, month и т.д.»).
_TERM = {"hour": "hour", "hourly": "hour", "day": "day", "daily": "day",
         "month": "month", "monthly": "month", "1month": "month",
         "quarter": "quarter", "3month": "quarter",
         "year": "year", "yearly": "year", "12month": "year"}
_DEFAULT_TERM = "month"
# Написания, по которым узнаётся карта цен «по срокам», а не «по валютам».
_TERM_KEYS = {"hour", "day", "week", "month", "quarter",
              "halfyear", "half_year", "year"}

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


def _int_id(raw: Any) -> Optional[int]:
    """Целочисленный идентификатор (`productId`, `os`) или None.

    `bool` отсекается явно: в Python `True` — это `int`, и «id=True» уехало бы
    в тело заказа единицей."""
    if isinstance(raw, bool):
        return None
    value = _money(raw)
    return None if value is None else int(value)


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


def _term(raw: Any) -> str:
    return _TERM.get(str(raw or "").strip().lower(), _DEFAULT_TERM)


def _price_value(node: Any) -> Optional[float]:
    if isinstance(node, dict):
        return _pick_number(node, ("value", "price", "amount", "sum"))
    return _money(node)


def _product_price(product: dict, term: str) -> Optional[float]:
    """Цена продукта за указанный СРОК, либо `None`.

    ⚠️ `prices` бывает картой по срокам (`{"month": …}`) и картой по валютам
    (`{"rub": {"value", "slug", "defaultCurrency"}}`). Различаем по ключам:
    у карты сроков чужой срок — это НЕ цена нашего заказа, поэтому подставлять
    её вместо отсутствующей нельзя (пользователь подтвердил бы час вместо
    месяца). У карты валют берём валюту по умолчанию."""
    prices = product.get("prices")
    if not isinstance(prices, dict):
        return _pick_number(product, ("price", "cost", "summaryPrice"))
    lowered = {str(key).lower(): value for key, value in prices.items()}
    if any(key in _TERM_KEYS for key in lowered):
        return _price_value(lowered.get(term)) if term in lowered else None
    for node in prices.values():
        if isinstance(node, dict) and node.get("defaultCurrency"):
            value = _price_value(node)
            if value is not None:
                return value
    for node in prices.values():
        value = _price_value(node)
        if value is not None:
            return value
    return None


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
    CAPS = {"balance", "services", "payments", "order"}

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

    async def _post(self, creds: dict, path: str, body: dict) -> tuple[Any, str]:
        """РОВНО один POST, без ретраев: заказ тратит деньги, и таймаут не
        означает «не заказано»."""
        key = str((creds or {}).get("api_key") or "").strip()
        try:
            async with self._client() as c:
                r = await c.post(f"{_BASE}{path}", json=body, headers={
                    "X-API-Key": key,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                })
        except httpx.HTTPError as exc:
            return None, f"Aeza недоступна: {redact(str(exc), key)}"

        if r.status_code >= 400:
            try:
                text = _api_error(r.json())
            except ValueError:
                text = ""
            return None, redact(text, key) or map_http_error(r.status_code)
        try:
            data = r.json()
        except ValueError:
            return None, "Aeza вернула не-JSON ответ"
        # Aeza умеет ответить 200 с телом-ошибкой — для заказа это критично.
        err = _api_error(data)
        return (None, redact(err, key)) if err else (data, "")

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

    # ── Заказ ──────────────────────────────────────────────────
    async def order_options(self, creds: dict) -> Optional[OrderOptions]:
        if self.check_fields(creds):
            return None
        data, err, _status = await self._get(creds, _PRODUCTS_PATH)
        if err:
            return None
        rows = _items(data)
        if not rows:
            log.warning("aeza: no recognised items in %s", _PRODUCTS_PATH)
            return None
        oses, os_err, _os_status = await self._get(creds, _OS_PATH)

        plans: list[OrderPlan] = []
        for raw in rows:
            pid = _int_id(raw.get("id"))
            # Приватный продукт назначается администратором персонально —
            # предлагать его к самостоятельному заказу нельзя.
            if pid is None or raw.get("isPrivate"):
                continue
            group = raw.get("group")
            specs = _pick_str(group, ("name", "title")) if isinstance(group, dict) else ""
            install = _money(raw.get("installPrice")) or 0
            if install > 0:
                # Разовая плата за установку — это тоже деньги пользователя,
                # и в `price` контракта она не помещается.
                specs = (f"{specs} · " if specs else "") + f"установка {install:g}"
            plans.append(OrderPlan(
                id=str(pid),
                name=_pick_str(raw, _NAME_KEYS) or f"Продукт {pid}",
                specs=specs,
                price=_product_price(raw, _DEFAULT_TERM),
                currency=_pick_str(raw, _CURRENCY_KEYS, "RUB").upper(),
                period=_DEFAULT_TERM,
            ))

        images: list[dict] = []
        if not os_err:
            for raw in _items(oses):
                oid = _int_id(raw.get("id"))
                if oid is None:
                    continue
                images.append({
                    "id": str(oid),
                    "name": _pick_str(raw, _NAME_KEYS) or f"OS {oid}",
                })

        return OrderOptions(
            plans=plans,
            # Локация у Aeza зашита в сам продукт, отдельной ручки локаций в
            # документации нет — выдумывать список не из чего.
            regions=[],
            images=images,
            custom=None,  # готовые продукты, конструктора нет
        )

    async def _order_body(self, creds: dict, spec: dict) -> tuple[Optional[dict], str]:
        """Тело `POST /services/orders` по спецификации формы."""
        missing = self.check_fields(creds)
        if missing:
            return None, missing

        spec = spec or {}
        product_id = _int_id(spec.get("plan_id"))
        os_id = _int_id(spec.get("image"))
        name = str(spec.get("name") or "").strip()
        empty = [label for label, value in (
            ("тариф", product_id), ("образ ОС", os_id),
        ) if value is None]
        if not name:
            empty.append("имя услуги")
        if empty:
            return None, "не заполнено: " + ", ".join(empty)

        return {
            "count": 1,                       # никогда не больше одной услуги
            "term": _term(spec.get("period")),
            "name": name,
            "productId": product_id,
            "parameters": {"os": os_id},
            # Вендорское умолчание — true; см. докстроку модуля.
            "autoProlong": bool(spec.get("auto_prolong", False)),
            "method": str(spec.get("method") or "balance"),
        }, ""

    async def quote_order(self, creds: dict, spec: dict) -> Optional[dict]:
        """Цена продукта за ТОТ срок, который уйдёт в заказ."""
        spec = spec or {}
        product_id = _int_id(spec.get("plan_id"))
        if product_id is None or self.check_fields(creds):
            return None
        data, err, _status = await self._get(creds, _PRODUCTS_PATH)
        if err:
            return None
        for raw in _items(data):
            if _int_id(raw.get("id")) != product_id:
                continue
            price = _product_price(raw, _term(spec.get("period")))
            if price is None:
                return None
            return {"price": price,
                    "currency": _pick_str(raw, _CURRENCY_KEYS, "RUB").upper()}
        return None

    async def create_order(self, creds: dict, spec: dict) -> dict:
        fail = {"ok": False, "id": "", "name": "", "price": None, "currency": "RUB"}
        body, err = await self._order_body(creds, spec)
        if err or body is None:
            return {**fail, "error": err or "не удалось собрать заказ"}

        data, err = await self._post(creds, _ORDERS_PATH, body)
        if err:
            return {**fail, "error": err}
        node = _unwrap(data)
        if not isinstance(node, dict):
            return {**fail, "error": "Aeza приняла запрос, но не вернула заказ "
                                     "— проверьте ЛК перед повторной попыткой"}
        # Услуга создаётся асинхронно: сразу после заказа список пуст (докстрока).
        created = node.get("createdServiceIds")
        service_id = None
        if isinstance(created, list):
            service_id = next((_int_id(x) for x in created
                               if _int_id(x) is not None), None)
        order_id = _int_id(node.get("id"))
        if service_id is None and order_id is None:
            return {**fail, "error": "Aeza приняла запрос, но не вернула идентификатор "
                                     "— проверьте ЛК перед повторной попыткой"}
        return {
            "ok": True,
            "id": str(service_id if service_id is not None else order_id),
            "name": str(body["name"]),
            "price": None,
            # Валюту НЕ переопределяем. Ответ заказа её не называет, а карта
            # `prices` бывает и по валютам — подставив сюда «RUB», мы бы
            # переписали ту валюту, в которой пользователь подтвердил сумму
            # (маршрут предпочитает значение адаптера любому непустому).
            "currency": "",
            "error": "",
        }


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
