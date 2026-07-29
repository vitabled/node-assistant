"""Infomaniak — счета через публичный API.

База `https://api.infomaniak.com`, заголовок `Authorization: Bearer <токен>`.
Токен выпускается в личном кабинете (Manager → API tokens) и ДОЛЖЕН нести scope
**`invoicing`** — без него ручка счетов ответит 403, а не пустым списком.

⚠️ **HTTP 200 у Infomaniak не значит «успех».** Ответ завёрнут в конверт
`{"result": "success"|"error", "data": …}`, и отказ приходит двухсотым кодом с
`result: "error"`. Поэтому конверт разбирается ДО чтения данных — иначе описание
ошибки молча превратилось бы в «нет счетов».

Счета привязаны к аккаунту, а у одного токена аккаунтов может быть несколько,
поэтому `account_id` — необязательное поле: пусто → берём первый из `/1/account`.

**Баланса нет и он не заявлен в CAPS.** Infomaniak работает по счетам и предоплате
за продукт; достоверного поля остатка средств в публичном API не нашлось, так что
`balance()` отдаёт `None` — честное «баланс вводится вручную».

⚠️ Путь счетов и форма строк на живом аккаунте не снимались: читатель защитный
(ключи ищутся по списку правдоподобных написаний), а 404/403 дают пустой список,
не исключение. Это первое место под правку после разведки.

**⚠️ Заказ: `CAPS` НЕ заявляет `order`, и это не недоделка.** Публичной ручки,
которая создавала бы сервер по API-токену, подтвердить не удалось: в открытой
части API Infomaniak — продукты, счета, домены и веб-хостинг, а покупка
оформляется в Manager. Угаданный `POST` сюда стоил бы чужих денег, поэтому
`create_order` отказывает СЛОВАМИ и без запроса.

Полезная замена, а не тупик: **Infomaniak Public Cloud — это OpenStack**, и он
уже покрыт адаптером `openstack` (Keystone `https://api.pub1.infomaniak.cloud/
identity`, проектные креды). Отказ прямо на него и указывает.
"""
from __future__ import annotations

import logging
import urllib.parse
from typing import Any, Optional

import httpx

from app.services.hosting_providers.base import (
    CredField,
    ProviderAdapter,
    map_http_error,
    redact,
)

log = logging.getLogger("hosting.infomaniak")

_BASE = "https://api.infomaniak.com"

_TS_KEYS = ("date", "created_at", "issued_at", "invoice_date", "due_date",
            "created")
_AMOUNT_KEYS = ("amount_tax_incl", "total_tax_incl", "amount", "total",
                "price", "amount_tax_excl", "total_tax_excl")
_CURRENCY_KEYS = ("currency", "currency_code")
_NOTE_KEYS = ("number", "reference", "name", "state", "status", "description")

_ORDER_UNSUPPORTED = (
    "Infomaniak не публикует API заказа серверов — оформите покупку в Manager. "
    "Для Public Cloud подойдёт адаптер «OpenStack»: он и есть их облако "
    "(Keystone https://api.pub1.infomaniak.cloud/identity, проектные креды)"
)


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
        text = str(node.get(key) or "").strip()
        if text:
            return text
    return default


class InfomaniakAdapter(ProviderAdapter):
    KIND = "infomaniak"
    TITLE = "Infomaniak"
    FIELDS = [
        CredField("token", "API-токен (scope invoicing)", "password"),
        CredField("account_id", "ID аккаунта (пусто — возьмём первый)",
                  "text", required=False),
    ]
    # Без "balance": остатка средств в публичном API нет (см. докстроку).
    CAPS = {"payments"}

    async def _get(self, creds: dict, path: str) -> tuple[Any, str]:
        token = str((creds or {}).get("token") or "").strip()
        try:
            async with self._client() as c:
                r = await c.get(f"{_BASE}{path}", headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                })
        except httpx.HTTPError as exc:
            return None, "Infomaniak недоступен: " + redact(str(exc), token)

        if r.status_code >= 400:
            return None, map_http_error(r.status_code)
        try:
            payload = r.json()
        except ValueError:
            return None, "Infomaniak вернул не-JSON ответ"

        if isinstance(payload, dict):
            if str(payload.get("result") or "").lower() == "error":
                # Отказ приезжает кодом 200 — см. докстроку.
                error = payload.get("error")
                text = ""
                if isinstance(error, dict):
                    text = str(error.get("description")
                               or error.get("code") or "").strip()
                elif error:
                    text = str(error).strip()
                return None, ("Infomaniak отклонил запрос"
                              + (": " + redact(text, token) if text else ""))
            if "data" in payload:
                return payload.get("data"), ""
        return payload, ""

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        _data, err = await self._get(creds, "/2/profile")
        return (False, err) if err else (True, "")

    async def _account_id(self, creds: dict) -> str:
        given = str((creds or {}).get("account_id") or "").strip()
        if given:
            return given
        data, err = await self._get(creds, "/1/account")
        if err:
            return ""
        rows = data if isinstance(data, list) else []
        for row in rows:
            if isinstance(row, dict):
                found = str(row.get("id") or row.get("account_id") or "").strip()
                if found:
                    return found
        log.warning("infomaniak: не удалось определить аккаунт для счетов")
        return ""

    async def payments(self, creds: dict) -> list[dict]:
        if self.check_fields(creds):
            return []
        account = await self._account_id(creds)
        if not account:
            return []
        # account_id вводит пользователь, а он идёт сегментом пути — квотируем,
        # иначе «../» увёл бы запрос на соседнюю ручку.
        account = urllib.parse.quote(account, safe="")
        data, err = await self._get(creds, f"/1/invoicing/{account}/invoices")
        if err:
            return []
        rows = data
        if isinstance(data, dict):
            for key in ("invoices", "items", "data"):
                found = data.get(key)
                if isinstance(found, list):
                    rows = found
                    break
        if not isinstance(rows, list):
            log.warning("infomaniak: незнакомая форма ответа со счетами")
            return []

        out: list[dict] = []
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            out.append({
                "ts": _pick_str(raw, _TS_KEYS),
                "amount": _pick_number(raw, _AMOUNT_KEYS),
                "currency": _pick_str(raw, _CURRENCY_KEYS, "EUR").upper(),
                # Счета — начисления; возвраты в этой ручке не приходят.
                "type": "charge",
                "note": _pick_str(raw, _NOTE_KEYS),
            })
        return out

    async def create_order(self, creds: dict, spec: dict) -> dict:
        """Честный отказ БЕЗ единого запроса — см. докстринг модуля."""
        return {"ok": False, "id": "", "name": "", "price": None, "currency": "",
                "error": _ORDER_UNSUPPORTED}


ADAPTER = InfomaniakAdapter()
