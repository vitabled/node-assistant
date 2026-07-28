"""IONOS — выделенный Billing API (счета).

База `https://api.ionos.com/billing/v3`, эндпоинт `/invoices`.

**Главная особенность — двойная авторизация.** IONOS принимает и **Bearer**
(токен из DCD → Token Manager), и **Basic** (e-mail + пароль личного кабинета).
Поэтому все три поля объявлены НЕобязательными, а способ выбирается по тому, что
заполнено: `check_fields()` тут не годится (он требует все `required`), проверку
делает `_auth_kwargs()` — ни токена, ни пары логин+пароль → «заполните токен или
логин с паролем».

**Баланса нет и он не заявлен в CAPS.** IONOS — постоплатная контрактная модель:
в billing-API публикуются счета, а не остаток средств. Достоверного поля остатка
не нашлось, поэтому `balance()` отдаёт `None` — UI покажет честное «баланс
вводится вручную», а не «синхронизация не удалась».

**Потребление ресурсов НЕ читается.** Отдельная ручка потребления в billing-API
есть по описанию вендора, но её точный путь и форма на живом контракте не
снимались. Угаданный URL дал бы пустой список, неотличимый от «расхода нет», —
это хуже отсутствия функции. Место для доработки после разведки на живом
контракте (тогда же проверить и `balance`).

⚠️ Форма ответа тоже не снималась: суммы/даты читаются из списка правдоподобных
имён (приём из `veesp`/`aeza`), а коллекции IONOS традиционно заворачивают строку
в `properties` — это снимается. Незнакомая форма даёт `[]` и warning в лог.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.services.hosting_providers.base import (
    CredField,
    ProviderAdapter,
    map_http_error,
    redact,
)

log = logging.getLogger("hosting.ionos")

_BASE = "https://api.ionos.com/billing/v3"

_TS_KEYS = ("invoiceDate", "documentDate", "issueDate", "date", "createdDate",
            "created", "createdAt")
_AMOUNT_KEYS = ("totalGross", "grossAmount", "amountGross", "total", "amount",
                "sum", "totalNet", "netAmount")
_CURRENCY_KEYS = ("currency", "currencyCode", "currency_code")
_NOTE_KEYS = ("documentNumber", "invoiceNumber", "number", "description",
              "status", "id")


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


def _secrets(creds: dict) -> tuple[str, ...]:
    creds = creds or {}
    return (str(creds.get("token") or ""), str(creds.get("password") or ""))


def _auth_kwargs(creds: dict) -> tuple[dict, str]:
    """httpx-kwargs выбранного способа авторизации, либо причина отказа."""
    creds = creds or {}
    token = str(creds.get("token") or "").strip()
    username = str(creds.get("username") or "").strip()
    password = str(creds.get("password") or "")
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        return {"headers": headers}, ""
    if username and password:
        return {"headers": headers, "auth": (username, password)}, ""
    return {}, "заполните токен или логин с паролем"


def _rows(payload: Any) -> list[dict]:
    """Строки коллекции из любой из трёх виденных у IONOS форм."""
    raw = payload
    if isinstance(payload, dict):
        for key in ("items", "invoices", "data", "content"):
            found = payload.get(key)
            if isinstance(found, list):
                raw = found
                break
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        # Коллекции IONOS кладут поля записи внутрь `properties`.
        props = row.get("properties")
        out.append({**row, **props} if isinstance(props, dict) else row)
    return out


class IonosAdapter(ProviderAdapter):
    KIND = "ionos"
    TITLE = "IONOS"
    FIELDS = [
        CredField("token", "API-токен (Token Manager)", "password", required=False),
        CredField("username", "E-mail личного кабинета", "text", required=False),
        CredField("password", "Пароль личного кабинета", "password", required=False),
    ]
    # Без "balance": остатка средств в billing-API нет (см. докстроку).
    CAPS = {"payments"}

    async def _get(self, creds: dict, path: str) -> tuple[Any, str]:
        kwargs, err = _auth_kwargs(creds)
        if err:
            return None, err
        try:
            async with self._client() as c:
                r = await c.get(f"{_BASE}{path}", **kwargs)
        except httpx.HTTPError as exc:
            return None, "IONOS недоступен: " + redact(str(exc), *_secrets(creds))

        if r.status_code >= 400:
            return None, map_http_error(r.status_code)
        try:
            return r.json(), ""
        except ValueError:
            return None, "IONOS вернул не-JSON ответ"

    async def verify(self, creds: dict) -> tuple[bool, str]:
        _data, err = await self._get(creds, "/invoices")
        return (False, err) if err else (True, "")

    async def payments(self, creds: dict) -> list[dict]:
        data, err = await self._get(creds, "/invoices")
        if err:
            return []
        rows = _rows(data)
        if not rows:
            if data:
                log.warning("ionos: незнакомая форма ответа /invoices")
            return []
        out: list[dict] = []
        for raw in rows:
            out.append({
                "ts": _pick_str(raw, _TS_KEYS),
                "amount": _pick_number(raw, _AMOUNT_KEYS),
                "currency": _pick_str(raw, _CURRENCY_KEYS, "EUR").upper(),
                # Счёт — это всегда начисление; возвратов в /invoices не бывает.
                "type": "charge",
                "note": _pick_str(raw, _NOTE_KEYS),
            })
        return out


ADAPTER = IonosAdapter()
