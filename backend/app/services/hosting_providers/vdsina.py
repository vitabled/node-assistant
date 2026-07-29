"""VDSina adapter — баланс + список серверов.

`https://userapi.vdsina.com` (зеркало `userapi.vdsina.ru`).

Два места, где легко ошибиться:

- **⚠️ Токен идёт в `Authorization` БЕЗ схемы**: `Authorization: <token>`, а не
  `Bearer <token>`. С «Bearer» приходит 401, неотличимый от неверного токена.
- **Конверт двойной**: `{"status": "ok"|"error", "status_msg": "…", "data": …}`.
  HTTP при этом может быть 200, поэтому `status: "error"` обязан читаться как
  ошибка — иначе пустой `data` выглядел бы как «нет серверов».

Баланс берём из `/v1/account` (он приходит в теле аккаунта), а если там его в
узнаваемом виде нет — из `/v1/account.balance`. Поле `balance` бывает объектом
`{"real": …, "bonus": …, "partner": …}`: показываем **real** — это живые деньги,
а не бонусы, которыми нельзя оплатить всё.

Заказ (`order`) — каталог из четырёх ручек и одно создание:

- **Тарифы лежат ПОД группами.** `GET /v1/server-plan` без идентификатора не
  существует: список отдаётся только как `/v1/server-plan/{id группы}`, а сами
  группы — `GET /v1/server-group` (VDS, HighCPU, GPU…). Поэтому каталог
  собирается обходом активных групп, а `region` у плана не заполняется — у
  VDSina доступность тарифа задаётся не дата-центром, а группой.
- **`has_params` делит тарифы надвое.** У фиксированного тарифа есть `cost` за
  `period`, у настраиваемого — `params.{cpu,ram,disk,gpu}` с `{min,max,step,cost}`,
  и **готовой суммы у него нет**: `cost` там базовый, а формулу вендор не
  публикует. Поэтому у настраиваемых планов `price=None` — маршрут покупки
  требует для них отдельного подтверждения «сумма заранее неизвестна». Считать
  сумму самим значило бы обойти это подтверждение.
- ⚠️ **Единицы `params` считаем такими же, как у полей создания** (`ram` и `disk`
  — в ГБ: так они описаны у заказа). Отдельного описания единиц диапазонов
  вендор не даёт; если он всё же вернёт мегабайты, конфигурация не пройдёт
  валидацию VDSina и денег не спишет — ошибка вернётся текстом вендора.
- **Совместимость ОС едет с образом**: `GET /v1/template` отдаёт у каждого
  шаблона `server-plan` (список id тарифов) и `limits.{cpu,ram,disk}.min`.
- Создание: `POST /v1/server` — обязательны `datacenter` и `server-plan`,
  остальное опционально (`template`, `name`, `cpu`/`ram`/`disk` для
  настраиваемого тарифа). Ответ — `data.id`; ни цены, ни пароля в нём нет,
  поэтому `price` возвращаем `None`, а секретов из ответа наружу и не бывает.
- `quote_order` НЕ реализован: ручки предварительного расчёта у VDSina нет, а
  придумать сумму нельзя.
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

log = logging.getLogger("hosting.vdsina")

_BASE = "https://userapi.vdsina.com"

# Групп у VDSina единицы, но каждая — отдельный запрос: потолок держит открытие
# формы в разумном числе обращений, если вендор заведёт их десятками.
_MAX_GROUPS = 8

# «real» первым: это деньги, а не бонусы.
_AMOUNT_KEYS = ("real", "balance", "amount", "value", "total")
_CURRENCY_KEYS = ("currency", "currency_code", "curr")


def _pick_number(node: dict, keys: tuple[str, ...]) -> Optional[float]:
    for key in keys:
        if key in node:
            try:
                return float(str(node[key]).strip())
            except (TypeError, ValueError):
                continue
    return None


def _pick_str(node: dict, keys: tuple[str, ...], default: str = "") -> str:
    for key in keys:
        value = node.get(key)
        if isinstance(value, dict):
            value = value.get("name")
        text = str(value or "").strip()
        if text:
            return text
    return default


def _int(raw: Any) -> Optional[int]:
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return None


def _float(raw: Any) -> Optional[float]:
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


class VdsinaAdapter(ProviderAdapter):
    KIND = "vdsina"
    TITLE = "VDSina"
    FIELDS = [CredField("token", "API-токен", "password")]
    CAPS = {"balance", "services", "order"}

    async def _get(self, creds: dict, path: str) -> tuple[Any, str]:
        """→ (`data`, error). Снимает конверт и переводит `status: error` в текст."""
        token = str((creds or {}).get("token") or "").strip()
        try:
            async with self._client() as c:
                r = await c.get(f"{_BASE}{path}", headers={
                    # Без «Bearer» — так требует VDSina.
                    "Authorization": token,
                    "Accept": "application/json",
                })
        except httpx.HTTPError as exc:
            return None, f"VDSina недоступна: {redact(str(exc), token)}"

        if r.status_code >= 400:
            return None, map_http_error(r.status_code)
        try:
            body = r.json()
        except ValueError:
            return None, "VDSina вернула не-JSON ответ"

        if not isinstance(body, dict):
            return None, "неожиданный формат ответа VDSina"
        if str(body.get("status") or "").lower() == "error":
            text = str(body.get("status_msg") or "").strip()
            return None, redact(text, token) or "VDSina вернула ошибку без описания"
        return body.get("data"), ""

    async def _post(self, creds: dict, path: str, body: dict) -> tuple[Any, str]:
        """РОВНО один POST, без ретраев: создание сервера тратит деньги, и таймаут
        не означает «не создано». Конверт снимается так же, как в `_get`."""
        token = str((creds or {}).get("token") or "").strip()
        try:
            async with self._client() as c:
                r = await c.post(f"{_BASE}{path}", json=body, headers={
                    # Без «Bearer» — так требует VDSina.
                    "Authorization": token,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                })
        except httpx.HTTPError as exc:
            return None, f"VDSina недоступна: {redact(str(exc), token)}"

        try:
            payload = r.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict) and str(payload.get("status") or "").lower() == "error":
            # Отказ приезжает и с HTTP 200, и с 4xx — в обоих случаях слова
            # вендора («not enough money») объясняют причину точнее кода.
            text = str(payload.get("status_msg") or "").strip()
            if text:
                return None, redact(text, token)
        if r.status_code >= 400:
            return None, map_http_error(r.status_code)
        if not isinstance(payload, dict):
            return None, "VDSina вернула не-JSON ответ"
        return payload.get("data"), ""

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        _data, err = await self._get(creds, "/v1/account")
        return (False, err) if err else (True, "")

    async def balance(self, creds: dict) -> Optional[Balance]:
        if self.check_fields(creds):
            return None
        data, err = await self._get(creds, "/v1/account")
        currency = ""
        amount = None
        if not err and isinstance(data, dict):
            currency = _pick_str(data, _CURRENCY_KEYS)
            node = data.get("balance")
            if isinstance(node, dict):
                amount = _pick_number(node, _AMOUNT_KEYS)
                currency = currency or _pick_str(node, _CURRENCY_KEYS)
            else:
                amount = _pick_number(data, ("balance", "amount", "value"))
        if amount is None:
            amount, extra = await self._balance_endpoint(creds)
            currency = currency or extra
        if amount is None:
            log.warning("vdsina: no recognised balance in /v1/account")
            return None
        return Balance(amount, (currency or "RUB").upper())

    async def _balance_endpoint(self, creds: dict) -> tuple[Optional[float], str]:
        data, err = await self._get(creds, "/v1/account.balance")
        if err:
            return None, ""
        if isinstance(data, dict):
            return _pick_number(data, _AMOUNT_KEYS), _pick_str(data, _CURRENCY_KEYS)
        try:
            return float(str(data).strip()), ""
        except (TypeError, ValueError):
            return None, ""

    async def services(self, creds: dict) -> list[ServiceItem]:
        if self.check_fields(creds):
            return []
        data, err = await self._get(creds, "/v1/server")
        if err:
            return []
        if not isinstance(data, list):
            log.warning("vdsina: unexpected /v1/server shape")
            return []
        return [_server_item(raw) for raw in data if isinstance(raw, dict)]

    # ── Заказ ──────────────────────────────────────────────────
    async def _plans(self, creds: dict) -> list[tuple[str, dict]]:
        """`(имя группы, тариф)` по всем активным группам.

        Отдельной ручки «все тарифы» у VDSina нет — только `/v1/server-plan/{id
        группы}`, поэтому обход групп здесь единственный способ собрать каталог.
        Группа без тарифов не обнуляет остальные."""
        groups, err = await self._get(creds, "/v1/server-group")
        if err or not isinstance(groups, list):
            return []
        out: list[tuple[str, dict]] = []
        for group in groups[:_MAX_GROUPS]:
            if not isinstance(group, dict) or group.get("active") is False:
                continue
            gid = _int(group.get("id"))
            if gid is None:
                continue
            plans, plan_err = await self._get(creds, f"/v1/server-plan/{gid}")
            if plan_err or not isinstance(plans, list):
                continue
            name = str(group.get("name") or "").strip()
            out.extend((name, p) for p in plans if isinstance(p, dict))
        return out

    async def order_options(self, creds: dict) -> Optional[OrderOptions]:
        if self.check_fields(creds):
            return None
        rows = await self._plans(creds)
        if not rows:
            return None
        dcs, _dc_err = await self._get(creds, "/v1/datacenter")
        templates, _tpl_err = await self._get(creds, "/v1/template")

        plans: list[OrderPlan] = []
        ranges: list[dict] = []
        for group_name, raw in rows:
            pid = _int(raw.get("id"))
            # `active`/`enable` — два разных признака вендора: снятый с продажи
            # тариф гасит любой из них.
            if pid is None or raw.get("active") is False or raw.get("enable") is False:
                continue
            configurable = bool(raw.get("has_params"))
            params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
            if configurable and params:
                ranges.append(params)
            name = str(raw.get("name") or f"Тариф {pid}").strip()
            plans.append(OrderPlan(
                id=str(pid),
                name=f"{group_name} · {name}".strip(" ·") if group_name else name,
                specs=_plan_specs(raw, configurable),
                # У настраиваемого тарифа готовой суммы нет — см. докстроку модуля.
                price=None if configurable else _float(raw.get("cost")),
                currency="RUB",
                period=str(raw.get("period") or "month").strip() or "month",
            ))

        regions = [
            {"id": str(_int(dc.get("id"))),
             "name": " ".join(x for x in (str(dc.get("name") or ""),
                                          str(dc.get("country") or "")) if x).strip()
                     or str(_int(dc.get("id")))}
            for dc in (dcs if isinstance(dcs, list) else [])
            if isinstance(dc, dict) and _int(dc.get("id")) is not None
            and dc.get("active") is not False
        ]

        images: list[dict] = []
        for tpl in (templates if isinstance(templates, list) else []):
            if not isinstance(tpl, dict) or tpl.get("active") is False:
                continue
            tid = _int(tpl.get("id"))
            if tid is None:
                continue
            limits = tpl.get("limits") if isinstance(tpl.get("limits"), dict) else {}
            images.append({
                "id": str(tid),
                "name": str(tpl.get("name") or f"OS {tid}"),
                # Совместимость едет с образом: не каждая ОС ставится на каждый
                # тариф, и общий каталог этого не выражает.
                "allowed_plans": [str(x) for x in (tpl.get("server-plan") or [])
                                  if _int(x) is not None],
                "min_cpu": _int((limits.get("cpu") or {}).get("min")) or 0,
                "min_ram_gb": _float((limits.get("ram") or {}).get("min")) or 0,
                "min_disk_gb": _float((limits.get("disk") or {}).get("min")) or 0,
            })

        return OrderOptions(plans=plans, regions=regions, images=images,
                            custom=_custom_ranges(ranges))

    async def create_order(self, creds: dict, spec: dict) -> dict:
        fail = {"ok": False, "id": "", "name": "", "price": None, "currency": "RUB"}
        missing = self.check_fields(creds)
        if missing:
            return {**fail, "error": missing}

        spec = spec or {}
        datacenter = _int(spec.get("region"))
        plan_id = _int(spec.get("plan_id"))
        name = str(spec.get("name") or "").strip()
        empty = [label for label, value in (
            ("дата-центр", datacenter), ("тариф", plan_id),
        ) if value is None]
        if not name:
            empty.append("имя сервера")
        if empty:
            return {**fail, "error": "не заполнено: " + ", ".join(empty)}

        body: dict = {"datacenter": datacenter, "server-plan": plan_id, "name": name}
        template = _int(spec.get("image"))
        if template is not None:
            body["template"] = template
        # Размеры уходят только если их прислали: у фиксированного тарифа они
        # заданы самим тарифом, и лишние поля вендор считает ошибкой.
        for key, value in (("cpu", _int(spec.get("cpu"))),
                           ("ram", _int(spec.get("ram_gb"))),
                           ("disk", _int(spec.get("disk_gb")))):
            if value is not None:
                body[key] = value

        data, err = await self._post(creds, "/v1/server", body)
        if err:
            return {**fail, "error": err}
        sid = _int((data or {}).get("id")) if isinstance(data, dict) else None
        if sid is None:
            # Заказ мог пройти: молчаливое «не получилось» опаснее просьбы
            # заглянуть в панель — деньги уже могли списаться.
            return {**fail, "error": "VDSina приняла запрос, но не вернула id сервера "
                                     "— проверьте панель перед повторной попыткой"}
        return {
            "ok": True,
            "id": str(sid),
            "name": name,
            # Цены в ответе создания нет — маршрут подставит подтверждённую сумму.
            "price": None,
            "currency": "RUB",
            "error": "",
        }


def _resource(node: Any) -> str:
    """`{"value": 4, "for": "Gb"}` → «4 Gb». Единицу вендор кладёт в `for`, и она
    у разных ресурсов разная — своей не придумываем."""
    if not isinstance(node, dict):
        return ""
    value = _float(node.get("value"))
    if value is None:
        return ""
    unit = str(node.get("for") or "").strip()
    return f"{value:g} {unit}".strip()


def _plan_specs(raw: dict, configurable: bool) -> str:
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    parts = [f"{label} {text}" for label, text in (
        ("CPU", _resource(data.get("cpu"))),
        ("RAM", _resource(data.get("ram"))),
        ("диск", _resource(data.get("disk"))),
        ("трафик", _resource(data.get("traff"))),
    ) if text]
    if configurable:
        parts.append("настраиваемый")
    return " · ".join(parts)


def _custom_ranges(ranges: list[dict]) -> Optional[dict]:
    """Границы конструктора, склеенные по всем настраиваемым тарифам.

    Схема `custom` одна на провайдера, а тарифов с параметрами несколько, поэтому
    `min` берём самый мягкий, `max` — самый щедрый, `step` — самый КРУПНЫЙ
    (значение, кратное большему шагу, подойдёт и там, где шаг мельче; наоборот —
    нет). Конкретный тариф всё равно проверит VDSina при заказе."""
    axes = (("cpu", "cpu"), ("ram_gb", "ram"), ("disk_gb", "disk"))
    mins: dict[str, list[float]] = {key: [] for key, _ in axes}
    maxs: dict[str, list[float]] = {key: [] for key, _ in axes}
    steps: dict[str, list[float]] = {key: [] for key, _ in axes}

    for params in ranges:
        for key, vendor_key in axes:
            node = params.get(vendor_key)
            if not isinstance(node, dict):
                continue
            for bucket, field in ((mins, "min"), (maxs, "max"), (steps, "step")):
                value = _float(node.get(field))
                if value is not None and value > 0:
                    bucket[key].append(value)

    if not any(mins.values()):
        return None
    return {key: {
        "min": min(mins[key]) if mins[key] else None,
        # Пусто = вендор потолок не публикует (семантика base.OrderOptions),
        # а не «безлимит».
        "max": max(maxs[key]) if maxs[key] else None,
        "step": max(steps[key]) if steps[key] else 1,
    } for key, _ in axes}


def _server_ip(raw: dict) -> str:
    nets = raw.get("ip")
    if isinstance(nets, list):
        for entry in nets:
            if isinstance(entry, dict) and str(entry.get("ip") or "").strip():
                return str(entry["ip"]).strip()
            if isinstance(entry, str) and entry.strip():
                return entry.strip()
    return str(nets or "").strip() if isinstance(nets, str) else ""


def _server_item(raw: dict) -> ServiceItem:
    sid = str(raw.get("id") or "")
    plan = raw.get("server-plan") or raw.get("server_plan") or {}
    plan = plan if isinstance(plan, dict) else {}
    return ServiceItem(
        id=sid,
        name=str(raw.get("name") or "").strip() or f"сервер #{sid}",
        kind=str(plan.get("name") or "vps").strip() or "vps",
        cost=_pick_number(plan, ("cost", "price")),
        currency="RUB",
        period=str(plan.get("period") or "month").strip() or "month",
        status=str(raw.get("status") or ""),
        ip=_server_ip(raw),
        region=_pick_str(raw, ("datacenter", "location")),
        # `end` — дата окончания оплаченного периода.
        paid_till=str(raw.get("end") or "").strip(),
    )


ADAPTER = VdsinaAdapter()
