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
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.services.hosting_providers.base import (
    CredField,
    ProviderAdapter,
    ServiceItem,
    map_http_error,
    redact,
)

log = logging.getLogger("hosting.servers_com")

_BASE = "https://api.servers.com/v1"

_PER_PAGE = 100
_MAX_PAGES = 5


class ServersComAdapter(ProviderAdapter):
    KIND = "servers_com"
    TITLE = "Servers.com"
    FIELDS = [CredField("token", "API-токен", "password")]
    # Без "balance" и "payments": публичный API счетов не отдаёт (см. шапку).
    CAPS = {"services"}

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
