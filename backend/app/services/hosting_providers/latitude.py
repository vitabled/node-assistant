"""Latitude.sh — серверы (bare metal) + использование.

База `https://api.latitude.sh`, заголовок `Authorization: Bearer <токен>`
(личный кабинет → API Keys).

⚠️ **Ответы в формате JSON:API**, а не «плоским» объектом: строка выглядит как
`{"id": …, "type": "servers", "attributes": {…}}`, и все реальные поля лежат
именно в `attributes`. Читать `row["hostname"]` бесполезно — вернётся пусто, а не
ошибка, поэтому распаковка вынесена в `_attrs()` и применяется ко ВСЕМ строкам.

**Баланса нет и он не заявлен в CAPS.** У Latitude постоплата по использованию:
остатка средств в API нет, есть агрегат потребления — он и уходит в `payments()`.

⚠️ Форма ответа `/billing/usage` на живом аккаунте не снималась: суммы и период
читаются из списка правдоподобных имён, незнакомая форма даёт `[]` и warning в
лог. Стоимость конкретного сервера в `/servers` не приходит (цена живёт в плане,
это отдельная ручка `/plans`), поэтому `cost` у услуг честно `None`, а не ноль.

Заказ (`order`) — `POST /servers`, тоже в конверте JSON:API
(`{"data": {"type": "servers", "attributes": {…}}}`), ответ **201**:

- Обязательны ПЯТЬ атрибутов: `project`, `plan`, `site`, `operating_system`,
  `hostname`. Значения — слаги: план `c2-small-x86`, площадка `ASH`, ОС
  `ubuntu_22_04_x64_lts`.
- ⚠️ **`project` в общем контракте `spec` отсутствует** (маршрут его не шлёт),
  поэтому он резолвится сам — но только когда выбор однозначен: ровно один
  проект в аккаунте. При нескольких адаптер ОТКАЗЫВАЕТ с перечислением, потому
  что «самый первый» проект — это не безопасное умолчание, а молча выбранная
  чужая корзина. Явный `spec["project"]` всегда выигрывает.
- ⚠️ **Цена зависит от площадки**: она лежит в
  `plans[].attributes.regions[].pricing.<валюта>.<hour|month|year>`. В каталоге
  показываем МИНИМУМ по площадкам (как у Hetzner), а `quote_order` считает
  цену уже для ВЫБРАННОЙ площадки — иначе пользователь подтверждал бы самую
  дешёвую локацию, а платил за выбранную.
- `billing` — `hourly` / `monthly` / `yearly`; годовой доступен только
  зарезервированным проектам, поэтому умолчание — `monthly`.
- Bare metal: готовые планы, конструктора нет (`custom = None`).
- В ответе создания цены нет — `create_order` возвращает `price=None`, и
  маршрут подставляет уже подтверждённую сумму.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.services.hosting_providers.base import (
    CredField,
    OrderOptions,
    OrderPlan,
    ProviderAdapter,
    ServiceItem,
    map_http_error,
    redact,
)

log = logging.getLogger("hosting.latitude")

_BASE = "https://api.latitude.sh"

# Период оплаты: наш `period` → атрибут `billing` вендора → ключ в `pricing`.
_BILLING = {"hour": "hourly", "hourly": "hourly",
            "month": "monthly", "monthly": "monthly", "1month": "monthly",
            "year": "yearly", "yearly": "yearly", "12month": "yearly"}
_DEFAULT_BILLING = "monthly"
_PRICE_KEY = {"hourly": "hour", "monthly": "month", "yearly": "year"}
# В `pricing` вендор отдаёт несколько валют (USD, BRL). Читаем только USD: если
# её в ответе нет, цена остаётся неизвестной — сумму в одной валюте нельзя
# выдавать за сумму в другой.
_CURRENCY = "USD"

_NAME_KEYS = ("hostname", "label", "name")
_STATUS_KEYS = ("status", "state")
_IP_KEYS = ("primary_ipv4", "ipv4", "ip_address", "ip")
_TS_KEYS = ("period_start", "start_date", "created_at", "date", "billing_date",
            "period")
_AMOUNT_KEYS = ("amount", "total", "price", "cost", "amount_due", "total_price")
_CURRENCY_KEYS = ("currency", "currency_code")
_NOTE_KEYS = ("description", "name", "product", "type", "status")


def _num(value: Any) -> Optional[float]:
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def _pick_number(node: dict, keys: tuple[str, ...]) -> Optional[float]:
    for key in keys:
        if key in node:
            found = _num(node[key])
            if found is not None:
                return found
    return None


def _pick_str(node: dict, keys: tuple[str, ...], default: str = "") -> str:
    for key in keys:
        value = node.get(key)
        if isinstance(value, (dict, list)):
            continue
        text = str(value or "").strip()
        if text:
            return text
    return default


def _rows(payload: Any) -> list[dict]:
    """Строки JSON:API-коллекции (`{"data": [...]}`) либо голого списка."""
    raw = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [row for row in raw if isinstance(row, dict)]


def _attrs(row: dict) -> dict:
    """Поля записи: JSON:API прячет их в `attributes` (см. докстроку)."""
    attrs = row.get("attributes")
    return {**row, **attrs} if isinstance(attrs, dict) else row


def _error_text(payload: Any) -> str:
    """Причина отказа словами вендора: JSON:API кладёт её в `errors[].detail`."""
    if not isinstance(payload, dict):
        return ""
    errors = payload.get("errors")
    if isinstance(errors, list):
        parts = [_pick_str(e, ("detail", "title", "message"))
                 for e in errors if isinstance(e, dict)]
        return "; ".join(p for p in parts if p)
    return _pick_str(payload, ("message", "error", "detail"))


def _billing(period: Any) -> str:
    return _BILLING.get(str(period or "").strip().lower(), _DEFAULT_BILLING)


def _sites_of(region: dict) -> list[str]:
    """Площадки, на которых план доступен в этой регион-записи."""
    out: list[str] = []
    locations = region.get("locations")
    if isinstance(locations, dict):
        for key in ("in_stock", "available"):
            values = locations.get(key)
            if isinstance(values, list):
                out += [str(v).strip() for v in values if str(v or "").strip()]
    named = _pick_str(region, ("slug", "name"))
    if named:
        out.append(named)
    return out


def _region_price(region: dict, price_key: str) -> Optional[float]:
    pricing = region.get("pricing")
    if not isinstance(pricing, dict):
        return None
    node = pricing.get(_CURRENCY) or pricing.get(_CURRENCY.lower())
    if not isinstance(node, dict):
        return None
    return _num(node.get(price_key))


def _plan_price(attrs: dict, site: str, price_key: str) -> Optional[float]:
    """Цена плана: для конкретной площадки, а при пустой — минимум по всем.

    Минимум в каталоге — сознательно (как у Hetzner): точная сумма известна
    только после выбора площадки, и завышать её в списке незачем."""
    regions = attrs.get("regions")
    rows = [r for r in regions if isinstance(r, dict)] if isinstance(regions, list) else []
    found: list[float] = []
    for region in rows:
        if site and not any(s.upper() == site.upper() for s in _sites_of(region)):
            continue
        price = _region_price(region, price_key)
        if price is not None:
            found.append(price)
    return min(found) if found else None


def _plan_specs(attrs: dict) -> str:
    """Характеристики плана строкой. Значения показываем как отдал вендор —
    пересчитывать неизвестные единицы значило бы соврать."""
    specs = attrs.get("specs")
    if not isinstance(specs, dict):
        return ""
    parts: list[str] = []
    cpu = specs.get("cpu")
    if isinstance(cpu, dict):
        cores = _pick_str(cpu, ("cores",))
        parts.append(" ".join(x for x in (
            _pick_str(cpu, ("type",)), f"{cores} ядер" if cores else "",
            _pick_str(cpu, ("clock",)),
        ) if x))
    memory = specs.get("memory")
    if isinstance(memory, dict):
        total = _pick_str(memory, ("total",))
        if total:
            parts.append(f"{total} ГБ RAM" if _num(total) is not None else total)
    drives = specs.get("drives")
    if isinstance(drives, list):
        for drive in drives:
            if not isinstance(drive, dict):
                continue
            parts.append(" ".join(x for x in (
                f"{_pick_str(drive, ('count',))} ×" if drive.get("count") else "",
                _pick_str(drive, ("size",)), _pick_str(drive, ("type",)),
            ) if x))
    return " · ".join(p for p in parts if p)


class LatitudeAdapter(ProviderAdapter):
    KIND = "latitude"
    TITLE = "Latitude.sh"
    FIELDS = [CredField("token", "API-токен", "password")]
    # Без "balance": у Latitude постоплата, остатка средств в API нет.
    CAPS = {"services", "payments", "order"}

    async def _get(self, creds: dict, path: str) -> tuple[Any, str]:
        token = str((creds or {}).get("token") or "").strip()
        try:
            async with self._client() as c:
                r = await c.get(f"{_BASE}{path}", headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                })
        except httpx.HTTPError as exc:
            return None, "Latitude.sh недоступен: " + redact(str(exc), token)

        if r.status_code >= 400:
            return None, map_http_error(r.status_code)
        try:
            return r.json(), ""
        except ValueError:
            return None, "Latitude.sh вернул не-JSON ответ"

    async def _post(self, creds: dict, path: str, body: dict) -> tuple[Any, str]:
        """РОВНО один POST, без ретраев: развёртывание тратит деньги, и таймаут
        не означает «не создано»."""
        token = str((creds or {}).get("token") or "").strip()
        try:
            async with self._client() as c:
                r = await c.post(f"{_BASE}{path}", json=body, headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                })
        except httpx.HTTPError as exc:
            return None, "Latitude.sh недоступен: " + redact(str(exc), token)

        if r.status_code >= 400:
            try:
                text = _error_text(r.json())
            except ValueError:
                text = ""
            base = map_http_error(r.status_code)
            if r.status_code in (401, 403) or not text:
                return None, base
            return None, redact(f"{base}: {text}", token)
        try:
            return r.json(), ""
        except ValueError:
            return None, "Latitude.sh вернул не-JSON ответ"

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        _data, err = await self._get(creds, "/servers")
        return (False, err) if err else (True, "")

    async def services(self, creds: dict) -> list[ServiceItem]:
        if self.check_fields(creds):
            return []
        data, err = await self._get(creds, "/servers")
        if err:
            return []
        out: list[ServiceItem] = []
        for row in _rows(data):
            attrs = _attrs(row)
            sid = str(row.get("id") or attrs.get("id") or "")
            plan = attrs.get("plan")
            plan_name = ""
            if isinstance(plan, dict):
                plan_name = _pick_str(plan, ("name", "slug"))
            region = attrs.get("region")
            region_name = ""
            if isinstance(region, dict):
                site = region.get("site")
                if isinstance(site, dict):
                    region_name = _pick_str(site, ("slug", "name", "facility"))
                region_name = region_name or _pick_str(region, ("city", "country"))
            out.append(ServiceItem(
                id=sid,
                name=_pick_str(attrs, _NAME_KEYS) or f"server {sid}",
                kind=plan_name or "bare-metal",
                # Цена лежит в плане, а не в сервере — см. докстроку.
                cost=None,
                currency="USD",
                period="month",
                status=_pick_str(attrs, _STATUS_KEYS),
                ip=_pick_str(attrs, _IP_KEYS),
                region=region_name,
            ))
        return out

    async def payments(self, creds: dict) -> list[dict]:
        if self.check_fields(creds):
            return []
        data, err = await self._get(creds, "/billing/usage")
        if err:
            return []
        rows = _rows(data)
        if not rows:
            if data:
                log.warning("latitude: незнакомая форма ответа /billing/usage")
            return []
        out: list[dict] = []
        for row in rows:
            attrs = _attrs(row)
            out.append({
                "ts": _pick_str(attrs, _TS_KEYS),
                "amount": _pick_number(attrs, _AMOUNT_KEYS),
                "currency": _pick_str(attrs, _CURRENCY_KEYS, "USD").upper(),
                # Использование — это начисление, а не платёж.
                "type": "charge",
                "note": _pick_str(attrs, _NOTE_KEYS),
            })
        return out

    # ── Заказ ──────────────────────────────────────────────────
    async def order_options(self, creds: dict) -> Optional[OrderOptions]:
        if self.check_fields(creds):
            return None
        plans_raw, err = await self._get(creds, "/plans")
        if err:
            return None
        regions_raw, _region_err = await self._get(creds, "/regions")
        images_raw, _image_err = await self._get(creds, "/plans/operating_systems")

        plans: list[OrderPlan] = []
        for row in _rows(plans_raw):
            attrs = _attrs(row)
            # Разворачивать план надо по СЛАГУ: атрибут `plan` при заказе — слаг,
            # а не opaque-идентификатор JSON:API.
            slug = _pick_str(attrs, ("slug",))
            if not slug:
                continue
            plans.append(OrderPlan(
                id=slug,
                name=_pick_str(attrs, ("name", "slug")),
                specs=_plan_specs(attrs),
                price=_plan_price(attrs, "", _PRICE_KEY[_DEFAULT_BILLING]),
                currency=_CURRENCY,
                period="month",
            ))

        regions: list[dict] = []
        for row in _rows(regions_raw):
            attrs = _attrs(row)
            slug = _pick_str(attrs, ("slug",))
            if not slug:
                continue
            country = attrs.get("country")
            country_name = (_pick_str(country, ("name", "slug"))
                            if isinstance(country, dict) else "")
            regions.append({
                "id": slug,
                "name": " ".join(x for x in (_pick_str(attrs, ("name", "facility")),
                                             f"({country_name})" if country_name else "") if x),
            })

        images: list[dict] = []
        for row in _rows(images_raw):
            attrs = _attrs(row)
            slug = _pick_str(attrs, ("slug",))
            if not slug:
                continue
            images.append({
                "id": slug,
                "name": _pick_str(attrs, ("name", "distro", "slug")),
            })

        return OrderOptions(
            plans=plans, regions=regions, images=images,
            custom=None,  # bare metal: только готовые планы
        )

    async def _resolve_project(self, creds: dict, spec: dict) -> tuple[str, str]:
        """Проект для заказа: явный из спеки, иначе единственный в аккаунте.

        При нескольких — отказ, а не «первый попавшийся»: сервер ушёл бы в чужой
        проект, и заметили бы это уже после оплаты."""
        explicit = str((spec or {}).get("project") or "").strip()
        if explicit:
            return explicit, ""
        data, err = await self._get(creds, "/projects")
        if err:
            return "", err
        rows = _rows(data)
        if not rows:
            return "", "в аккаунте Latitude.sh нет проекта — создайте его в панели"
        if len(rows) > 1:
            names = ", ".join(
                _pick_str(_attrs(r), ("slug", "name")) or str(r.get("id") or "")
                for r in rows[:5])
            return "", ("в аккаунте несколько проектов, а заказ обязан указывать "
                        f"один ({names}) — передайте project явно")
        row = rows[0]
        project = str(row.get("id") or "") or _pick_str(_attrs(row), ("slug",))
        if not project:
            return "", "Latitude.sh не вернул идентификатор проекта"
        return project, ""

    async def _order_attrs(self, creds: dict, spec: dict) -> tuple[Optional[dict], str]:
        """Атрибуты `POST /servers` по спецификации формы."""
        missing = self.check_fields(creds)
        if missing:
            return None, missing

        spec = spec or {}
        plan = str(spec.get("plan_id") or "").strip()
        site = str(spec.get("region") or "").strip()
        image = str(spec.get("image") or "").strip()
        hostname = str(spec.get("name") or "").strip()
        empty = [label for label, value in (
            ("тариф", plan), ("площадка", site), ("образ ОС", image),
            ("имя сервера", hostname),
        ) if not value]
        if empty:
            return None, "не заполнено: " + ", ".join(empty)

        project, why = await self._resolve_project(creds, spec)
        if not project:
            return None, why
        return {
            "project": project,
            "plan": plan,
            "site": site,
            "operating_system": image,
            "hostname": hostname,
            "billing": _billing(spec.get("period")),
        }, ""

    async def quote_order(self, creds: dict, spec: dict) -> Optional[dict]:
        """Цена плана НА ВЫБРАННОЙ площадке — в каталоге лежит минимум по всем."""
        spec = spec or {}
        plan_id = str(spec.get("plan_id") or "").strip()
        site = str(spec.get("region") or "").strip()
        if not plan_id or not site or self.check_fields(creds):
            return None
        data, err = await self._get(creds, "/plans")
        if err:
            return None
        price_key = _PRICE_KEY[_billing(spec.get("period"))]
        for row in _rows(data):
            attrs = _attrs(row)
            if _pick_str(attrs, ("slug",)) != plan_id:
                continue
            price = _plan_price(attrs, site, price_key)
            return {"price": price, "currency": _CURRENCY} if price is not None else None
        return None

    async def create_order(self, creds: dict, spec: dict) -> dict:
        fail = {"ok": False, "id": "", "name": "", "price": None, "currency": _CURRENCY}
        attrs, err = await self._order_attrs(creds, spec)
        if err or attrs is None:
            return {**fail, "error": err or "не удалось собрать заказ"}

        data, err = await self._post(creds, "/servers", {
            "data": {"type": "servers", "attributes": attrs},
        })
        if err:
            return {**fail, "error": err}
        row = (data or {}).get("data") if isinstance(data, dict) else None
        if not isinstance(row, dict) or not row.get("id"):
            # 2xx без сервера: заказ мог пройти. Молчать нельзя — деньги могли
            # быть списаны.
            return {**fail, "error": "Latitude.sh принял запрос, но не вернул сервер "
                                     "— проверьте панель перед повторной попыткой"}
        return {
            "ok": True,
            "id": str(row.get("id")),
            "name": _pick_str(_attrs(row), _NAME_KEYS) or attrs["hostname"],
            # Цены в ответе развёртывания нет — см. докстроку модуля.
            "price": None,
            "currency": _CURRENCY,
            "error": "",
        }


ADAPTER = LatitudeAdapter()
