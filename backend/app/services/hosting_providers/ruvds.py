"""RuVDS adapter — balance, servers, payments (Wave-9 Plan C, Ф1).

`https://api.ruvds.com`, `Authorization: Bearer <token>` (Basic was dropped in
API 2.23). Token is issued at https://ruvds.com/my/settings/api.

Quirks taken from the v2 OpenAPI spec (`ruvds-api-v2.yaml`) — each one bites:

- **`currency` is an INTEGER enum**, not an ISO string: 1=RUB, 3=USD, 4=EUR. It
  appears that way in both `/v2/balance` and `/v2/payments`.
- **A server has no `name`.** The closest thing is the user's `user_comment`, so
  an uncommented server falls back to `VPS #<id>`.
- **`paid_till` and `network_v4` are `null` unless asked for**: they need
  `get_paid_till=true` / `get_network=true` in the query, otherwise the fields
  are present but empty and the UI would silently show no IP and no expiry.
- **`payment_period` is an integer enum** (2=1 month, 3=3 months, 4=6 months,
  5=1 year, 1=trial, 0=unset) → mapped to a period string, falling back to
  "month" (the common plan) when unset/unknown.
- **Per-server cost is a separate endpoint** (`/v2/servers/{id}/cost`), i.e. one
  request per server against a 120 req/min budget. We report `cost=None` rather
  than fan out; the local `services` table carries the user's own cost anyway.
- **Rate limit is advertised**: `ratelimit-remaining` / `ratelimit-reset`, and on
  429 a `retry-after`. On 429 we surface the wait in seconds instead of failing
  with a bare status.

Заказ (`order`) — у RuVDS это КОНСТРУКТОР, а не выбор готовой коробки:

- **`vps_tariff` — прайс-лист, а не конфигурация.** Его поля `cpu`/`ram`/`ip` —
  это ЦЕНЫ (рублей за ядро, за ГБ памяти, за адрес), поэтому у плана нет
  итоговой суммы (`price=None`), а ядра/память/диск задаются отдельно.
- **`POST /v2/servers` требует `drive_tariff_id` и `ip`**, которых нет в общем
  контракте `spec`. Тариф диска подбирается сам (самый дешёвый активный из
  доступных в выбранном ДЦ), `ip=1` — минимум, при котором сервер получает
  адрес. Оба можно передать в `spec` явно.
- **Верхних границ cpu/ram/drive API не публикует** — в `custom` они `None`, а
  нижние выведены из `os_requirements` активных ОС. Последнее слово всё равно за
  валидацией RuVDS: неподходящая конфигурация вернёт 400 и денег не спишет.
- В ответе на создание приходит `password` — наружу он НЕ отдаётся.
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

log = logging.getLogger("hosting.ruvds")

_BASE = "https://api.ruvds.com"

_CURRENCY = {1: "RUB", 3: "USD", 4: "EUR"}
_PERIOD = {1: "trial", 2: "month", 3: "quarter", 4: "half_year", 5: "year"}
# Обратное отображение для заказа. Синонимы («year» и «12month») — потому что
# период приезжает из общей формы, а не из нашего словаря.
_PERIOD_ID = {"trial": 1, "month": 2, "1month": 2, "quarter": 3, "3month": 3,
              "half_year": 4, "6month": 4, "year": 5, "12month": 5}
_DEFAULT_PERIOD = 2  # 1 месяц — самый короткий реальный период оплаты

# One page is 25 by default; ask for more and follow `pagination.next_page` so an
# account with >25 servers isn't silently truncated. Capped so a broken
# pagination cursor can't spin forever.
_PER_PAGE = 100
_MAX_PAGES = 5


def _currency(raw: Any) -> str:
    try:
        return _CURRENCY.get(int(raw), "RUB")
    except (TypeError, ValueError):
        return "RUB"


def _int(raw: Any) -> Optional[int]:
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _float(raw: Any) -> Optional[float]:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


class RuvdsAdapter(ProviderAdapter):
    KIND = "ruvds"
    TITLE = "RuVDS"
    FIELDS = [CredField("token", "API-токен", "password")]
    CAPS = {"balance", "services", "payments", "order"}

    async def _get(
        self, creds: dict, path: str, params: Optional[dict] = None
    ) -> tuple[Optional[dict], str]:
        """GET one endpoint → (json, error). `error` is "" on success; the token
        never appears in it (it travels in a header, but a proxy error string can
        still quote the request — redact defensively)."""
        token = str((creds or {}).get("token") or "").strip()
        try:
            async with self._client() as c:
                r = await c.get(
                    f"{_BASE}{path}",
                    params=params,
                    headers={"Authorization": f"Bearer {token}",
                             "Content-Type": "application/json"},
                )
        except httpx.HTTPError as exc:
            return None, f"RuVDS недоступен: {redact(str(exc), token)}"

        if r.status_code == 429:
            wait = r.headers.get("retry-after") or r.headers.get("ratelimit-reset") or ""
            suffix = f" (подождите {wait} с)" if wait.strip().isdigit() else ""
            return None, map_http_error(429) + suffix
        if r.status_code >= 400:
            return None, map_http_error(r.status_code)
        try:
            data = r.json()
        except ValueError:
            return None, "RuVDS вернул не-JSON ответ"
        return (data if isinstance(data, dict) else {}), ""

    async def _post(self, creds: dict, path: str, body: dict) -> tuple[Optional[dict], str]:
        """РОВНО один POST, без ретраев: создание сервера тратит деньги, и
        таймаут не означает «не создано»."""
        token = str((creds or {}).get("token") or "").strip()
        try:
            async with self._client() as c:
                r = await c.post(
                    f"{_BASE}{path}",
                    json=body,
                    headers={"Authorization": f"Bearer {token}",
                             "Content-Type": "application/json"},
                )
        except httpx.HTTPError as exc:
            return None, f"RuVDS недоступен: {redact(str(exc), token)}"

        if r.status_code >= 400:
            # Текст RuVDS («Missing field 'datacenter'», «not enough money»)
            # объясняет отказ точнее общей фразы по коду.
            try:
                msg = str((r.json() or {}).get("message") or "").strip()
            except ValueError:
                msg = ""
            base = map_http_error(r.status_code)
            return None, redact(f"{base}: {msg}" if msg else base, token)
        try:
            data = r.json()
        except ValueError:
            return None, "RuVDS вернул не-JSON ответ"
        return (data if isinstance(data, dict) else {}), ""

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        _data, err = await self._get(creds, "/v2/balance")
        return (False, err) if err else (True, "")

    async def balance(self, creds: dict) -> Optional[Balance]:
        if self.check_fields(creds):
            return None
        data, err = await self._get(creds, "/v2/balance")
        if err or not data:
            return None
        try:
            return Balance(float(data["amount"]), _currency(data.get("currency")))
        except (KeyError, TypeError, ValueError):
            log.warning("ruvds: unexpected /v2/balance shape")
            return None

    async def services(self, creds: dict) -> list[ServiceItem]:
        if self.check_fields(creds):
            return []
        regions = await self._datacenters(creds)
        out: list[ServiceItem] = []
        page = 1
        while page <= _MAX_PAGES:
            data, err = await self._get(creds, "/v2/servers", {
                "per_page": _PER_PAGE, "page": page,
                "get_paid_till": "true", "get_network": "true",
            })
            if err or not data:
                break
            servers = data.get("servers")
            if not isinstance(servers, list):
                break
            for raw in servers:
                if isinstance(raw, dict):
                    out.append(_server_item(raw, regions))
            nxt = (data.get("pagination") or {}).get("next_page")
            if not isinstance(nxt, int) or nxt <= page:
                break
            page = nxt
        return out

    async def payments(self, creds: dict) -> list[dict]:
        if self.check_fields(creds):
            return []
        data, err = await self._get(creds, "/v2/payments",
                                    {"per_page": _PER_PAGE, "page": 1})
        if err or not data:
            return []
        items = data.get("payments")
        if not isinstance(items, list):
            return []
        return [_payment(p) for p in items if isinstance(p, dict)]

    async def order_options(self, creds: dict) -> Optional[OrderOptions]:
        if self.check_fields(creds):
            return None
        tariffs, err = await self._get(creds, "/v2/tariffs")
        if err or not tariffs:
            return None
        dcs, _ = await self._get(creds, "/v2/datacenters")
        oses, _ = await self._get(creds, "/v2/os")

        plans: list[OrderPlan] = []
        for t in (tariffs.get("vps") or []):
            if not isinstance(t, dict) or not t.get("is_active"):
                continue
            tid = _int(t.get("id"))
            if tid is None:
                continue
            plans.append(OrderPlan(
                id=str(tid),
                name=str(t.get("name") or f"Тариф {tid}"),
                # Это ЦЕНЫ ЗА ЕДИНИЦУ, а не характеристики: сумма считается по
                # выбранным ядрам/памяти/диску.
                specs=f"CPU {_float(t.get('cpu')) or 0:g} ₽/ядро · "
                      f"RAM {_float(t.get('ram')) or 0:g} ₽/ГБ · "
                      f"IP {_float(t.get('ip')) or 0:g} ₽",
                price=None,
                currency="RUB",
                period="month",
            ))

        regions: list[dict] = []
        for dc in ((dcs or {}).get("datacenters") or []):
            if not isinstance(dc, dict) or _int(dc.get("id")) is None:
                continue
            regions.append({
                "id": str(_int(dc.get("id"))),
                "name": str(dc.get("name") or ""),
                # Не каждый тариф и не каждый тип диска есть в каждом ДЦ —
                # отдаём списки, чтобы форма не предлагала заведомый 400.
                "vps_tariffs": [str(x) for x in (dc.get("vps_tariffs") or [])],
                "drive_tariffs": [str(x) for x in (dc.get("drive_tariffs") or [])],
            })

        images: list[dict] = []
        floors: list[tuple[int, float, int]] = []
        for os_row in ((oses or {}).get("os") or []):
            if not isinstance(os_row, dict) or not os_row.get("is_active"):
                continue
            oid = _int(os_row.get("id"))
            if oid is None:
                continue
            req = os_row.get("os_requirements") or {}
            cpu = _int(req.get("cpu")) or 0
            ram = _float(req.get("ram")) or 0.0
            drive = _int(req.get("drive")) or 0
            if cpu or ram or drive:
                floors.append((cpu, ram, drive))
            images.append({
                "id": str(oid),
                "name": str(os_row.get("name") or f"OS {oid}"),
                "type": str(os_row.get("type") or ""),
                # Требования едут с образом: минимум у Windows выше, чем у Linux,
                # и общий «пол» конструктора этого не выражает.
                "min_cpu": cpu,
                "min_ram_gb": ram,
                "min_disk_gb": drive,
            })

        return OrderOptions(
            plans=plans,
            regions=regions,
            images=images,
            custom=_custom_ranges(floors),
        )

    async def _order_body(self, creds: dict, spec: dict) -> tuple[Optional[dict], str]:
        """Тело POST /v2/servers по спецификации формы: `(body, причина отказа)`.

        Общее для расчёта цены и самой покупки — иначе предварительная сумма
        считалась бы не для той конфигурации, которую потом закажут."""
        missing = self.check_fields(creds)
        if missing:
            return None, missing

        spec = spec or {}
        datacenter = _int(spec.get("region"))
        tariff_id = _int(spec.get("plan_id"))
        os_id = _int(spec.get("image"))
        cpu = _int(spec.get("cpu"))
        ram = _float(spec.get("ram_gb"))
        drive = _int(spec.get("disk_gb"))
        name = str(spec.get("name") or "").strip()

        empty = [label for label, value in (
            ("дата-центр", datacenter), ("тариф", tariff_id), ("образ ОС", os_id),
            ("CPU", cpu), ("RAM", ram), ("диск", drive),
        ) if value is None]
        if not name:
            empty.append("имя сервера")
        if empty:
            return None, "не заполнено: " + ", ".join(empty)

        drive_tariff_id = _int(spec.get("drive_tariff_id"))
        if drive_tariff_id is None:
            drive_tariff_id, why = await self._resolve_drive_tariff(creds, datacenter)
            if drive_tariff_id is None:
                return None, why

        body = {
            "datacenter": datacenter,
            "tariff_id": tariff_id,
            "os_id": os_id,
            "payment_period": _PERIOD_ID.get(
                str(spec.get("period") or "").strip().lower(), _DEFAULT_PERIOD),
            "cpu": cpu,
            "ram": ram,
            "drive": drive,
            "drive_tariff_id": drive_tariff_id,
            "ip": _int(spec.get("ip")) or 1,
            "computer_name": name,
        }
        comment = str(spec.get("comment") or "").strip()
        if comment:
            body["user_comment"] = comment
        return body, ""

    async def quote_order(self, creds: dict, spec: dict) -> Optional[dict]:
        """Стоимость конфигурации БЕЗ создания сервера.

        У RuVDS «тариф» — это прайс-лист (цена за ядро/ГБ), готовой суммы у плана
        нет. Штатный `get_price_only=true` считает её на стороне вендора и денег
        не тратит — без него маршрут покупки обязан был бы отказать."""
        body, err = await self._order_body(creds, spec)
        if err or body is None:
            return None
        data, err = await self._post(creds, "/v2/servers?get_price_only=true", body)
        if err or not isinstance(data, dict):
            return None
        price = _float(data.get("cost_rub"))
        if price is None:
            return None
        return {"price": price, "currency": "RUB"}

    async def create_order(self, creds: dict, spec: dict) -> dict:
        fail = {"ok": False, "id": "", "name": "", "price": None, "currency": "RUB"}
        body, err = await self._order_body(creds, spec)
        if err or body is None:
            return {**fail, "error": err or "не удалось собрать заказ"}
        name = str(body.get("computer_name") or "")

        data, err = await self._post(creds, "/v2/servers", body)
        if err:
            return {**fail, "error": err}
        sid = _int((data or {}).get("virtual_server_id"))
        if sid is None:
            # 200 без id: задача могла встать в очередь. Молча «не получилось»
            # сказать нельзя — сервер мог быть создан и уже оплачен.
            return {**fail, "error": "RuVDS принял запрос, но не вернул id сервера "
                                     "— проверьте ЛК перед повторной попыткой"}
        # `password` из ответа наружу не отдаём: в контракте заказа поля для
        # секрета нет, а карточка заказа персистится на клиенте.
        return {
            "ok": True,
            "id": str(sid),
            "name": name,
            "price": _float((data or {}).get("cost_rub")),
            "currency": "RUB",
            "error": "",
        }

    async def _resolve_drive_tariff(
        self, creds: dict, datacenter: Optional[int]
    ) -> tuple[Optional[int], str]:
        """Тариф диска для заказа: самый дешёвый активный, доступный в ДЦ.

        Дешёвый — потому что тип диска пользователь не выбирал, и подставлять за
        него самый дорогой значит тратить чужие деньги молча."""
        tariffs, err = await self._get(creds, "/v2/tariffs")
        if err or not tariffs:
            return None, err or "не удалось получить тарифы дисков RuVDS"
        drives = [d for d in (tariffs.get("drive") or [])
                  if isinstance(d, dict) and d.get("is_active") and _int(d.get("id")) is not None]
        if not drives:
            return None, "RuVDS не вернул активных тарифов диска — укажите drive_tariff_id"

        dcs, dc_err = await self._get(creds, "/v2/datacenters")
        if not dc_err and dcs:
            for dc in (dcs.get("datacenters") or []):
                if isinstance(dc, dict) and _int(dc.get("id")) == datacenter:
                    allowed = {_int(x) for x in (dc.get("drive_tariffs") or [])}
                    if allowed:
                        drives = [d for d in drives if _int(d.get("id")) in allowed]
                    break
        if not drives:
            return None, ("в выбранном дата-центре нет доступного тарифа диска "
                          "— укажите drive_tariff_id явно")
        best = min(drives, key=lambda d: _float(d.get("price")) or 0.0)
        return _int(best.get("id")), ""

    async def _datacenters(self, creds: dict) -> dict[int, str]:
        """id → human name, so `region` isn't a bare number in the UI. One extra
        request; a failure just leaves the numeric fallback."""
        data, err = await self._get(creds, "/v2/datacenters")
        if err or not data:
            return {}
        out: dict[int, str] = {}
        for dc in data.get("datacenters") or []:
            if isinstance(dc, dict) and isinstance(dc.get("id"), int):
                out[dc["id"]] = str(dc.get("name") or "")
        return out


def _custom_ranges(floors: list[tuple[int, float, int]]) -> dict:
    """Границы конструктора для формы заказа.

    `min` — самое мягкое требование среди активных ОС; жёстче ставить нельзя, а
    конкретная ОС всё равно несёт свой минимум в `images[].min_*`.
    `max` — None: верхних границ RuVDS в API не публикует, а выдуманный потолок
    отрезал бы реальные конфигурации. Шаг 1: `cpu` и `drive` в спеке целые, а
    сетку для `ram` вендор не объявляет."""
    cpus = [c for c, _r, _d in floors if c > 0]
    rams = [r for _c, r, _d in floors if r > 0]
    disks = [d for _c, _r, d in floors if d > 0]
    return {
        "cpu": {"min": min(cpus) if cpus else 1, "max": None, "step": 1},
        "ram_gb": {"min": min(rams) if rams else 1, "max": None, "step": 1},
        "disk_gb": {"min": min(disks) if disks else 10, "max": None, "step": 1},
    }


def _server_item(raw: dict, regions: dict[int, str]) -> ServiceItem:
    sid = raw.get("virtual_server_id")
    nets = raw.get("network_v4") or []
    ip = ""
    if isinstance(nets, list) and nets and isinstance(nets[0], dict):
        ip = str(nets[0].get("ip_address") or "")
    dc = raw.get("datacenter")
    region = regions.get(dc, "") if isinstance(dc, int) else ""
    if not region and dc is not None:
        region = str(dc)
    return ServiceItem(
        id=str(sid if sid is not None else ""),
        name=str(raw.get("user_comment") or "").strip() or f"VPS #{sid}",
        kind="vps",
        cost=None,
        currency="RUB",
        period=_PERIOD.get(raw.get("payment_period"), "month"),
        status=str(raw.get("status") or ""),
        ip=ip,
        region=region,
        paid_till=str(raw.get("paid_till") or ""),
    )


def _payment(raw: dict) -> dict:
    try:
        amount = float(raw.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0.0
    return {
        "ts": str(raw.get("dt") or ""),
        "amount": amount,
        "currency": _currency(raw.get("currency")),
        "type": "topup" if raw.get("direction") == 1 else "charge",
        "note": str(raw.get("pay_source") or ""),
    }


ADAPTER = RuvdsAdapter()
