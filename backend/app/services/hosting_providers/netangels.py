"""NetAngels adapter — только баланс.

⚠️ **Авторизация двухшаговая**, и это главное отличие от остальных «простых»
адаптеров:

1. `POST https://panel.netangels.ru/api/gateway/token/` с полем формы `api_key`
   → выдаёт Bearer-токен;
2. `GET https://api-ms.netangels.ru/api/v1/account/info/` с этим токеном → поле
   `balance`.

Токен **живёт 24 часа с последнего использования** (скользящий срок), поэтому он
кэшируется в памяти процесса — как IAM-токен в `yandex.py`. Без кэша каждый опрос
дашборда и каждый тик фонового синка делали бы лишний обмен, а API авторизации —
самое неприятное место, чтобы ловить лимиты. Кэшируем на 12 ч: вдвое меньше срока
жизни, так что протухнуть между обращениями токен не успевает.

Ключ кэша — **sha256 от api_key**, а не сам ключ: незачем держать секрет ещё и
ключом словаря модульного уровня.

Списков услуг и счетов в этом API нет — `CAPS` объявляет только `balance`,
`services()`/`payments()` остаются пустыми из базового класса. Это честное «API не
отдаёт», а не недоделка.

**⚠️ Заказ: `CAPS` НЕ заявляет `order`, и это тоже не недоделка.** NetAngels —
это в первую очередь хостинг с панелью: подтверждена ровно одна пара ручек
(токен + профиль аккаунта), ручки заказа услуги в публичном API нет, а
обязательные для неё идентификаторы (тариф, площадка, шаблон ОС) взять неоткуда
— каталогов API тоже не отдаёт. Кнопка, за которой стоит угаданный URL, здесь
означала бы либо 404, либо оплаченную не ту услугу, поэтому `create_order`
отказывает словами и БЕЗ запроса.

**Что нужно снять, чтобы включить заказ**: путь и тело ручки заказа, ручки
каталогов (тарифы, площадки, шаблоны ОС), поле идентификатора созданной услуги в
ответе. После этого пишутся `order_options()`/`create_order()`, и в `CAPS`
добавляется `order`.
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Optional

import httpx

from app.services.hosting_providers.base import (
    Balance,
    CredField,
    ProviderAdapter,
    map_http_error,
    redact,
)

log = logging.getLogger("hosting.netangels")

_TOKEN_URL = "https://panel.netangels.ru/api/gateway/token/"
_ACCOUNT_URL = "https://api-ms.netangels.ru/api/v1/account/info/"

_TOKEN_TTL = 12 * 3600      # вдвое меньше вендорских 24 ч «с последнего запроса»

# sha256(api_key) → (токен, когда протухнет). Модульный уровень намеренно: опрос
# дашборда и фоновый синк живут в одном процессе и делят один кэш.
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}

_AMOUNT_KEYS = ("balance", "amount", "value", "sum")

_ORDER_UNSUPPORTED = ("NetAngels: заказ услуги через публичное API не "
                      "предусмотрен — оформите в панели NetAngels")


def _cache_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8", "replace")).hexdigest()


class NetangelsAdapter(ProviderAdapter):
    KIND = "netangels"
    TITLE = "NetAngels"
    FIELDS = [CredField("api_key", "API-ключ", "password")]
    CAPS = {"balance"}

    async def _token(self, creds: dict) -> tuple[str, str]:
        """Кэшированный Bearer-токен → (токен, ошибка)."""
        api_key = str((creds or {}).get("api_key") or "").strip()
        slot = _cache_key(api_key)
        cached = _TOKEN_CACHE.get(slot)
        if cached and cached[1] > time.time():
            return cached[0], ""

        try:
            async with self._client() as c:
                r = await c.post(_TOKEN_URL, data={"api_key": api_key})
        except httpx.HTTPError as exc:
            return "", f"NetAngels недоступен: {redact(str(exc), api_key)}"

        if r.status_code >= 400:
            return "", map_http_error(r.status_code)
        try:
            body = r.json()
        except ValueError:
            return "", "NetAngels вернул не-JSON ответ"

        token = str((body or {}).get("token") or "").strip() if isinstance(body, dict) else ""
        if not token:
            return "", "NetAngels не вернул токен"
        _TOKEN_CACHE[slot] = (token, time.time() + _TOKEN_TTL)
        return token, ""

    async def _account_info(self, creds: dict) -> tuple[Any, str]:
        api_key = str((creds or {}).get("api_key") or "").strip()
        token, err = await self._token(creds)
        if err:
            return None, err
        try:
            async with self._client() as c:
                r = await c.get(_ACCOUNT_URL, headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                })
        except httpx.HTTPError as exc:
            return None, f"NetAngels недоступен: {redact(str(exc), api_key, token)}"

        if r.status_code in (401, 403):
            # Токен могли отозвать раньше срока — выбрасываем из кэша, чтобы
            # следующий вызов авторизовался заново, а не ждал 12 часов.
            _TOKEN_CACHE.pop(_cache_key(api_key), None)
            return None, map_http_error(r.status_code)
        if r.status_code >= 400:
            return None, map_http_error(r.status_code)
        try:
            return r.json(), ""
        except ValueError:
            return None, "NetAngels вернул не-JSON ответ"

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        # Обмен ключа на токен — уже проверка ключа, но берём и профиль: токен
        # может выдаваться, а прав на аккаунт не быть.
        _data, err = await self._account_info(creds)
        return (False, err) if err else (True, "")

    async def balance(self, creds: dict) -> Optional[Balance]:
        if self.check_fields(creds):
            return None
        data, err = await self._account_info(creds)
        if err or not isinstance(data, dict):
            return None
        node = data
        nested = data.get("account") or data.get("data")
        if not any(key in data for key in _AMOUNT_KEYS) and isinstance(nested, dict):
            node = nested
        for key in _AMOUNT_KEYS:
            if key in node:
                try:
                    # NetAngels — российский провайдер, тарифы рублёвые; поля
                    # валюты в ответе нет.
                    return Balance(float(str(node[key]).strip()), "RUB")
                except (TypeError, ValueError):
                    continue
        log.warning("netangels: no recognised balance key in /account/info/")
        return None

    async def create_order(self, creds: dict, spec: dict) -> dict:
        """Отказ БЕЗ сетевого запроса — см. докстринг модуля.

        Своими словами, а не дефолтом базы: пользователю нужно знать, что дело в
        отсутствующей ручке вендора, а не в его ключе."""
        return {
            "ok": False, "id": "", "name": "", "price": None, "currency": "RUB",
            "error": _ORDER_UNSUPPORTED,
        }


ADAPTER = NetangelsAdapter()
