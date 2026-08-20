"""
Linode cloud adapter — balance, servers, payments (Wave-9 Plan C, Ф1).

Linode API v4: https://www.linode.com/api/
Документация: https://www.linode.com/api/v4/

Возможности:
- balance: GET /account (get field 'balance')
- services: GET /linode/instances (список инстансов)
- payments: GET /account/invoices + GET /account/payments
- order: POST /linode/instances (создание сервера с конструктором или типом)

Особенности:
- Авторизация: Bearer token
- Все цены в USD (или локальной валюте аккаунта)
- Rate limit: 4 req/sec, или 240 req/min
- Поддерживает как fixed plans, так и custom configurations
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

log = logging.getLogger("hosting.linode")

_BASE = "https://api.linode.com/v4"


class LinodeAdapter(ProviderAdapter):
    KIND = "linode"
    TITLE = "Linode"
    FIELDS = [CredField("token", "API-токен", "password")]
    CAPS = {"balance", "services", "payments", "order"}

    async def _get(
        self, creds: dict, path: str, params: Optional[dict] = None
    ) -> tuple[Optional[dict], str]:
        """GET запрос к API Linode."""
        token = str((creds or {}).get("token") or "").strip()
        try:
            async with self._client() as c:
                r = await c.get(
                    f"{_BASE}{path}",
                    params=params,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                )
        except httpx.HTTPError as exc:
            return None, f"Linode недоступен: {redact(str(exc), token)}"

        if r.status_code >= 400:
            msg = map_http_error(r.status_code)
            # Попробуем прочитать детальное сообщение ошибки
            try:
                err_data = r.json()
                if isinstance(err_data, dict):
                    err_msg = str(err_data.get("errors", [{}])[0].get("field", "")) or str(err_data.get("errors", [{}])[0].get("reason", ""))
                    if err_msg:
                        msg = f"{msg}: {err_msg}"
            except ValueError:
                pass
            return None, msg

        try:
            data = r.json()
        except ValueError:
            return None, "Linode вернул не-JSON ответ"

        return (data if isinstance(data, dict) else {}), ""

    async def _post(
        self, creds: dict, path: str, body: dict
    ) -> tuple[Optional[dict], str]:
        """POST запрос (для заказа)."""
        token = str((creds or {}).get("token") or "").strip()
        try:
            async with self._client() as c:
                r = await c.post(
                    f"{_BASE}{path}",
                    json=body,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                )
        except httpx.HTTPError as exc:
            return None, f"Linode недоступен: {redact(str(exc), token)}"

        if r.status_code >= 400:
            return None, map_http_error(r.status_code)

        try:
            data = r.json()
        except ValueError:
            return None, "Linode вернул не-JSON ответ"

        return (data if isinstance(data, dict) else {}), ""

    async def verify(self, creds: dict) -> tuple[bool, str]:
        """Проверить валидность токена."""
        missing = self.check_fields(creds)
        if missing:
            return False, missing

        data, err = await self._get(creds, "/account")
        return (False, err) if err else (True, "")

    async def balance(self, creds: dict) -> Optional[Balance]:
        """Получить баланс аккаунта."""
        if self.check_fields(creds):
            return None

        data, err = await self._get(creds, "/account")
        if err or not data:
            return None

        try:
            # Linode возвращает баланс как account credit (negative = owed)
            # Мы показываем положительное значение как баланс
            credit = float(data.get("balance", 0))
            # Если баланс отрицательный, это задолженность
            balance_amount = max(0, credit)  # показываем только положительный баланс
            return Balance(balance_amount, "USD")
        except (KeyError, TypeError, ValueError):
            log.warning("linode: unexpected /account shape")
            return None

    async def services(self, creds: dict) -> list[ServiceItem]:
        """Получить список всех инстансов."""
        if self.check_fields(creds):
            return []

        data, err = await self._get(creds, "/linode/instances")
        if err or not data:
            return []

        out = []
        instances = data.get("data", [])
        if not isinstance(instances, list):
            return []

        for inst in instances:
            if not isinstance(inst, dict):
                continue

            # Цена в месяц
            hourly_price = inst.get("specs", {}).get("price", {}).get("monthly")
            try:
                cost = float(hourly_price) if hourly_price else None
            except (TypeError, ValueError):
                cost = None

            out.append(ServiceItem(
                id=str(inst.get("id", "")),
                name=str(inst.get("label", "") or f"Linode {inst.get('id', '')}"),
                kind="vps",
                cost=cost,
                currency="USD",
                period="month",
                status=str(inst.get("status", "running")),
                ip=str((inst.get("ipv4") or [""])[0]) if inst.get("ipv4") else "",
                region=str((inst.get("region") or "").upper()),
                paid_till=str(inst.get("expires", "")),
            ))

        return out

    async def payments(self, creds: dict) -> list[dict]:
        """Получить историю платежей (из счётов)."""
        if self.check_fields(creds):
            return []

        out = []

        # Получаем счёта
        invoices_data, err = await self._get(creds, "/account/invoices")
        if not err and invoices_data:
            invoices = invoices_data.get("data", [])
            if isinstance(invoices, list):
                for inv in invoices:
                    if not isinstance(inv, dict):
                        continue
                    try:
                        amount = float(inv.get("total", 0))
                    except (TypeError, ValueError):
                        amount = 0.0

                    # Дата счёта
                    date_str = inv.get("date")
                    ts = 0
                    if isinstance(date_str, str):
                        # Linode возвращает ISO 8601: 2023-11-15T00:00:00
                        try:
                            from datetime import datetime as dt
                            parsed = dt.fromisoformat(date_str.replace("Z", "+00:00"))
                            ts = int(parsed.timestamp())
                        except (ValueError, AttributeError):
                            ts = 0

                    out.append({
                        "ts": ts,
                        "amount": amount,
                        "currency": "USD",
                        "type": "charge",
                        "note": f"Invoice {inv.get('id', '')}",
                    })

        return out

    async def order_options(self, creds: dict) -> Optional[OrderOptions]:
        """Каталог для формы заказа."""
        if self.check_fields(creds):
            return None

        # Получаем типы (sizes)
        sizes_data, err = await self._get(creds, "/linode/types")
        if err or not sizes_data:
            return None

        # Получаем регионы
        regions_data, _ = await self._get(creds, "/regions")

        # Получаем образы
        images_data, _ = await self._get(creds, "/images")

        plans = []
        sizes = sizes_data.get("data", [])
        if isinstance(sizes, list):
            for size in sizes:
                if not isinstance(size, dict) or not size.get("available"):
                    continue

                plan_id = str(size.get("id", ""))
                if not plan_id:
                    continue

                price = size.get("price", {}).get("monthly")
                try:
                    price = float(price) if price else None
                except (TypeError, ValueError):
                    price = None

                specs = size.get("specs", {})
                specs_str = f"CPU {specs.get('vcpus', 1)} · RAM {specs.get('memory', 0)} MB · Disk {specs.get('disk', 0)} MB"

                plans.append(OrderPlan(
                    id=plan_id,
                    name=str(size.get("label", plan_id)),
                    specs=specs_str,
                    price=price,
                    currency="USD",
                    period="month",
                    region="",
                ))

        regions = []
        if isinstance(regions_data, dict):
            region_list = regions_data.get("data", [])
            if isinstance(region_list, list):
                for reg in region_list:
                    if isinstance(reg, dict) and reg.get("available"):
                        regions.append({
                            "id": str(reg.get("id", "")),
                            "name": str(reg.get("label", reg.get("id", ""))),
                        })

        images = []
        if isinstance(images_data, dict):
            image_list = images_data.get("data", [])
            if isinstance(image_list, list):
                for img in image_list:
                    if isinstance(img, dict):
                        images.append({
                            "id": str(img.get("id", "")),
                            "name": str(img.get("label", img.get("id", ""))),
                        })

        return OrderOptions(
            plans=plans,
            regions=regions,
            images=images,
            custom=None,  # Linode не поддерживает кастомные конфиги через API (используются предустановленные типы)
        )

    async def quote_order(self, creds: dict, spec: dict) -> Optional[dict]:
        """Linode не поддерживает предварительный расчёт (цена известна из типа)."""
        # На практике цена берётся из plan.price
        return None

    async def create_order(self, creds: dict, spec: dict) -> dict:
        """Создать новый Linode (сервер)."""
        fail = {
            "ok": False,
            "id": "",
            "name": "",
            "price": None,
            "currency": "USD",
        }

        # Валидация полей
        plan_id = str((spec or {}).get("plan_id", "")).strip()
        region = str((spec or {}).get("region", "")).strip()
        image = str((spec or {}).get("image", "")).strip()
        name = str((spec or {}).get("name", "")).strip()

        if not all([plan_id, region, image, name]):
            return {
                **fail,
                "error": "Требуются: план, регион, образ и имя",
            }

        body = {
            "type": plan_id,
            "region": region,
            "image": image,
            "label": name,
            "root_pass": "will-be-auto-generated",
        }

        data, err = await self._post(creds, "/linode/instances", body)
        if err:
            return {**fail, "error": err}

        if not isinstance(data, dict):
            return {
                **fail,
                "error": "Linode не вернул данные инстанса",
            }

        instance_id = data.get("id")
        if not instance_id:
            return {
                **fail,
                "error": "Linode принял заказ, но не вернул ID инстанса",
            }

        try:
            price = float(data.get("specs", {}).get("price", {}).get("monthly") or 0)
        except (TypeError, ValueError):
            price = None

        return {
            "ok": True,
            "id": str(instance_id),
            "name": name,
            "price": price,
            "currency": "USD",
            "error": "",
        }


ADAPTER = LinodeAdapter()
