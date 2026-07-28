"""Selectel — баланс аккаунта через `GET https://api.selectel.ru/v3/balances`.

Авторизация: статический токен аккаунта (панель → Профиль и настройки → Ключи
API), заголовок `X-Auth-Token`. Подходит и IAM-токен со скоупом аккаунта.

Ответ вложенный: `data.billings[]` — по типу биллинга (облако, выделенные
серверы и т.д.), внутри `balances[]` со значениями. Суммируем `final_sum`
(если он есть) либо значения балансов: пользователю в карточке нужен один
общий остаток, а не разбивка по типам.

⚠️ **Единицы не задокументированы явно** — исторически Selectel отдаёт суммы в
КОПЕЙКАХ. Наличие явного делителя не подтверждено, поэтому значение берётся как
есть и НЕ делится: молча уменьшить чужой баланс в сто раз хуже, чем показать
сырое число, которое человек сверит с панелью. Если на живом аккаунте окажутся
копейки — добавить деление здесь, в одном месте.

⚠️ **Заказ: `CAPS` НЕ заявляет `order`, и это не недоделка.** У Selectel
конструктор действительно есть («произвольная конфигурация»), но создаются такие
серверы через OpenStack/Nova, а не через `api.selectel.ru`, и для этого нужны
ПРОЕКТНЫЕ креды Keystone (пользователь+пароль+домен), которых у этого адаптера
нет — здесь только аккаунтский статический токен. Этот путь уже покрыт отдельным
адаптером `openstack`. Публичной ручки, которая заказала бы сервер по токену
аккаунта, подтвердить не удалось, поэтому `create_order` отказывает словами, а
не «принимает» заказ, который никуда не уйдёт.

Следствие, о котором стоит помнить: `/providers/{uuid}/order-options` гейтится на
`CAPS`, поэтому `order_options()` ниже через API сейчас недостижим — он оставлен
как справочные границы конструктора для будущего OpenStack-пути и для формы,
если её решат показывать в режиме «только справка».
"""
from __future__ import annotations

from typing import Any, Optional

from app.services.hosting_providers.base import (
    Balance, CredField, OrderOptions, ProviderAdapter, map_http_error, redact,
)

_URL = "https://api.selectel.ru/v3/balances"

# Границы конструктора. `max: None` = «вендор не публикует верхнюю границу»
# (семантика base.OrderOptions), а не «безлимит»: в документации предельные
# значения найти не удалось — публикации вендора называют порядок «сотни vCPU и
# сотни ГБ RAM», но подтверждения в docs нет, а выдуманный потолок отрезал бы
# реальную конфигурацию. Минимумы — от самой младшей линейки.
# Не выражено здесь: Selectel требует соотношения ядер к памяти (память кратно
# больше ядер) — схема {min,max,step} такого ограничения не описывает, и
# последнее слово всё равно за валидацией провайдера.
_CUSTOM = {
    "cpu": {"min": 1, "max": None, "step": 1},
    "ram_gb": {"min": 1, "max": None, "step": 1},
    "disk_gb": {"min": 1, "max": None, "step": 1},
}


def _num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class SelectelAdapter(ProviderAdapter):
    KIND = "selectel"
    TITLE = "Selectel"
    FIELDS = [CredField("token", "Статический токен API", "password")]
    CAPS = {"balance"}

    async def _get(self, creds: dict) -> tuple[Any, str]:
        token = (creds.get("token") or "").strip()
        try:
            async with self._client() as c:
                r = await c.get(_URL, headers={"X-Auth-Token": token})
        except Exception as exc:
            return None, redact(f"Selectel недоступен: {exc}", token)
        if r.status_code >= 400:
            return None, map_http_error(r.status_code)
        try:
            return r.json(), ""
        except Exception:
            return None, "Selectel вернул не-JSON"

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        _, err = await self._get(creds)
        return (False, err) if err else (True, "")

    async def balance(self, creds: dict) -> Optional[Balance]:
        if self.check_fields(creds):
            return None
        data, err = await self._get(creds)
        if err or not isinstance(data, dict):
            return None
        node = data.get("data") if isinstance(data.get("data"), dict) else data
        currency = str(((node.get("settings") or {}).get("currency")) or "RUB").upper()

        total: Optional[float] = None
        for b in node.get("billings") or []:
            if not isinstance(b, dict):
                continue
            value = _num(b.get("final_sum"))
            if value is None:
                # Нет итоговой суммы — складываем сами значения балансов.
                parts = [_num(x.get("value")) for x in (b.get("balances") or [])
                         if isinstance(x, dict)]
                parts = [p for p in parts if p is not None]
                value = sum(parts) if parts else None
            if value is not None:
                total = value if total is None else total + value
        if total is None:
            return None
        return Balance(total, currency)

    async def order_options(self, creds: dict) -> Optional[OrderOptions]:
        """Справочные границы конструктора. Сети не трогает, поэтому и креды не
        проверяет — отдавать нечего, кроме констант."""
        return OrderOptions(plans=[], regions=[], images=[], custom=dict(_CUSTOM))

    async def create_order(self, creds: dict, spec: dict) -> dict:
        return {
            "ok": False, "id": "", "name": "", "price": None, "currency": "RUB",
            "error": "Selectel не отдаёт заказ через публичный API — оформите в панели",
        }


ADAPTER = SelectelAdapter()
