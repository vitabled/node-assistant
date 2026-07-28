"""DigitalOcean — полный биллинг через общий API v2.

База `https://api.digitalocean.com/v2`, заголовок `Authorization: Bearer <token>`
(токен выпускается в панели → API → Tokens; для чтения хватает scope `read`).

Из всех «старых» провайдеров каталога у DO самый полный биллинг: кроме баланса
есть расход с начала месяца и история начислений, поэтому CAPS полный.

Заказ (`order`): у DO ФИКСИРОВАННЫЕ размеры — конфигурируется не «сколько ядер»,
а какой `size` взять, поэтому `custom = None`, а вся конфигурация живёт в списке
планов. Создание — `POST /v2/droplets`, ответ **202 Accepted**: дроплет ставится
в очередь, поэтому в ответе он ещё в статусе `new` и без IP.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.services.hosting_providers.base import (
    Balance, CredField, OrderOptions, OrderPlan, ProviderAdapter, ServiceItem,
    map_http_error, redact,
)

log = logging.getLogger(__name__)

_BASE = "https://api.digitalocean.com/v2"


def _num(v: Any) -> Optional[float]:
    """DO отдаёт суммы СТРОКАМИ («12.34»), а не числами."""
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _reason(resp: Any, fallback: str, *secrets: str) -> str:
    """Причина отказа словами вендора, если он её прислал.

    Для заказа это не украшательство: «размер недоступен в регионе» и «превышен
    лимит дроплетов» лечатся по-разному, а общее «провайдер отклонил запрос»
    не подсказывает ничего."""
    try:
        msg = str((resp.json() or {}).get("message") or "").strip()
    except Exception:
        msg = ""
    return redact(f"{fallback}: {msg}" if msg else fallback, *secrets)


class DigitalOceanAdapter(ProviderAdapter):
    KIND = "digitalocean"
    TITLE = "DigitalOcean"
    FIELDS = [CredField("token", "API-токен", "password")]
    CAPS = {"balance", "services", "payments", "order"}

    async def _get(self, creds: dict, path: str) -> tuple[Any, str]:
        token = (creds.get("token") or "").strip()
        try:
            async with self._client() as c:
                r = await c.get(f"{_BASE}{path}",
                                headers={"Authorization": f"Bearer {token}"})
        except Exception as exc:
            return None, redact(f"DigitalOcean недоступен: {exc}", token)
        if r.status_code >= 400:
            return None, map_http_error(r.status_code)
        try:
            return r.json(), ""
        except Exception:
            return None, "DigitalOcean вернул не-JSON"

    async def _post(self, creds: dict, path: str, body: dict) -> tuple[Any, str]:
        """РОВНО один POST. Ретраев нет намеренно: создание дроплета тратит
        деньги, и таймаут не значит «не создано»."""
        token = (creds.get("token") or "").strip()
        try:
            async with self._client() as c:
                r = await c.post(f"{_BASE}{path}", json=body,
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json"})
        except Exception as exc:
            return None, redact(f"DigitalOcean недоступен: {exc}", token)
        if r.status_code >= 400:
            return None, _reason(r, map_http_error(r.status_code), token)
        try:
            return r.json(), ""
        except Exception:
            return None, "DigitalOcean вернул не-JSON"

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        _, err = await self._get(creds, "/customers/my/balance")
        return (False, err) if err else (True, "")

    async def balance(self, creds: dict) -> Optional[Balance]:
        if self.check_fields(creds):
            return None
        data, err = await self._get(creds, "/customers/my/balance")
        if err or not isinstance(data, dict):
            return None
        # `account_balance` — остаток (у постоплаты обычно «0.00» или кредит),
        # `month_to_date_usage` — расход с начала месяца. Показываем остаток:
        # именно он сравнивается с порогом низкого баланса.
        amount = _num(data.get("account_balance"))
        if amount is None:
            log.warning("digitalocean: в ответе нет account_balance")
            return None
        return Balance(amount, "USD")

    async def services(self, creds: dict) -> list[ServiceItem]:
        if self.check_fields(creds):
            return []
        data, err = await self._get(creds, "/droplets?per_page=200")
        if err or not isinstance(data, dict):
            return []
        out: list[ServiceItem] = []
        for d in data.get("droplets") or []:
            if not isinstance(d, dict):
                continue
            size = d.get("size") or {}
            net = ((d.get("networks") or {}).get("v4") or [])
            public = next((n.get("ip_address") for n in net
                           if isinstance(n, dict) and n.get("type") == "public"), "")
            out.append(ServiceItem(
                id=str(d.get("id") or ""),
                name=str(d.get("name") or d.get("id") or "droplet"),
                kind="vps",
                cost=_num(size.get("price_monthly")),
                currency="USD",
                period="month",
                status=str(d.get("status") or ""),
                ip=str(public or ""),
                region=str((d.get("region") or {}).get("slug") or ""),
            ))
        return out

    async def order_options(self, creds: dict) -> Optional[OrderOptions]:
        if self.check_fields(creds):
            return None
        sizes, err = await self._get(creds, "/sizes?per_page=200")
        if err or not isinstance(sizes, dict):
            return None
        regions, _ = await self._get(creds, "/regions?per_page=200")
        images, _ = await self._get(creds, "/images?type=distribution&per_page=200")

        plans: list[OrderPlan] = []
        for s in (sizes.get("sizes") or []):
            if not isinstance(s, dict) or not s.get("available", True):
                continue
            slug = str(s.get("slug") or "")
            if not slug:
                continue
            mem_mb = _num(s.get("memory")) or 0.0
            # `memory` у DO в МЕГАбайтах, а `disk`/`transfer` — в ГБ/ТБ.
            plans.append(OrderPlan(
                id=slug,
                name=f"{slug} — {s.get('description') or 'Droplet'}",
                specs=f"{int(s.get('vcpus') or 0)} vCPU · "
                      f"{mem_mb / 1024:g} ГБ RAM · {int(s.get('disk') or 0)} ГБ SSD",
                price=_num(s.get("price_monthly")),
                currency="USD",
                period="month",
                # Размер доступен не везде — список регионов едет с планом,
                # иначе форма предложит заведомо отбиваемую пару size+region.
                region=",".join(str(r) for r in (s.get("regions") or [])),
            ))

        return OrderOptions(
            plans=plans,
            regions=[{"id": str(r.get("slug") or ""), "name": str(r.get("name") or "")}
                     for r in ((regions or {}).get("regions") or [])
                     if isinstance(r, dict) and r.get("available", True)],
            images=[{"id": str(i.get("slug") or i.get("id") or ""),
                     "name": f"{i.get('distribution') or ''} {i.get('name') or ''}".strip()}
                    for i in ((images or {}).get("images") or [])
                    if isinstance(i, dict) and (i.get("slug") or i.get("id"))],
            custom=None,  # у DO нет конструктора: только готовые size-ы
        )

    async def create_order(self, creds: dict, spec: dict) -> dict:
        fail = {"ok": False, "id": "", "name": "", "price": None, "currency": "USD"}
        missing = self.check_fields(creds)
        if missing:
            return {**fail, "error": missing}

        spec = spec or {}
        body = {
            "name": str(spec.get("name") or "").strip(),
            "region": str(spec.get("region") or "").strip(),
            "size": str(spec.get("plan_id") or "").strip(),
            "image": str(spec.get("image") or "").strip(),
        }
        empty = [k for k, v in body.items() if not v]
        if empty:
            return {**fail, "error": "не заполнено: " + ", ".join(empty)}

        data, err = await self._post(creds, "/droplets", body)
        if err:
            return {**fail, "error": err}
        droplet = (data or {}).get("droplet") if isinstance(data, dict) else None
        if not isinstance(droplet, dict) or not droplet.get("id"):
            # Запрос прошёл, а дроплета в ответе нет — молчать нельзя: он мог
            # быть создан, поэтому просим человека посмотреть в панели.
            return {**fail, "error": "DigitalOcean принял запрос, но не вернул дроплет "
                                     "— проверьте панель перед повторной попыткой"}
        return {
            "ok": True,
            "id": str(droplet.get("id")),
            "name": str(droplet.get("name") or body["name"]),
            "price": _num((droplet.get("size") or {}).get("price_monthly")),
            "currency": "USD",
            "error": "",
        }

    async def payments(self, creds: dict) -> list[dict]:
        if self.check_fields(creds):
            return []
        data, err = await self._get(creds, "/customers/my/billing_history")
        if err or not isinstance(data, dict):
            return []
        out: list[dict] = []
        for row in data.get("billing_history") or []:
            if not isinstance(row, dict):
                continue
            amount = _num(row.get("amount"))
            # У DO положительная сумма — это НАЧИСЛЕНИЕ (Invoice), отрицательная —
            # платёж/кредит. Приводим к нашему словарю явно, чтобы знак не путал.
            kind = "charge" if (amount or 0) >= 0 else "topup"
            out.append({
                "ts": str(row.get("date") or ""),
                "amount": abs(amount) if amount is not None else None,
                "currency": "USD",
                "type": kind,
                "note": str(row.get("description") or row.get("type") or ""),
            })
        return out


ADAPTER = DigitalOceanAdapter()
