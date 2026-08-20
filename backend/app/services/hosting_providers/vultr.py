"""
Vultr cloud adapter — balance, servers, payments (Wave-9 Plan C, Ф1).

Vultr API v2: https://www.vultr.com/api/
Документация: https://www.vultr.com/api/

Возможности:
- balance: GET /v2/account (get field 'balance')
- services: GET /v2/instances (список всех серверов)
- payments: GET /v2/billing/history (история платежей)
- order: POST /v2/instances (создание сервера — требует конкретный регион/размер)

Особенности:
- Авторизация: Bearer token в заголовке
- Все цены в USD
- Rate limit: 40 req/min
"""
from __future__ import annotations

import logging
from typing import Optional

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

log = logging.getLogger("hosting.vultr")

_BASE = "https://api.vultr.com"


class VultrAdapter(ProviderAdapter):
    KIND = "vultr"
    TITLE = "Vultr"
    FIELDS = [CredField("token", "API-токен", "password")]
    CAPS = {"balance", "services", "payments"}

    async def _get(
        self, creds: dict, path: str, params: Optional[dict] = None
    ) -> tuple[Optional[dict], str]:
        """GET запрос к API Vultr."""
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
            return None, f"Vultr недоступен: {redact(str(exc), token)}"

        if r.status_code >= 400:
            return None, map_http_error(r.status_code)

        try:
            data = r.json()
        except ValueError:
            return None, "Vultr вернул не-JSON ответ"

        return (data if isinstance(data, dict) else {}), ""

    async def verify(self, creds: dict) -> tuple[bool, str]:
        """Проверить валидность токена."""
        missing = self.check_fields(creds)
        if missing:
            return False, missing

        data, err = await self._get(creds, "/v2/account")
        return (False, err) if err else (True, "")

    async def balance(self, creds: dict) -> Optional[Balance]:
        """Получить баланс аккаунта."""
        if self.check_fields(creds):
            return None

        data, err = await self._get(creds, "/v2/account")
        if err or not data:
            return None

        try:
            balance_amount = float(data.get("balance", 0))
            # Vultr всегда в USD
            return Balance(balance_amount, "USD")
        except (KeyError, TypeError, ValueError):
            log.warning("vultr: unexpected /v2/account shape")
            return None

    async def services(self, creds: dict) -> list[ServiceItem]:
        """Получить список всех серверов."""
        if self.check_fields(creds):
            return []

        data, err = await self._get(creds, "/v2/instances")
        if err or not data:
            return []

        out = []
        instances = data.get("instances", [])
        if not isinstance(instances, list):
            return []

        for inst in instances:
            if not isinstance(inst, dict):
                continue

            # Цена за месяц (на основе почасовой цены * 730)
            monthly_cost = inst.get("monthly_cost")
            try:
                cost = float(monthly_cost) if monthly_cost else None
            except (TypeError, ValueError):
                cost = None

            out.append(ServiceItem(
                id=str(inst.get("id", "")),
                name=str(inst.get("label", "") or f"Instance {inst.get('id', '')}"),
                kind="vps",
                cost=cost,
                currency="USD",
                period="month",
                status=str(inst.get("status", "active")),
                ip=str(inst.get("main_ip", "")),
                region=str(inst.get("region", "")),
                paid_till="",  # Vultr не возвращает expiry для активных серверов
            ))

        return out

    async def payments(self, creds: dict) -> list[dict]:
        """Получить историю платежей."""
        if self.check_fields(creds):
            return []

        data, err = await self._get(creds, "/v2/billing/history")
        if err or not data:
            return []

        out = []
        history = data.get("billing_history", [])
        if not isinstance(history, list):
            return []

        for item in history:
            if not isinstance(item, dict):
                continue

            try:
                amount = float(item.get("amount", 0))
            except (TypeError, ValueError):
                amount = 0.0

            # Vultr использует timestamp в формате Unix
            ts = item.get("date")
            if isinstance(ts, str):
                try:
                    ts = int(ts)
                except (TypeError, ValueError):
                    ts = 0

            out.append({
                "ts": ts,
                "amount": amount,
                "currency": "USD",
                "type": "charge",
                "note": str(item.get("description", "")),
            })

        return out


ADAPTER = VultrAdapter()
