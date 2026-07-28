"""Alibaba Cloud — баланс и счёт за месяц через BSS OpenAPI 2017-12-14.

RPC-стиль: один эндпоинт `GET https://business.aliyuncs.com/`, операция задаётся
параметром `Action` (`QueryAccountBalance` / `QueryBillOverview`), остальное —
общие параметры подписи в том же query.

Подпись (HMAC-SHA1 по RPC-схеме Alibaba) устроена так:

1. **процентное кодирование по правилам Alibaba** — RFC-3986 с тремя отличиями от
   обычного `urlencode`: пробел даёт `%20` (а не `+`), «`~`» НЕ кодируется, «`*`»
   кодируется в `%2A`. Ровно это даёт `quote(value, safe="~")`;
2. параметры (кроме самой подписи) сортируются по ключу и склеиваются в
   `k=v&k=v` — **уже закодированными**;
3. `StringToSign = "GET&" + enc("/") + "&" + enc(canonical_query)`, то есть
   canonical query кодируется ЦЕЛИКОМ ещё раз;
4. ключ HMAC — `secret + "&"` (амперсанд обязателен), результат в base64.

Грабли:

- **`SignatureNonce` обязан быть уникальным**, а `Timestamp` — в UTC формата
  `YYYY-MM-DDTHH:MM:SSZ`; расхождение часов больше 15 минут отклоняется.
- **`AvailableAmount` приходит строкой с РАЗДЕЛИТЕЛЯМИ ТЫСЯЧ** — «1,234.56».
  Запятая здесь не десятичная (в отличие от других адаптеров, где мы меняем «,»
  на «.»), её надо ВЫРЕЗАТЬ, иначе баланс либо не распарсится, либо станет 1.23.
- **Неверный AccessKeyId отдаёт HTTP 404** (`InvalidAccessKeyId.NotFound`), а не
  401/403. Без разбора `Code` из тела пользователь увидел бы «ручка API не
  найдена» вместо «неверные креды».
- **Признак успеха пишется двумя способами**: `"Code": "200"` у баланса и
  `"Code": "Success"` у части ручек, плюс булев `Success`. Проверяем оба.
- **Список счёта вложен дважды** — `Data.Items.Item[]` (наследие RPC-XML).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import urllib.parse
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.services.hosting_providers.base import (
    Balance,
    CredField,
    ProviderAdapter,
    map_http_error,
    redact,
)

log = logging.getLogger("hosting.alibaba")

_ENDPOINT = "https://business.aliyuncs.com/"
_VERSION = "2017-12-14"

# Коды, которые означают «креды не те», независимо от HTTP-статуса.
_AUTH_CODES = {
    "InvalidAccessKeyId.NotFound",
    "InvalidAccessKeyId",
    "SignatureDoesNotMatch",
    "SignatureNonceUsed",
    "InvalidSecurityToken.Expired",
    "NoPermission",
}


def percent_encode(value: Any) -> str:
    """Кодирование по правилам Alibaba: `%20` вместо `+`, «~» как есть, «*»→`%2A`."""
    return urllib.parse.quote(str(value), safe="~")


def string_to_sign(method: str, params: dict) -> str:
    canonical = "&".join(f"{percent_encode(k)}={percent_encode(v)}"
                         for k, v in sorted(params.items()))
    return f"{method.upper()}&{percent_encode('/')}&{percent_encode(canonical)}"


def sign(params: dict, secret: str, method: str = "GET") -> str:
    digest = hmac.new((secret + "&").encode(),
                      string_to_sign(method, params).encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def _nonce() -> str:
    return uuid.uuid4().hex


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _billing_cycle(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m")


def _num(value: Any) -> Optional[float]:
    """Суммы приходят строками с разделителями тысяч: «1,234.56» → 1234.56.
    Запятая ВЫРЕЗАЕТСЯ, а не превращается в точку — она не десятичная."""
    text = str(value if value is not None else "").replace(",", "").replace("\xa0", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _ok(payload: dict) -> bool:
    if payload.get("Success") is True:
        return True
    return str(payload.get("Code") or "") in ("200", "Success")


class AlibabaAdapter(ProviderAdapter):
    KIND = "alibaba"
    TITLE = "Alibaba Cloud"
    FIELDS = [
        CredField("access_key_id", "AccessKey ID"),
        CredField("access_key_secret", "AccessKey Secret", "password"),
    ]
    CAPS = {"balance", "payments"}

    async def _call(self, creds: dict, action: str,
                    extra: Optional[dict] = None) -> tuple[Any, str]:
        akid = str((creds or {}).get("access_key_id") or "").strip()
        secret = str((creds or {}).get("access_key_secret") or "").strip()
        params = {
            "Action": action,
            "Format": "JSON",
            "Version": _VERSION,
            "AccessKeyId": akid,
            "SignatureMethod": "HMAC-SHA1",
            "SignatureVersion": "1.0",
            "SignatureNonce": _nonce(),
            "Timestamp": _timestamp(),
            **(extra or {}),
        }
        params["Signature"] = sign(params, secret)

        try:
            async with self._client() as c:
                r = await c.get(_ENDPOINT, params=params)
        except httpx.HTTPError as exc:
            return None, f"Alibaba Cloud недоступен: {redact(str(exc), secret, akid)}"

        if r.status_code >= 400:
            return None, _http_error(r, secret, akid)
        try:
            payload = r.json()
        except ValueError:
            return None, "Alibaba Cloud вернул не-JSON ответ"
        if not isinstance(payload, dict):
            return None, "Alibaba Cloud вернул неожиданный ответ"
        if not _ok(payload):
            code = str(payload.get("Code") or "")
            if code in _AUTH_CODES:
                return None, "неверные креды"
            return None, "Alibaba Cloud отклонил запрос" + (
                f" ({redact(code, secret, akid)})" if code else "")
        return payload, ""

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        _data, err = await self._call(creds, "QueryAccountBalance")
        return (False, err) if err else (True, "")

    async def balance(self, creds: dict) -> Optional[Balance]:
        if self.check_fields(creds):
            return None
        data, err = await self._call(creds, "QueryAccountBalance")
        if err or not isinstance(data, dict):
            return None
        block = data.get("Data")
        if not isinstance(block, dict):
            log.warning("alibaba: в QueryAccountBalance нет Data")
            return None
        # AvailableAmount — доступная сумма (кэш + кредитный лимит); именно её
        # сравнивают с порогом низкого баланса.
        amount = _num(block.get("AvailableAmount"))
        if amount is None:
            amount = _num(block.get("AvailableCashAmount"))
        if amount is None:
            log.warning("alibaba: не разобрал AvailableAmount")
            return None
        return Balance(amount, str(block.get("Currency") or "CNY").upper())

    async def payments(self, creds: dict) -> list[dict]:
        """Строки счёта за текущий расчётный месяц — по одной на продукт."""
        if self.check_fields(creds):
            return []
        cycle = _billing_cycle()
        data, err = await self._call(creds, "QueryBillOverview", {"BillingCycle": cycle})
        if err or not isinstance(data, dict):
            return []
        block = data.get("Data") if isinstance(data.get("Data"), dict) else {}
        items = block.get("Items")
        if isinstance(items, dict):        # RPC-наследие: Data.Items.Item[]
            items = items.get("Item")
        if not isinstance(items, list):
            log.warning("alibaba: неожиданная форма QueryBillOverview")
            return []

        out: list[dict] = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            amount = _num(raw.get("PretaxAmount"))
            if amount is None:
                amount = _num(raw.get("PretaxGrossAmount"))
            out.append({
                "ts": str(raw.get("BillingCycle") or block.get("BillingCycle") or cycle),
                "amount": amount,
                "currency": str(raw.get("Currency") or "CNY").upper(),
                "type": "charge",
                "note": str(raw.get("ProductName") or raw.get("ProductCode") or ""),
            })
        return out


def _http_error(r: httpx.Response, *secrets: str) -> str:
    code = ""
    try:
        body = r.json()
        code = str((body or {}).get("Code") or "") if isinstance(body, dict) else ""
    except ValueError:
        code = ""
    # Неверный ключ приходит как 404 — без этой ветки пользователь получил бы
    # «ручка API не найдена» и искал бы несуществующую проблему.
    if code in _AUTH_CODES:
        return "неверные креды"
    base = map_http_error(r.status_code)
    return f"{base} ({redact(code, *secrets)})" if code else base


ADAPTER = AlibabaAdapter()
