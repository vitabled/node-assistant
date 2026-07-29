"""IShosting — баланс, счета, услуги + заказ услуги. ДЕНЕГ НЕ ТРАТИТ.

`https://api.ishosting.com`, заголовок `X-Api-Token` (выпускается в личном
кабинете).

⚠️ **Запрет трогать оплату остаётся в силе.** У API есть ручки, которые ТРАТЯТ
ДЕНЬГИ — оплата счёта (`POST /billing/invoice/{id}/pay`) и пополнение баланса
(`POST /billing/balance/add`). Их здесь нет и быть не должно: адаптер дёргает
фоновый синк (`provider_sync.loop`) без участия человека, а списание средств по
расписанию — не то, на что подписывается пользователь, добавляя креды ради
показа баланса. Ловушка на эти пути стоит в тестах.

⚠️ **Заказ у is\\*hosting не платёж, и это важное свойство.** `POST /billing/order`
создаёт СЧЁТ (по документации он живёт 3 дня), а оплата — отдельное действие,
которого модуль не умеет. То есть кнопка «купить» здесь оформляет заказ, а
деньги списываются только когда пользователь сам оплатит счёт в панели. Поле
оплаты в тело заказа не кладётся вовсе — иначе запрет выше обходился бы одним
параметром.

Каталог и заказ (сверено с официальным клиентом вендора `ishosting/ishosting-manager`):

- `GET /vps/plans` — тарифы; у каждого `code` (вида `29_1m` — тариф и срок
  оплаты одной строкой) и `location: {code, name}`. Отдельной ручки локаций нет:
  и в клиенте вендора они собираются из тарифов.
- `GET /vps/configs/{code}` — опции ПЕР-ТАРИФНЫЕ (ОС, панели, железо), у каждой
  `code` и `category.code`. Общего каталога ОС нет, поэтому образы собираются по
  нескольким первым тарифам (потолок `_MAX_OS_PLANS`), а каждый несёт
  `allowed_plans` — приём `hostkey.py`.
- `POST /billing/order` — тело `{"items": [{action, identity, type, plan,
  quantity, additions}]}`, где локация и ОС едут ДОБАВЛЕНИЯМИ
  (`{"code": …, "category": "country"|"os"}`), а не отдельными полями. В ответе
  `id` — номер счёта.
- `POST /billing/order/validate` — то же тело, но ничего не создаёт и возвращает
  актуальные цены; это и есть `quote_order`.

⚠️ **Формы ответов на живом аккаунте не снимались** — читатели написаны защитно:
неузнанная форма даёт `None`/`[]` и warning в лог, а не выдуманное число.
Читающие пути `/billing/balance`, `/billing/invoice` и `/services` подтверждения
не имеют (в клиенте вендора те же данные лежат в `/profile`,
`/billing/invoices` и `/vps/list`), поэтому у каждого есть запасной путь: он
пробуется, только если основной ответил ошибкой или нераспознаваемым телом.
"""
from __future__ import annotations

import logging
import uuid
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

log = logging.getLogger("hosting.ishosting")

_BASE = "https://api.ishosting.com"

_BALANCE_PATH = "/billing/balance"
_INVOICES_PATH = "/billing/invoice"
_SERVICES_PATH = "/services"
# Запасные пути — из официального клиента вендора; пробуются только при отказе
# основного (см. шапку).
_BALANCE_FALLBACK = "/profile"
_INVOICES_FALLBACK = "/billing/invoices"
_SERVICES_FALLBACK = "/vps/list"

_PLANS_PATH = "/vps/plans"
_CONFIGS_PATH = "/vps/configs"
_ORDER_PATH = "/billing/order"
_ORDER_VALIDATE_PATH = "/billing/order/validate"

# Список ОС пер-тарифный, общего каталога нет: полный обход стоил бы запроса на
# каждый тариф и превратил бы открытие формы в десятки обращений.
_MAX_OS_PLANS = 6
_PER_PAGE = 100

# Срок оплаты зашит в код тарифа (`29_6m`) — разворачиваем в слова контракта.
_PERIODS = {"1m": "month", "3m": "quarter", "6m": "half_year", "1y": "year",
            "12m": "year"}

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
    CAPS = {"balance", "services", "payments", "order"}

    async def _get(self, creds: dict, path: str,
                   params: Optional[dict] = None) -> tuple[Any, str]:
        """Единственная ЧИТАЮЩАЯ сетевая функция модуля (см. шапку)."""
        token = str((creds or {}).get("api_token") or "").strip()
        try:
            async with self._client() as c:
                r = await c.get(f"{_BASE}{path}", params=params, headers={
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

    async def _post(self, creds: dict, path: str, body: dict) -> tuple[Any, str]:
        """POST только по путям заказа (`/billing/order[/validate]`) — оплату
        модуль не делает вовсе. РОВНО один запрос, без ретраев."""
        token = str((creds or {}).get("api_token") or "").strip()
        try:
            async with self._client() as c:
                r = await c.post(f"{_BASE}{path}", json=body, headers={
                    "X-Api-Token": token,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                })
        except httpx.HTTPError as exc:
            return None, f"IShosting недоступен: {redact(str(exc), token)}"

        try:
            data = r.json()
        except ValueError:
            data = None
        if r.status_code >= 400:
            # Слова вендора («plan is not available») объясняют отказ точнее
            # общей фразы по коду.
            text = _error_text(data)
            base = map_http_error(r.status_code)
            if r.status_code in (401, 403) or not text:
                return None, base
            return None, redact(f"{base}: {text}", token)
        text = _error_text(data, ("error", "errors"))
        if text:
            # Отказ приезжает и с HTTP 200 — его обязан поймать разбор тела.
            return None, redact(text, token)
        return data, ""

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        _data, err = await self._get(creds, _BALANCE_PATH)
        return (False, err) if err else (True, "")

    async def _balance_at(self, creds: dict, path: str) -> Optional[Balance]:
        data, err = await self._get(creds, path)
        if err:
            return None
        node = _unwrap(data)
        if node is None:
            log.warning("ishosting: неожиданная форма %s", path)
            return None
        amount = _number(node, _AMOUNT_KEYS)
        if amount is None:
            log.warning("ishosting: в ответе %s нет узнаваемой суммы", path)
            return None
        return Balance(amount, _text(node, _CURRENCY_KEYS, "EUR").upper())

    async def balance(self, creds: dict) -> Optional[Balance]:
        if self.check_fields(creds):
            return None
        found = await self._balance_at(creds, _BALANCE_PATH)
        if found is None:
            # Запасной путь из клиента вендора — см. шапку модуля.
            found = await self._balance_at(creds, _BALANCE_FALLBACK)
        return found

    async def services(self, creds: dict) -> list[ServiceItem]:
        if self.check_fields(creds):
            return []
        data, err = await self._get(creds, _SERVICES_PATH)
        if err:
            data, err = await self._get(creds, _SERVICES_FALLBACK)
        if err:
            return []
        out: list[ServiceItem] = []
        for raw in _rows(data, "services", "vps"):
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
            data, err = await self._get(creds, _INVOICES_FALLBACK)
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

    # ── Заказ ──────────────────────────────────────────────────
    async def order_options(self, creds: dict) -> Optional[OrderOptions]:
        if self.check_fields(creds):
            return None
        data, err = await self._get(creds, _PLANS_PATH,
                                    {"limit": _PER_PAGE, "page": 1})
        if err:
            return None
        rows = _rows(data, "plans")
        if not rows:
            return None

        plans: list[OrderPlan] = []
        regions: dict[str, dict] = {}
        for raw in rows:
            code = str(raw.get("code") or "").strip()
            if not code:
                continue
            location = raw.get("location") if isinstance(raw.get("location"), dict) else {}
            loc_code = str(location.get("code") or "").strip()
            plans.append(OrderPlan(
                id=code,
                name=_text(raw, _NAME_KEYS, code),
                specs=_plan_specs(raw),
                price=_plan_price(raw),
                currency=_text(raw, _CURRENCY_KEYS, "EUR").upper(),
                period=_period_of(code),
                region=loc_code,
            ))
            if loc_code:
                entry = regions.setdefault(loc_code, {
                    "id": loc_code,
                    "name": str(location.get("name") or loc_code),
                    # Не каждый тариф есть в каждой локации — форма не должна
                    # предлагать заведомый отказ.
                    "plans": [],
                })
                entry["plans"].append(code)

        images: dict[str, dict] = {}
        for plan in plans[:_MAX_OS_PLANS]:
            configs, cfg_err = await self._get(creds, f"{_CONFIGS_PATH}/{plan.id}")
            if cfg_err:
                # Тариф без опций не должен обнулять весь каталог.
                continue
            for option in _os_options(configs):
                code = str(option.get("code") or "").strip()
                if not code:
                    continue
                entry = images.setdefault(code, {
                    "id": code,
                    "name": _text(option, _NAME_KEYS, code),
                    # Совместимость едет с образом — приём `hostkey.py`.
                    "allowed_plans": [],
                })
                entry["allowed_plans"].append(plan.id)

        return OrderOptions(plans=plans, regions=list(regions.values()),
                            images=list(images.values()),
                            # Размеры у is*hosting фиксированные (тариф + опции),
                            # конструктора нет.
                            custom=None)

    def _order_body(self, spec: dict) -> tuple[Optional[dict], str]:
        """Тело `/billing/order[/validate]`. Чистое: одно и то же и для расчёта,
        и для покупки — иначе цена считалась бы не для того заказа."""
        spec = spec or {}
        plan = str(spec.get("plan_id") or "").strip()
        location = str(spec.get("region") or "").strip()
        empty = ([] if plan else ["тариф"]) + ([] if location else ["локация"])
        if empty:
            return None, "не заполнено: " + ", ".join(empty)

        additions = [{"code": location, "category": "country"}]
        image = str(spec.get("image") or "").strip()
        if image:
            additions.append({"code": image, "category": "os"})
        return {"items": [{
            "action": "new",
            # Идентификатор позиции в рамках заказа — так его формирует клиент
            # вендора.
            "identity": uuid.uuid4().hex[:16],
            "type": "vps",
            "plan": plan,
            "quantity": 1,
            "additions": additions,
            # Полей оплаты здесь нет намеренно: заказ создаёт счёт, платит
            # пользователь сам (см. шапку модуля).
        }]}, ""

    async def quote_order(self, creds: dict, spec: dict) -> Optional[dict]:
        """Сумма заказа словами вендора, БЕЗ создания: `/billing/order/validate`."""
        if self.check_fields(creds):
            return None
        body, err = self._order_body(spec)
        if err or body is None:
            return None
        data, err = await self._post(creds, _ORDER_VALIDATE_PATH, body)
        if err:
            return None
        price = _order_total(data)
        if price is None:
            return None
        node = _unwrap(data) or {}
        return {"price": price, "currency": _text(node, _CURRENCY_KEYS, "EUR").upper()}

    async def create_order(self, creds: dict, spec: dict) -> dict:
        """Оформляет заказ. Денег НЕ списывает: вендор выставляет счёт, а оплата
        — отдельное действие, которого этот модуль не умеет."""
        fail = {"ok": False, "id": "", "name": "", "price": None, "currency": "EUR"}
        missing = self.check_fields(creds)
        if missing:
            return {**fail, "error": missing}
        body, err = self._order_body(spec)
        if err or body is None:
            return {**fail, "error": err or "не удалось собрать заказ"}

        data, err = await self._post(creds, _ORDER_PATH, body)
        if err:
            return {**fail, "error": err}
        node = _unwrap(data) or {}
        invoice = str(node.get("id") or (data or {}).get("id") or "").strip() \
            if isinstance(data, dict) else ""
        if not invoice:
            # Заказ мог пройти: молча сказать «не получилось» опаснее просьбы
            # заглянуть в панель — счёт уже мог быть выставлен.
            return {**fail, "error": "IShosting принял запрос, но не вернул номер счёта "
                                     "— проверьте панель перед повторной попыткой"}
        return {
            "ok": True,
            # Идентификатор заказа у вендора — номер счёта; сервер появится
            # после его оплаты в панели.
            "id": invoice,
            "name": str((spec or {}).get("name") or "").strip(),
            "price": _order_total(data),
            "currency": _text(node, _CURRENCY_KEYS, "EUR").upper(),
            "error": "",
        }


def _error_text(data: Any, keys: tuple[str, ...] = ("error", "errors", "message")) -> str:
    """Текст ошибки: `error` бывает строкой, объектом и списком сообщений.

    ⚠️ На успешном ответе `message` НЕ читаем: у вендора это бывает подпись вроде
    «Order created», и принять её за отказ значит сообщить о неудаче там, где
    счёт уже выставлен. Поэтому 2xx проверяется только по `error`/`errors`."""
    if not isinstance(data, dict):
        return ""
    for key in keys:
        node = data.get(key)
        if isinstance(node, dict):
            text = str(node.get("message") or node.get("text") or "").strip()
            if text:
                return text
        elif isinstance(node, list):
            parts = [str(x).strip() for x in node if str(x or "").strip()]
            if parts:
                return "; ".join(parts)
        elif str(node or "").strip():
            return str(node).strip()
    return ""


def _period_of(code: str) -> str:
    suffix = code.rsplit("_", 1)[-1].strip().lower() if "_" in code else ""
    return _PERIODS.get(suffix, "month")


def _plan_price(raw: dict) -> Optional[float]:
    """Цена тарифа: числом в корне либо вложенным объектом."""
    price = _number(raw, ("price", "cost", "amount", "total", "sum"))
    if price is not None:
        return price
    for key in ("price", "cost", "prices"):
        node = raw.get(key)
        if isinstance(node, dict):
            found = _number(node, ("value", "amount", "total", "price", "sum"))
            if found is not None:
                return found
    return None


def _plan_specs(raw: dict) -> str:
    """Характеристики тарифа. Форма их не документирует, поэтому берём то, что
    узнали, и молчим об остальном — строка чисто справочная."""
    parts: list[str] = []
    cpu = _number(raw, ("cpu", "cpu_count", "cores", "vcpu"))
    if cpu:
        parts.append(f"{cpu:g} vCPU")
    ram = _number(raw, ("ram", "ram_gb", "memory"))
    if ram:
        parts.append(f"{ram:g} ГБ RAM")
    disk = _number(raw, ("disk", "disk_gb", "ssd", "storage"))
    if disk:
        parts.append(f"{disk:g} ГБ диск")
    text = _text(raw, ("description", "specs", "summary"))
    if text:
        parts.append(text)
    return " · ".join(parts)


def _os_options(payload: Any) -> list[dict]:
    """Опции категории «os» из ответа `/vps/configs/{code}`.

    Форма ответа не документирована и у разных типов услуг отличается, поэтому
    обходим дерево: опцией считаем словарь с `code`, у которого категория (полем
    `category` или именем контейнера) — `os`. Так переименование обёртки не
    оставляет форму без образов."""
    found: list[dict] = []

    def category_of(node: dict) -> str:
        cat = node.get("category")
        if isinstance(cat, dict):
            return str(cat.get("code") or cat.get("name") or "").strip().lower()
        return str(cat or "").strip().lower()

    def walk(node: Any, container: str) -> None:
        if isinstance(node, dict):
            if node.get("code") and (category_of(node) == "os" or container == "os"):
                found.append(node)
                return
            for key, value in node.items():
                walk(value, str(key).strip().lower())
        elif isinstance(node, list):
            for item in node:
                walk(item, container)

    walk(payload, "")
    return found


def _order_total(data: Any) -> Optional[float]:
    """Сумма заказа из ответа расчёта/создания — там, где вендор её называет."""
    node = _unwrap(data)
    if not isinstance(node, dict):
        return None
    total = _number(node, ("total", "amount", "sum", "price", "grand_total"))
    if total is not None:
        return total
    for key in ("order", "invoice", "totals"):
        inner = node.get(key)
        if isinstance(inner, dict):
            total = _number(inner, ("total", "amount", "sum", "price"))
            if total is not None:
                return total
    return None


ADAPTER = IshostingAdapter()
