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
"""
from __future__ import annotations

import logging
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

log = logging.getLogger("hosting.timeweb")

_BASE = "https://api.timeweb.cloud"

_PER_PAGE = 100
_MAX_PAGES = 5


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
    CAPS = {"balance", "services"}

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
