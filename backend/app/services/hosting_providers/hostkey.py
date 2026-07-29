"""HostKey — баланс, услуги, счета. ТОЛЬКО ЧТЕНИЕ.

`https://invapi.hostkey.com` («inventory API»), токен из личного кабинета уходит
заголовком `Authorization: Bearer <token>`.

Две ручки закрывают все три возможности, и это не совпадение:

- `/auth/billing_list` — биллинговая сводка аккаунта: остаток на счету И перечень
  оплачиваемых услуг. Отсюда и `balance`, и `services` — отдельного «списка
  серверов» мы не выдумываем, чтобы не получить молча пустой список из
  несуществующего пути.
- `/auth/show_invoices` — счета, они же `payments`.

⚠️ **Оплата счетов и пополнение баланса не реализованы намеренно** — ровно та же
причина, что в `ishosting.py`: эти ручки дёргает фоновый синк без человека, и
тратить деньги по расписанию он не должен. Платежи у HostKey живут в `whmcs.php`
(`getcredits`, `get_invoices`, `getpaymentgw`) — этого раздела модуль не знает
вовсе, и запрет остаётся в силе. Все читающие методы по-прежнему делают только
GET; единственный не-GET в модуле — заказ сервера, и он происходит по явному
подтверждению пользователя.

Заказ (`order`) — документированный invapi, и транспорт у него ДРУГОЙ:

- **Токен едет полем формы `token`, а не заголовком.** Так это описано в
  документации вендора (`curl … --data "action=order_instance" --data "token=…"`),
  и так же устроены `presets.php` / `os.php`. Читающие методы выше ходят
  заголовком `Authorization: Bearer` — это место в модуле не проверено на живом
  аккаунте (см. ниже), поэтому подгонять документированный путь заказа под
  непроверенный не стали.
- Каталог: `POST /presets.php action=list` → `presets[]`
  (`id`, `name`, `active`, `cpu`/`ram`/`hdd`, `monthly_com` — цена в EUR **без
  местного НДС**, `price` — вложенный объект `{локация: {"EUR": …, "USD": …}}`).
  ⚠️ **`locations` — СТРОКА через запятую** (`"NL,US,FI,DE,IS,TR,UK"`), а не
  список; на этом легко ошибиться. Отдельной ручки локаций нет, поэтому `regions`
  собираются из пресетов и несут список доступных там пресетов (приём `ruvds.py`).
  ⚠️ **`active` — число `1`/`0`**, а не bool.
- Образы: `POST /os.php action=list` c `instance_id` = id ПРЕСЕТА → `os_list[]`
  (`id`, `name`, `active`). Список ОС **пер-пресетный** (совместимость зависит от
  железа), общего каталога нет — поэтому образы собираются по нескольким первым
  пресетам (потолок `_MAX_OS_PRESETS`), а каждый образ несёт `allowed_presets`,
  как `allowed_flavors` у `servers_com.py`. Полный обход всех пресетов стоил бы
  запроса на каждый и превратил бы открытие формы в десятки обращений.
- Создание: `POST /eq.php action=order_instance` с `preset`, `os_id`,
  `location_name`, `hostname`, `deploy_period`. **Заказ оплачивается с кредитного
  счёта сразу** (вендор сперва проверяет остаток) — в ответе бывает
  `status: "Paid"`. Локацию указывать обязательно: без неё вендор отвечает
  ошибкой совместимости ОС.
- ⚠️ `deploy_period` — слова вендора `monthly` / `quarterly` / `semi-annual` /
  `yearly`. Не WHMCS-форма (`semiannually`/`annually`): у HostKey документированы
  именно эти написания.
- Форма ответа плавает между сборками: у одной `{"result":"OK","id":123,…}`, у
  другой `{"result":"OK","invoice":50062,"status":"Paid"}`. Читаем `id`, иначе
  номер счёта — оба однозначно идентифицируют заказ в панели.
- **Пароль root мы не задаём и не читаем.** `root_pass` числится среди параметров
  заказа, но в контракте заказа поля для секрета нет: сгенерировать пароль и не
  вернуть его значит выдать пользователю сервер, в который он не войдёт. Без
  параметра доступы уходят на почту аккаунта (`deploy_notify`). Если конкретная
  сборка invapi всё же потребует его — вендор откажет ДО списания, и его текст
  дойдёт до пользователя дословно.

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
    OrderOptions,
    OrderPlan,
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

# Документированные точки заказа. Никаких путей `whmcs.php` здесь нет и быть не
# должно: там живут счета и их оплата (см. шапку модуля).
_PRESETS_PATH = "/presets.php"
_OS_PATH = "/os.php"
_ORDER_PATH = "/eq.php"

# Период оплаты словами вендора. Незнакомое значение сводится к САМОМУ КОРОТКОМУ
# сроку, а не к самому дорогому: ошибка маппинга не должна списывать за год.
_DEPLOY_PERIOD = {
    "month": "monthly", "1month": "monthly",
    "quarter": "quarterly", "3month": "quarterly",
    "half_year": "semi-annual", "6month": "semi-annual",
    "year": "yearly", "12month": "yearly",
}
_DEFAULT_DEPLOY_PERIOD = "monthly"

# Сколько пресетов опросить ради списка ОС (список пер-пресетный, см. шапку).
_MAX_OS_PRESETS = 6

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


def _api_error(data: Any) -> str:
    """Текст ошибки invapi, "" — тело выглядит нормальным.

    Ошибка приезжает с HTTP 200, поэтому статуса мало. Разбираем три формы:
    явный `error`, `result` не «OK» и документированный `{"code": -1, "message"}`.

    ⚠️ Отрицательный `code` — намеренно, а не любой ненулевой: у читающих ручек
    в теле может оказаться `code: 200`, и проверка «!= 0» превратила бы успешный
    ответ в ошибку. Голый `message` без признака отказа тоже не трогаем."""
    if not isinstance(data, dict):
        return ""

    err = data.get("error")
    if isinstance(err, dict):
        return (str(err.get("message") or err.get("msg") or "").strip()
                or "HostKey вернул ошибку без описания")
    if isinstance(err, str) and err.strip():
        return err.strip()

    failed = False
    result = str(data.get("result") or "").strip()
    if result:
        failed = result.upper() not in ("OK", "SUCCESS", "TRUE")
    if not failed:
        try:
            failed = int(str(data.get("code")).strip()) < 0
        except (TypeError, ValueError):
            failed = False
    if not failed:
        return ""
    return (str(data.get("message") or data.get("msg") or "").strip()
            or result or "HostKey отклонил запрос")


def _active(raw: Any) -> bool:
    """`active` у HostKey — число 1/0.

    Отсутствие ключа считаем «активен»: пропавшее поле куда вероятнее смена
    схемы, чем снятая с продажи позиция, а пустой каталог выглядел бы поломкой."""
    if raw is None:
        return True
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() not in ("0", "false", "no", "off", "")


def _locations(raw: Any) -> list[str]:
    """⚠️ `locations` — строка `"NL,US,FI"`, а не список (список принимаем тоже)."""
    if isinstance(raw, list):
        values = [str(x).strip() for x in raw]
    else:
        values = str(raw or "").split(",")
    out: list[str] = []
    for value in values:
        code = value.strip().upper()
        if code and code not in out:
            out.append(code)
    return out


def _preset_price(raw: dict, locations: list[str]) -> Optional[float]:
    """Месячная цена пресета в EUR.

    `price` разложен по локациям (`{"NL": {"EUR": 3, "USD": 4}}`), и цена зависит
    от локации — в каталоге показываем минимальную, как у Hetzner; точная сумма
    известна после выбора локации. Если разбивки нет — плоский `monthly_com`."""
    prices: list[float] = []
    table = raw.get("price")
    if isinstance(table, dict):
        for code in (locations or list(table.keys())):
            row = table.get(code)
            if isinstance(row, dict):
                value = _number(row, ("EUR", "eur"))
                if value is not None:
                    prices.append(value)
    if prices:
        return min(prices)
    return _number(raw, ("monthly_com", "monthly", "price_month"))


def _preset_specs(raw: dict) -> str:
    parts = []
    for key, suffix in (("cpu", "CPU"), ("ram", "ГБ RAM"), ("hdd", "ГБ диск")):
        value = _number(raw, (key,))
        if value is not None:
            parts.append(f"{value:g} {suffix}")
    description = str(raw.get("description") or "").strip()
    if description:
        parts.append(description)
    return " · ".join(parts)


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
    CAPS = {"balance", "services", "payments", "order"}

    async def _get(self, creds: dict, path: str) -> tuple[Any, str]:
        """Читающая функция модуля: только GET и только биллинговая сводка/счета."""
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

    async def _form(self, creds: dict, path: str, params: dict) -> tuple[Any, str]:
        """Документированный транспорт invapi: POST-форма с полем `token`.

        РОВНО один запрос, без ретраев: этой же функцией уходит заказ, а он тратит
        деньги — таймаут не означает «не создано». Ошибка invapi приезжает и как
        `result != "OK"`, и как `{"code": -1, "message": …}`, поэтому её разбирает
        `_api_error`, а не только HTTP-статус."""
        token = str((creds or {}).get("token") or "").strip()
        try:
            async with self._client() as c:
                r = await c.post(f"{_BASE}{path}",
                                 data={**params, "token": token},
                                 headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            return None, f"HostKey недоступен: {redact(str(exc), token)}"

        if r.status_code >= 400:
            return None, map_http_error(r.status_code)
        try:
            data = r.json()
        except ValueError:
            return None, "HostKey вернул не-JSON ответ"
        message = _api_error(data)
        if message:
            return None, redact(message, token)
        return data, ""

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

    async def order_options(self, creds: dict) -> Optional[OrderOptions]:
        if self.check_fields(creds):
            return None
        data, err = await self._form(creds, _PRESETS_PATH, {"action": "list"})
        if err:
            return None

        plans: list[OrderPlan] = []
        by_location: dict[str, list[str]] = {}
        for raw in _rows(data, "presets"):
            if not _active(raw.get("active")):
                continue
            pid = str(raw.get("id") or "").strip()
            if not pid:
                continue
            locations = _locations(raw.get("locations"))
            plans.append(OrderPlan(
                id=pid,
                name=str(raw.get("name") or f"пресет {pid}"),
                specs=_preset_specs(raw),
                price=_preset_price(raw, locations),
                currency="EUR",
                period="month",
            ))
            for code in locations:
                by_location.setdefault(code, []).append(pid)
        if not plans:
            return None

        # Список ОС пер-пресетный, поэтому опрашиваем ограниченное число пресетов
        # и объединяем: у каждого образа остаётся, к каким пресетам он подходит.
        images: dict[str, dict] = {}
        for plan in plans[:_MAX_OS_PRESETS]:
            os_data, os_err = await self._form(
                creds, _OS_PATH, {"action": "list", "instance_id": plan.id})
            if os_err:
                # Пресет без списка ОС не должен обнулять весь каталог.
                continue
            for raw in _rows(os_data, "os_list", "os"):
                if not _active(raw.get("active")):
                    continue
                oid = str(raw.get("id") or "").strip()
                if not oid:
                    continue
                entry = images.setdefault(oid, {
                    "id": oid,
                    "name": str(raw.get("name") or raw.get("alias") or oid),
                    "allowed_presets": [],
                })
                entry["allowed_presets"].append(plan.id)

        return OrderOptions(
            plans=plans,
            regions=[{"id": code, "name": code, "presets": pids}
                     for code, pids in sorted(by_location.items())],
            images=list(images.values()),
            custom=None,  # только готовые пресеты, конструктора у HostKey нет
        )

    async def create_order(self, creds: dict, spec: dict) -> dict:
        """⚠️ Тратит деньги: заказ списывается с кредитного счёта сразу."""
        fail = {"ok": False, "id": "", "name": "", "price": None, "currency": "EUR"}
        missing = self.check_fields(creds)
        if missing:
            return {**fail, "error": missing}

        spec = spec or {}
        hostname = str(spec.get("name") or "").strip()
        preset = str(spec.get("plan_id") or "").strip()
        os_id = str(spec.get("image") or "").strip()
        # Локация обязательна: без неё вендор отвечает ошибкой совместимости ОС.
        location = str(spec.get("region") or "").strip().upper()

        empty = [label for label, value in (
            ("имя сервера", hostname), ("пресет", preset),
            ("образ", os_id), ("локация", location),
        ) if not value]
        if empty:
            return {**fail, "error": "не заполнено: " + ", ".join(empty)}

        # РОВНО один создающий запрос (`_form` не ретраит). `root_pass` не шлём —
        # см. шапку модуля.
        data, err = await self._form(creds, _ORDER_PATH, {
            "action": "order_instance",
            "preset": preset,
            "os_id": os_id,
            "location_name": location,
            "hostname": hostname,
            "deploy_period": _DEPLOY_PERIOD.get(
                str(spec.get("period") or "").strip().lower(), _DEFAULT_DEPLOY_PERIOD),
        })
        if err:
            return {**fail, "error": err}

        node = data if isinstance(data, dict) else {}
        # Форма ответа плавает: у одних сборок `id` сервера, у других только номер
        # счёта. Оба однозначно находят заказ в панели.
        order_id = str(node.get("id") or node.get("invoice") or "").strip()
        if not order_id:
            # Ответ без опознавательного знака: заказ мог пройти и уже быть
            # оплачен, поэтому молча «не получилось» сказать нельзя.
            return {**fail, "error": "HostKey принял запрос, но не вернул ни id, ни "
                                     "счёт — проверьте панель перед повторной попыткой"}
        return {
            "ok": True,
            "id": order_id,
            "name": hostname,
            # Сумму вендор в ответе на заказ не называет — она есть в каталоге.
            "price": None,
            "currency": "EUR",
            "error": "",
        }


ADAPTER = HostkeyAdapter()
