"""Timeweb Cloud adapter — баланс + список серверов.

`https://api.timeweb.cloud`, `Authorization: Bearer <токен>` (токен выпускается в
панели: Доступ и настройки → API-ключи).

Что стоит знать про этот API:

- **Баланс** — `GET /api/v1/account/finances`, поле `finances.balance`. Там же
  `monthly_cost` и `hours_left`; в контракт адаптера они не помещаются, поэтому не
  используются.
- **Стоимость услуг** — `GET /api/v1/account/services/cost` — это АГРЕГАТ по
  аккаунту, а не прайс по каждому серверу. Мы читаем его и раскладываем по услугам
  ТОЛЬКО если ответ реально пришёл списком с идентификаторами; агрегатное число
  раскидать по серверам нечем, и в этом случае `cost` у каждой услуги остаётся
  `None` (локальная таблица услуг всё равно ведёт свою стоимость).
- **Список серверов** — `GET /api/v1/servers` с `limit`/`offset` и `meta.total`;
  без пагинации аккаунт больше страницы молча обрезался бы.
- **Ошибка приходит с `message` СПИСКОМ** (`{"message": ["…"]}`), а не строкой —
  наивный `str()` показал бы пользователю `['Unauthorized']` вместе со скобками.
- IP лежит не в корне сервера, а в `networks[].ips[]`; предпочитаем публичный
  ipv4 с `is_main`, иначе первый публичный, иначе любой.

Заказ (`order`) — у Timeweb Cloud есть ОБА пути, и они взаимоисключающие:

- **Готовый тариф** (`GET /api/v1/presets/servers`) → в теле `preset_id`.
- **Конструктор** (`GET /api/v1/configurator/servers`) → в теле объект
  `configuration {configurator_id, cpu, ram, disk}`. Вендор запрещает слать
  `preset_id` и `configuration` вместе, поэтому правило одно и явное:
  **непустой `plan_id` выигрывает**, конструктор используется только когда
  `plan_id` пуст (форма для заказа по конструктору тариф не выбирает).
- ⚠️ **`ram` и `disk` у Timeweb в МЕГАБАЙТАХ** — и в тарифах, и в границах
  конструктора, и в теле создания. Наш контракт (`ram_gb`/`disk_gb`) в
  гигабайтах, поэтому весь обмен идёт через `_mb_to_gb`/`_gb_to_mb`. Ошибиться
  здесь — значит заказать сервер в 1024 раза не того размера.
- ⚠️ **`os_id` и `image_id` вместе слать нельзя.** Числовой `image` из формы
  трактуем как `os_id` (образ вендора), нечисловой — как `image_id`
  (пользовательский образ, у него строковый идентификатор).
- **Цена есть только у тарифа.** Формулу конструктора Timeweb Cloud не
  публикует и ручки предварительного расчёта у него нет, поэтому
  `quote_order` отвечает суммой только для тарифа, а для конструктора честно
  возвращает `None` — маршрут покупки в этом случае требует отдельного
  подтверждения «без известной суммы». Выдумывать цену нельзя.
- **Ответ создания (`{"server": …}`) цены не содержит** — `create_order`
  возвращает `price=None`, и маршрут подставляет сумму, которую уже подтвердил
  пользователь.
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

log = logging.getLogger("hosting.timeweb")

_BASE = "https://api.timeweb.cloud"

_PER_PAGE = 100
_MAX_PAGES = 5

_PRESETS_PATH = "/api/v1/presets/servers"
_CONFIGURATORS_PATH = "/api/v1/configurator/servers"
_OS_PATH = "/api/v1/os/servers"
_SERVERS_PATH = "/api/v1/servers"


def _num(raw: Any) -> Optional[float]:
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _int(raw: Any) -> Optional[int]:
    value = _num(raw)
    return None if value is None else int(value)


def _mb_to_gb(raw: Any) -> Optional[float]:
    """МБ вендора → ГБ контракта. Округление до 3 знаков убирает хвосты вида
    0.9999999 у нецелых шагов, не пряча реальную дробность."""
    value = _num(raw)
    return None if value is None else round(value / 1024.0, 3)


def _gb_to_mb(raw: Any) -> Optional[int]:
    """ГБ контракта → МБ вендора. Целое: `disk`/`ram` у Timeweb — целые МБ."""
    value = _num(raw)
    return None if value is None else int(round(value * 1024.0))


def _rows(payload: Any, key: str) -> list[dict]:
    """Строки коллекции из `{"<key>": [...]}`.

    Контракт адаптеров запрещает бросать, а `payload` — это чужой JSON: голый
    список или `null` вместо объекта не должны превращаться в `AttributeError`
    на ровном месте."""
    node = payload.get(key) if isinstance(payload, dict) else payload
    return [row for row in node if isinstance(row, dict)] if isinstance(node, list) else []


def _error_text(data: Any) -> str:
    """Текст ошибки Timeweb: `message` бывает и строкой, и списком строк."""
    if not isinstance(data, dict):
        return ""
    message = data.get("message")
    if isinstance(message, list):
        parts = [str(m).strip() for m in message if str(m or "").strip()]
        return "; ".join(parts)
    text = str(message or "").strip()
    if text:
        return text
    return str(data.get("error_code") or "").strip()


def _currency(raw: Any) -> str:
    text = str(raw or "").strip().upper()
    # Встречается и «RUB», и внутренний код вроде «RU» — на трёхбуквенный ISO
    # полагаемся, всё остальное считаем неизвестным и показываем рубли.
    return text if len(text) == 3 and text.isalpha() else "RUB"


class TimewebAdapter(ProviderAdapter):
    KIND = "timeweb"
    TITLE = "Timeweb Cloud"
    FIELDS = [CredField("token", "API-токен", "password")]
    CAPS = {"balance", "services", "order"}

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
            return None, f"Timeweb Cloud недоступен: {redact(str(exc), token)}"

        if r.status_code >= 400:
            try:
                text = _error_text(r.json())
            except ValueError:
                text = ""
            # Для 401/403 показываем нашу формулировку: вендорское «Unauthorized»
            # пользователю ничего не объясняет.
            if r.status_code in (401, 403) or not text:
                return None, map_http_error(r.status_code)
            return None, redact(text, token)
        try:
            return r.json(), ""
        except ValueError:
            return None, "Timeweb Cloud вернул не-JSON ответ"

    async def _post(self, creds: dict, path: str, body: dict) -> tuple[Any, str]:
        """РОВНО один POST, без ретраев: создание сервера тратит деньги, и
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
            return None, f"Timeweb Cloud недоступен: {redact(str(exc), token)}"

        if r.status_code >= 400:
            # Текст вендора («not enough money», «preset is not available»)
            # объясняет отказ точнее общей фразы по коду.
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
            return None, "Timeweb Cloud вернул не-JSON ответ"

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        _data, err = await self._get(creds, "/api/v1/account/finances")
        return (False, err) if err else (True, "")

    async def balance(self, creds: dict) -> Optional[Balance]:
        if self.check_fields(creds):
            return None
        data, err = await self._get(creds, "/api/v1/account/finances")
        if err or not isinstance(data, dict):
            return None
        finances = data.get("finances")
        if not isinstance(finances, dict):
            log.warning("timeweb: unexpected /account/finances shape")
            return None
        try:
            amount = float(str(finances["balance"]).strip())
        except (KeyError, TypeError, ValueError):
            log.warning("timeweb: no numeric balance in /account/finances")
            return None
        return Balance(amount, _currency(finances.get("currency")))

    async def services(self, creds: dict) -> list[ServiceItem]:
        if self.check_fields(creds):
            return []
        costs = await self._cost_map(creds)
        out: list[ServiceItem] = []
        offset = 0
        for _page in range(_MAX_PAGES):
            data, err = await self._get(creds, "/api/v1/servers",
                                        {"limit": _PER_PAGE, "offset": offset})
            if err or not isinstance(data, dict):
                break
            servers = data.get("servers")
            if not isinstance(servers, list):
                log.warning("timeweb: unexpected /servers shape")
                break
            for raw in servers:
                if isinstance(raw, dict):
                    out.append(_server_item(raw, costs))
            offset += len(servers)
            total = (data.get("meta") or {}).get("total")
            if not servers or not isinstance(total, int) or offset >= total:
                break
        return out

    async def _cost_map(self, creds: dict) -> dict[str, float]:
        """id услуги → стоимость, если ручка отдала разбивку. Агрегатное число
        разложить не по чем — тогда пусто и `cost` останется `None`."""
        data, err = await self._get(creds, "/api/v1/account/services/cost")
        if err or data is None:
            return {}
        rows = data
        if isinstance(data, dict):
            for key in ("services_cost", "services", "items", "data"):
                if isinstance(data.get(key), list):
                    rows = data[key]
                    break
        if not isinstance(rows, list):
            return {}
        out: dict[str, float] = {}
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            sid = raw.get("id") or raw.get("service_id") or raw.get("resource_id")
            if sid is None:
                continue
            for key in ("cost", "price", "monthly_cost", "value"):
                if key in raw:
                    try:
                        out[str(sid)] = float(str(raw[key]).strip())
                    except (TypeError, ValueError):
                        pass
                    break
        return out

    # ── Заказ ──────────────────────────────────────────────────
    async def order_options(self, creds: dict) -> Optional[OrderOptions]:
        if self.check_fields(creds):
            return None
        presets, preset_err = await self._get(creds, _PRESETS_PATH)
        confs, conf_err = await self._get(creds, _CONFIGURATORS_PATH)
        # Хватает любого из двух: тарифы и конструктор — независимые пути заказа,
        # и падение одного не должно прятать другой.
        if preset_err and conf_err:
            return None
        oses, _os_err = await self._get(creds, _OS_PATH)

        preset_rows = [p for p in _rows(presets, "server_presets")
                       if _int(p.get("id")) is not None]
        conf_rows = [c for c in _rows(confs, "server_configurators")
                     if _int(c.get("id")) is not None]

        plans = [_preset_plan(p) for p in preset_rows]

        # Локация — общая ось: у тарифа она своя, у конструктора своя. Форме
        # нужен один список, поэтому склеиваем и помечаем, что где доступно.
        regions: dict[str, dict] = {}

        def _region(location: str) -> dict:
            return regions.setdefault(location, {
                "id": location, "name": location,
                "configurator_id": "", "preset_ids": [],
            })

        for c in conf_rows:
            location = str(c.get("location") or "")
            if not location:
                continue
            entry = _region(location)
            if not entry["configurator_id"]:
                entry["configurator_id"] = str(_int(c.get("id")))
                entry["disk_type"] = str(c.get("disk_type") or "")
        for p in preset_rows:
            location = str(p.get("location") or "")
            if location:
                _region(location)["preset_ids"].append(str(_int(p.get("id"))))

        images = []
        for o in _rows(oses, "servers_os"):
            if _int(o.get("id")) is None:
                continue
            req = o.get("requirements") if isinstance(o.get("requirements"), dict) else {}
            images.append({
                "id": str(_int(o.get("id"))),
                "name": " ".join(x for x in (str(o.get("name") or ""),
                                             str(o.get("version") or "")) if x).strip()
                        or str(o.get("description") or f"OS {_int(o.get('id'))}"),
                "family": str(o.get("family") or ""),
                # Требования едут с образом: у Windows порог выше, чем у Linux,
                # и общий «пол» конструктора этого не выражает.
                "min_cpu": _int(req.get("cpu_min")) or 0,
                "min_ram_gb": _mb_to_gb(req.get("ram_min")) or 0,
                "min_disk_gb": _mb_to_gb(req.get("disk_min")) or 0,
            })

        return OrderOptions(
            plans=plans,
            regions=list(regions.values()),
            images=images,
            custom=_custom_ranges(conf_rows),
        )

    async def _resolve_configurator(
        self, creds: dict, location: str
    ) -> tuple[Optional[int], Optional[int], str]:
        """`(configurator_id, минимальная полоса, причина отказа)` для локации.

        Локация обязательна: конструктор у Timeweb Cloud задаётся отдельным
        конфигуратором на каждую локацию, и выбрать её за пользователя нельзя —
        от неё зависит и цена, и физическое расположение сервера."""
        if not location:
            return None, None, "не заполнено: локация"
        confs, err = await self._get(creds, _CONFIGURATORS_PATH)
        if err:
            return None, None, err
        rows = [c for c in _rows(confs, "server_configurators")
                if _int(c.get("id")) is not None]
        if not rows:
            return None, None, "Timeweb Cloud не вернул конфигураторов"
        matched = [c for c in rows if str(c.get("location") or "") == location]
        if not matched:
            return None, None, (f"в локации {location} нет конструктора "
                                "— выберите другую локацию или готовый тариф")
        chosen = matched[0]
        req = chosen.get("requirements") if isinstance(chosen.get("requirements"), dict) else {}
        return _int(chosen.get("id")), _int(req.get("network_bandwidth_min")), ""

    async def _order_body(self, creds: dict, spec: dict) -> tuple[Optional[dict], str]:
        """Тело `POST /api/v1/servers` по спецификации формы."""
        missing = self.check_fields(creds)
        if missing:
            return None, missing

        spec = spec or {}
        name = str(spec.get("name") or "").strip()
        image = str(spec.get("image") or "").strip()
        if not name or not image:
            empty = ([] if name else ["имя сервера"]) + ([] if image else ["образ ОС"])
            return None, "не заполнено: " + ", ".join(empty)

        body: dict = {"name": name}
        os_id = _int(image)
        # `os_id` и `image_id` вместе слать нельзя — см. докстроку модуля.
        if os_id is not None:
            body["os_id"] = os_id
        else:
            body["image_id"] = image

        plan_id = _int(spec.get("plan_id"))
        if plan_id is not None:
            # Готовый тариф: локация и полоса заданы самим тарифом.
            body["preset_id"] = plan_id
            return body, ""

        cpu = _int(spec.get("cpu"))
        ram = _gb_to_mb(spec.get("ram_gb"))
        disk = _gb_to_mb(spec.get("disk_gb"))
        empty = [label for label, value in (
            ("CPU", cpu), ("RAM", ram), ("диск", disk),
        ) if value is None]
        if empty:
            return None, "не заполнено: " + ", ".join(empty)

        conf_id, bandwidth_min, why = await self._resolve_configurator(
            creds, str(spec.get("region") or ""))
        if conf_id is None:
            return None, why
        body["configuration"] = {
            "configurator_id": conf_id, "cpu": cpu, "ram": ram, "disk": disk,
        }
        # Полосу пользователь не выбирал, поэтому берём минимальную из требований
        # конфигуратора: подставить за него более широкую значит молча потратить
        # чужие деньги. Явное значение в спеке выигрывает.
        bandwidth = _int(spec.get("bandwidth")) or bandwidth_min
        if bandwidth:
            body["bandwidth"] = bandwidth
        return body, ""

    async def quote_order(self, creds: dict, spec: dict) -> Optional[dict]:
        """Стоимость ТАРИФА, перечитанная у вендора прямо сейчас.

        Для конструктора — `None`: формулу Timeweb Cloud не публикует, а ручки
        предварительного расчёта у него нет. Придумать сумму нельзя."""
        plan_id = _int((spec or {}).get("plan_id"))
        if plan_id is None or self.check_fields(creds):
            return None
        data, err = await self._get(creds, _PRESETS_PATH)
        if err:
            return None
        for raw in _rows(data, "server_presets"):
            if _int(raw.get("id")) != plan_id:
                continue
            price = _num(raw.get("price"))
            return {"price": price, "currency": "RUB"} if price is not None else None
        return None

    async def create_order(self, creds: dict, spec: dict) -> dict:
        fail = {"ok": False, "id": "", "name": "", "price": None, "currency": "RUB"}
        body, err = await self._order_body(creds, spec)
        if err or body is None:
            return {**fail, "error": err or "не удалось собрать заказ"}

        data, err = await self._post(creds, _SERVERS_PATH, body)
        if err:
            return {**fail, "error": err}
        server = (data or {}).get("server") if isinstance(data, dict) else None
        if not isinstance(server, dict) or _int(server.get("id")) is None:
            # 2xx без сервера: заказ мог пройти. Молча сказать «не получилось»
            # нельзя — деньги могли быть списаны.
            return {**fail, "error": "Timeweb Cloud принял запрос, но не вернул сервер "
                                     "— проверьте панель перед повторной попыткой"}
        return {
            "ok": True,
            # `id` приезжает числом (в схеме — float): без `_int` строка стала бы «123.0».
            "id": str(_int(server.get("id"))),
            "name": str(server.get("name") or body["name"]),
            # Цены в ответе создания у Timeweb Cloud нет — см. докстроку модуля.
            "price": None,
            "currency": "RUB",
            "error": "",
        }


def _preset_plan(raw: dict) -> OrderPlan:
    """Готовый тариф → позиция каталога. Размеры вендор отдаёт в МБ."""
    pid = _int(raw.get("id"))
    ram_gb = _mb_to_gb(raw.get("ram")) or 0
    disk_gb = _mb_to_gb(raw.get("disk")) or 0
    specs = (f"{_int(raw.get('cpu')) or 0} vCPU · {ram_gb:g} ГБ RAM · "
             f"{disk_gb:g} ГБ {raw.get('disk_type') or ''}").strip()
    bandwidth = _int(raw.get("bandwidth"))
    if bandwidth:
        specs += f" · {bandwidth} Мбит/с"
    return OrderPlan(
        id=str(pid),
        name=str(raw.get("description_short") or raw.get("description")
                 or f"Тариф {pid}"),
        specs=specs,
        price=_num(raw.get("price")),
        currency="RUB",
        period="month",
        # У тарифа локация своя — форме не нужно спрашивать её отдельно.
        region=str(raw.get("location") or ""),
    )


def _custom_ranges(conf_rows: list[dict]) -> Optional[dict]:
    """Границы конструктора, склеенные по всем локациям (МБ → ГБ).

    Конфигураторов несколько (локация × тип диска), а схема `custom` одна, поэтому
    границы объединяются: `min` — самый мягкий, `max` — самый щедрый, а `step` —
    самый КРУПНЫЙ из шагов (значение, кратное большему шагу, подойдёт и там, где
    шаг мельче; наоборот — нет). Конкретный конфигуратор выбирается по локации уже
    при заказе, и последнее слово всё равно за валидацией Timeweb Cloud."""
    mins: dict[str, list[float]] = {"cpu": [], "ram_gb": [], "disk_gb": []}
    maxs: dict[str, list[float]] = {"cpu": [], "ram_gb": [], "disk_gb": []}
    steps: dict[str, list[float]] = {"cpu": [], "ram_gb": [], "disk_gb": []}
    # (ключ схемы, префикс полей вендора, конвертер)
    axes = (("cpu", "cpu", _num), ("ram_gb", "ram", _mb_to_gb),
            ("disk_gb", "disk", _mb_to_gb))

    for row in conf_rows:
        req = row.get("requirements")
        if not isinstance(req, dict):
            continue
        for key, prefix, conv in axes:
            for bucket, suffix in ((mins, "min"), (maxs, "max"), (steps, "step")):
                value = conv(req.get(f"{prefix}_{suffix}"))
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
    } for key in ("cpu", "ram_gb", "disk_gb")}


def _server_ip(raw: dict) -> str:
    """Главный публичный ipv4; если такого нет — первый попавшийся адрес."""
    fallback = ""
    for net in raw.get("networks") or []:
        if not isinstance(net, dict):
            continue
        public = str(net.get("type") or "").lower() == "public"
        for entry in net.get("ips") or []:
            if not isinstance(entry, dict):
                continue
            ip = str(entry.get("ip") or "").strip()
            if not ip:
                continue
            if public and str(entry.get("type") or "").lower() == "ipv4":
                if entry.get("is_main"):
                    return ip
                fallback = fallback or ip
            else:
                fallback = fallback or ip
    return fallback


def _server_item(raw: dict, costs: dict[str, float]) -> ServiceItem:
    sid = str(raw.get("id") or "")
    return ServiceItem(
        id=sid,
        name=str(raw.get("name") or "").strip() or f"сервер #{sid}",
        kind="vps",
        cost=costs.get(sid),
        currency="RUB",
        # Тарифы Timeweb Cloud считаются помесячно (списание почасовое, но и в
        # панели, и в `monthly_cost` фигурирует месяц).
        period="month",
        status=str(raw.get("status") or ""),
        ip=_server_ip(raw),
        region=str(raw.get("location") or ""),
        # Предоплаченного «оплачено до» у почасовой модели нет.
        paid_till="",
    )


ADAPTER = TimewebAdapter()
