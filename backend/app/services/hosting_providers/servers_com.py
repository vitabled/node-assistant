"""Servers.com — только список хостов.

`https://api.servers.com/v1`, заголовок `Authorization: Bearer <token>` (токен
выпускается в портале: Profile → API tokens).

⚠️ **Отдельных биллинг-ручек в публичном API НЕТ.** Счета, остаток на счету и
история платежей живут в портале и наружу не отдаются, поэтому:

- `balance()` — `None` (унаследован из базового класса), `"balance"` НЕ заявлен в
  `CAPS`: инфра-биллинг покажет «баланс вручную», а не «синхронизация упала».
  Выдумывать путь вроде `/account/balance` нельзя — он молча отдал бы 404, и
  провайдер выглядел бы сломанным вместо «у вендора этого просто нет».
- `payments()` — пустой список по той же причине.

Остаётся `services()` — `GET /hosts` отдаёт выделенные серверы, SBM и
bare-metal-ноды одним списком. Цены в этом ответе нет (тариф считается по
договору), поэтому `cost=None`, а стоимость остаётся тем, что пользователь ведёт
у себя в разделе услуг.

⚠️ Ответ страничный (`page`/`per_page`): без цикла аккаунт с >100 хостов молча
обрезался бы, и это выглядело бы как «серверы пропали». Цикл ограничен, чтобы
сломанная пагинация не крутилась вечно.

Заказ (`order`) — **только облачные инстансы**:

- `GET /cloud_computing/regions` → `{id (int), name, code}`;
  `GET /cloud_computing/regions/{id}/flavors` → `{id (str), name, vcpus, ram, disk}`;
  `GET /cloud_computing/regions/{id}/images` → `{id (str), name, min_disk,
  allowed_flavors}`. Создание — `POST /cloud_computing/instances` с телом
  `{name, region_id (ЧИСЛО), flavor_id (строка), image_id (строка)}`.
- **Каталог у Servers.com пер-регионный**, а `order_options` получает только
  креды, поэтому регионы обходятся циклом: по одному запросу за flavors и images
  на регион. Списки доступного едут внутри `regions[]` (приём `ruvds.py`), чтобы
  форма не предлагала комбинацию, которую вендор отвергнет.
- **Цены у flavor'а НЕТ** (это OpenStack-каталог), поэтому `price=None` и
  `quote_order` не реализован: выдумывать сумму нельзя, а маршрут покупки умеет
  отдельно подтвердить заказ с заранее неизвестной ценой.

⚠️ **Выделенные серверы (`POST /hosts/dedicated_servers`) заказом НЕ покрыты, и
это осознанно.** Их тело требует раскладку дисков — слоты, уровень RAID и список
разделов с точками монтирования и размерами. Общий `spec` (`plan_id`, `region`,
`image`, `name`, `cpu`, `ram_gb`, `disk_gb`) этого не выражает, а собрать
разметку чужого сервера за пользователя молча — не то решение, которое можно
принять за него.
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

log = logging.getLogger("hosting.servers_com")

_BASE = "https://api.servers.com/v1"

_PER_PAGE = 100
_MAX_PAGES = 5

# Облачных регионов у вендора единицы, но каждый стоит двух запросов каталога —
# потолок держит `order_options` предсказуемым, даже если список вырастет.
_MAX_REGIONS = 8


class ServersComAdapter(ProviderAdapter):
    KIND = "servers_com"
    TITLE = "Servers.com"
    FIELDS = [CredField("token", "API-токен", "password")]
    # Без "balance" и "payments": публичный API счетов не отдаёт (см. шапку).
    CAPS = {"services", "order"}

    async def _get(self, creds: dict, path: str,
                   params: Optional[dict] = None) -> tuple[Any, str]:
        token = str((creds or {}).get("token") or "").strip()
        try:
            async with self._client() as c:
                r = await c.get(f"{_BASE}{path}", params=params, headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                })
        except httpx.HTTPError as exc:
            return None, f"Servers.com недоступен: {redact(str(exc), token)}"

        if r.status_code >= 400:
            return None, map_http_error(r.status_code)
        try:
            return r.json(), ""
        except ValueError:
            return None, "Servers.com вернул не-JSON ответ"

    async def _post(self, creds: dict, path: str, body: dict) -> tuple[Any, str]:
        """РОВНО один POST, без ретраев: создание инстанса тратит деньги, и
        таймаут не означает «не создано»."""
        token = str((creds or {}).get("token") or "").strip()
        try:
            async with self._client() as c:
                r = await c.post(f"{_BASE}{path}", json=body, headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                })
        except httpx.HTTPError as exc:
            return None, f"Servers.com недоступен: {redact(str(exc), token)}"

        if r.status_code >= 400:
            # Причина словами вендора: «нет квоты» и «образ не для этого flavor»
            # лечатся по-разному, а общий текст по коду этого не различает.
            return None, redact(_reason(r, map_http_error(r.status_code)), token)
        try:
            return r.json(), ""
        except ValueError:
            return None, "Servers.com вернул не-JSON ответ"

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        _data, err = await self._get(creds, "/hosts", {"per_page": 1, "page": 1})
        return (False, err) if err else (True, "")

    async def services(self, creds: dict) -> list[ServiceItem]:
        if self.check_fields(creds):
            return []
        out: list[ServiceItem] = []
        for page in range(1, _MAX_PAGES + 1):
            data, err = await self._get(creds, "/hosts",
                                        {"per_page": _PER_PAGE, "page": page})
            if err:
                break
            rows = data if isinstance(data, list) else None
            if rows is None and isinstance(data, dict):
                # На случай, если вендор когда-нибудь завернёт список в объект.
                candidate = data.get("hosts") or data.get("data")
                rows = candidate if isinstance(candidate, list) else None
            if rows is None:
                log.warning("servers_com: неожиданная форма /hosts")
                break
            out.extend(_host_item(raw) for raw in rows if isinstance(raw, dict))
            # Неполная страница — она же последняя; лишний запрос не делаем.
            if len(rows) < _PER_PAGE:
                break
        return out

    async def order_options(self, creds: dict) -> Optional[OrderOptions]:
        if self.check_fields(creds):
            return None
        data, err = await self._get(creds, "/cloud_computing/regions")
        if err:
            return None

        plans: dict[str, OrderPlan] = {}
        images: dict[str, dict] = {}
        regions: list[dict] = []
        for raw in _as_list(data)[:_MAX_REGIONS]:
            rid = _int(raw.get("id"))
            if rid is None:
                continue
            flavors, ferr = await self._get(
                creds, f"/cloud_computing/regions/{rid}/flavors")
            if ferr:
                # Регион без каталога предлагать нечестно: форма отдала бы
                # flavor из соседнего региона, а вендор ответил бы 4xx.
                continue
            imgs, _ierr = await self._get(
                creds, f"/cloud_computing/regions/{rid}/images")

            flavor_ids: list[str] = []
            for f in _as_list(flavors):
                fid = str(f.get("id") or "").strip()
                if not fid:
                    continue
                flavor_ids.append(fid)
                plans.setdefault(fid, OrderPlan(
                    id=fid,
                    name=str(f.get("name") or fid),
                    specs=_flavor_specs(f),
                    # Стоимости в каталоге flavor'ов нет — выдумывать нельзя.
                    price=None,
                    currency="",
                    period="month",
                ))

            image_ids: list[str] = []
            for i in _as_list(imgs):
                iid = str(i.get("id") or "").strip()
                if not iid:
                    continue
                image_ids.append(iid)
                images.setdefault(iid, {
                    "id": iid,
                    "name": str(i.get("name") or iid),
                    # Требования и совместимость едут с образом: не каждый образ
                    # ставится на любой flavor.
                    "min_disk_gb": _int(i.get("min_disk")),
                    "allowed_flavors": [str(x) for x in (i.get("allowed_flavors") or [])],
                })

            regions.append({
                "id": str(rid),
                "name": str(raw.get("name") or raw.get("code") or rid),
                "code": str(raw.get("code") or ""),
                "flavors": flavor_ids,
                "images": image_ids,
            })

        if not plans:
            return None
        return OrderOptions(plans=list(plans.values()), regions=regions,
                            images=list(images.values()), custom=None)

    async def create_order(self, creds: dict, spec: dict) -> dict:
        fail = {"ok": False, "id": "", "name": "", "price": None, "currency": ""}
        missing = self.check_fields(creds)
        if missing:
            return {**fail, "error": missing}

        spec = spec or {}
        name = str(spec.get("name") or "").strip()
        flavor = str(spec.get("plan_id") or "").strip()
        image = str(spec.get("image") or "").strip()
        # `region_id` в теле — ЧИСЛО, а из формы приезжает строка.
        region = _int(spec.get("region"))

        empty = [label for label, value in (
            ("имя сервера", name), ("тариф", flavor), ("образ", image),
        ) if not value]
        if region is None:
            empty.append("регион")
        if empty:
            return {**fail, "error": "не заполнено: " + ", ".join(empty)}

        data, err = await self._post(creds, "/cloud_computing/instances", {
            "name": name, "region_id": region,
            "flavor_id": flavor, "image_id": image,
        })
        if err:
            return {**fail, "error": err}
        inst = data if isinstance(data, dict) else {}
        iid = str(inst.get("id") or "").strip()
        if not iid:
            # Ответ без id: инстанс мог быть создан и уже оплачен, поэтому молча
            # «не получилось» сказать нельзя.
            return {**fail, "error": "Servers.com принял запрос, но не вернул инстанс "
                                     "— проверьте портал перед повторной попыткой"}
        return {
            "ok": True,
            "id": iid,
            "name": str(inst.get("name") or name),
            # Цену вендор не называет ни в каталоге, ни в ответе на создание.
            "price": None,
            "currency": "",
            "error": "",
        }


def _int(value: Any) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _as_list(data: Any) -> list[dict]:
    """Коллекция Servers.com: голый массив (обычный случай) или обёртка."""
    rows: Any = data
    if isinstance(data, dict):
        for key in ("data", "items", "regions", "flavors", "images"):
            candidate = data.get(key)
            if isinstance(candidate, list):
                rows = candidate
                break
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _flavor_specs(raw: dict) -> str:
    """⚠️ `ram` у flavor'а — МЕГАбайты (каталог OpenStack), как `memory` у DO."""
    ram = _int(raw.get("ram"))
    parts = []
    if _int(raw.get("vcpus")) is not None:
        parts.append(f"{_int(raw.get('vcpus'))} vCPU")
    if ram is not None:
        parts.append(f"{ram / 1024:g} ГБ RAM")
    if _int(raw.get("disk")) is not None:
        parts.append(f"{_int(raw.get('disk'))} ГБ")
    return " · ".join(parts)


def _reason(resp: Any, fallback: str) -> str:
    try:
        body = resp.json()
    except Exception:
        return fallback
    if not isinstance(body, dict):
        return fallback
    msg = str(body.get("message") or body.get("error") or body.get("errors") or "").strip()
    return f"{fallback}: {msg}" if msg else fallback


def _host_item(raw: dict) -> ServiceItem:
    hid = str(raw.get("id") or "")
    return ServiceItem(
        id=hid,
        name=str(raw.get("title") or raw.get("name") or "").strip() or f"хост {hid}",
        kind=str(raw.get("type") or "host"),
        # Цены в /hosts нет: тариф выделенного сервера определяется договором,
        # поэтому и валюту заявлять не о чем.
        cost=None,
        currency="",
        period="month",
        status=str(raw.get("status") or ""),
        ip=str(raw.get("public_ipv4_address") or raw.get("private_ipv4_address") or ""),
        region=str(raw.get("location_code") or raw.get("location") or ""),
        paid_till="",
    )


ADAPTER = ServersComAdapter()
