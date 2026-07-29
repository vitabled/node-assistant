"""Cloud.ru — проверка ключа доступа через IAM.

Авторизация двухшаговая и это единственная часть, зафиксированная публично:
`POST https://iam.api.cloud.ru/api/v1/auth/token` с телом `{"keyId","secret"}`
отдаёт `{"access_token", "expires_in", "token_type"}`; полученный токен дальше
подставляется как `Authorization: Bearer <token>`.

**Почему CAPS пустой.** Формы биллинговых ручек потребления Cloud.ru публично не
зафиксированы: ни стабильного пути, ни схемы ответа. Вызывать выдуманный URL
токеном пользователя нельзя (это запрос неизвестно куда и молчаливая ложь в
интерфейсе), поэтому адаптер сейчас умеет ровно одно — подтвердить, что пара
key_id/key_secret рабочая. Инфра-биллинг покажет «баланс вручную».

**Что нужно снять на живом аккаунте, чтобы включить баланс/услуги**: точный путь
ручки потребления, ключ суммы и валюты в ответе, признак периода. После этого
пишутся `balance()`/`payments()` (запрос — `Authorization: Bearer` тем же токеном,
что отдаёт `_token`) и в `CAPS` добавляется соответствующий элемент.

**⚠️ Заказ: `CAPS` НЕ заявляет `order`, и это не недоделка.** Публично
зафиксирована только описанная выше пара «ключ → токен»; ни пути ручки создания
инстанса, ни формы её тела подтвердить не удалось, а обязательных идентификаторов
там заведомо больше, чем в нашей спецификации заказа (проект, зона доступности,
образ, тип инстанса, сеть) — и вывести их неоткуда, потому что каталогов мы тоже
не знаем. Отправить угаданное тело чужим ключом значит либо получить 404, либо
создать НЕ ту машину за деньги пользователя, поэтому `create_order` отказывает
словами и БЕЗ запроса.

**Что нужно снять, чтобы включить заказ**: путь и тело создания инстанса, ручки
каталогов (зоны, образы, типы инстансов, сети), поле идентификатора в ответе и
признак того, синхронный это ответ или операция. После этого пишутся
`order_options()`/`create_order()` и в `CAPS` добавляется `order`.

Токен НЕ кэшируется намеренно: единственный его потребитель — `verify()`, а
кэш, ключуемый по `key_id`, подтверждал бы старый токен после смены секрета,
то есть врал бы ровно в том, ради чего `verify()` и вызывают.
"""
from __future__ import annotations

import logging

import httpx

from app.services.hosting_providers.base import (
    CredField,
    ProviderAdapter,
    map_http_error,
    redact,
)

log = logging.getLogger("hosting.cloudru")

_TOKEN_URL = "https://iam.api.cloud.ru/api/v1/auth/token"

_ORDER_UNSUPPORTED = ("Cloud.ru: ручка создания инстанса публично не зафиксирована "
                      "— закажите в консоли Cloud.ru")


class CloudruAdapter(ProviderAdapter):
    KIND = "cloudru"
    TITLE = "Cloud.ru"
    FIELDS = [
        CredField("key_id", "Key ID"),
        CredField("key_secret", "Key Secret", "password"),
    ]
    # Только то, что реально работает: подтверждение кредов. См. докстринг.
    CAPS: set[str] = set()

    async def _token(self, creds: dict) -> tuple[str, str]:
        key_id = str((creds or {}).get("key_id") or "").strip()
        secret = str((creds or {}).get("key_secret") or "").strip()
        try:
            async with self._client() as c:
                r = await c.post(_TOKEN_URL, json={"keyId": key_id, "secret": secret})
        except httpx.HTTPError as exc:
            return "", f"Cloud.ru недоступен: {redact(str(exc), secret, key_id)}"

        if r.status_code >= 400:
            return "", map_http_error(r.status_code)
        try:
            payload = r.json()
        except ValueError:
            return "", "Cloud.ru вернул не-JSON ответ"
        token = str((payload or {}).get("access_token") or "") if isinstance(payload, dict) else ""
        if not token:
            log.warning("cloudru: в ответе IAM нет access_token")
            return "", "Cloud.ru не вернул токен доступа"
        return token, ""

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        _token, err = await self._token(creds)
        return (False, err) if err else (True, "")

    async def create_order(self, creds: dict, spec: dict) -> dict:
        """Отказ БЕЗ сетевого запроса — см. докстринг модуля.

        Своими словами, а не дефолтом базы: пользователю нужно знать, что дело в
        неподтверждённой ручке вендора, а не в его кредах."""
        return {
            "ok": False, "id": "", "name": "", "price": None, "currency": "",
            "error": _ORDER_UNSUPPORTED,
        }


ADAPTER = CloudruAdapter()
