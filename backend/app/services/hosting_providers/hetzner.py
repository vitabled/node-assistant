"""Hetzner Cloud — список серверов через API v1.

База `https://api.hetzner.cloud/v1`, заголовок `Authorization: Bearer <token>`
(токен создаётся в консоли внутри ПРОЕКТА: Security → API tokens, и он
project-scoped — сервера другого проекта по нему не видны).

⚠️ **Баланса в Cloud API нет.** Счета и остаток живут в Hetzner Console/Robot и
через этот API не отдаются, поэтому `balance()` честно возвращает `None`, а
`CAPS` не заявляет `balance` — в карточке провайдера остаётся ручной ввод.
Выдумывать несуществующую ручку хуже, чем показать «баланс вручную».

Заказ (`order`): типы серверов фиксированные (`custom = None`), создание —
`POST /v1/servers`, ответ **201** с `{server, action, root_password}`. Пароль
root приходит только когда у проекта нет SSH-ключей; наружу мы его НЕ отдаём —
в контракте заказа поля для секрета нет, а логировать его тем более нельзя.
"""
from __future__ import annotations

from typing import Any, Optional

from app.services.hosting_providers.base import (
    CredField, OrderOptions, OrderPlan, ProviderAdapter, ServiceItem,
    map_http_error, redact,
)

_BASE = "https://api.hetzner.cloud/v1"


def _num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _reason(resp: Any, fallback: str, *secrets: str) -> str:
    """Причина отказа словами Hetzner: у него ошибка вложена в `error.message`
    («resource_unavailable», «server type not available in location»)."""
    try:
        msg = str(((resp.json() or {}).get("error") or {}).get("message") or "").strip()
    except Exception:
        msg = ""
    return redact(f"{fallback}: {msg}" if msg else fallback, *secrets)


class HetznerAdapter(ProviderAdapter):
    KIND = "hetzner"
    TITLE = "Hetzner Cloud"
    FIELDS = [CredField("token", "API-токен проекта", "password")]
    CAPS = {"services", "order"}

    async def _get(self, creds: dict, path: str) -> tuple[Any, str]:
        token = (creds.get("token") or "").strip()
        try:
            async with self._client() as c:
                r = await c.get(f"{_BASE}{path}",
                                headers={"Authorization": f"Bearer {token}"})
        except Exception as exc:
            return None, redact(f"Hetzner недоступен: {exc}", token)
        if r.status_code >= 400:
            return None, map_http_error(r.status_code)
        try:
            return r.json(), ""
        except Exception:
            return None, "Hetzner вернул не-JSON"

    async def _post(self, creds: dict, path: str, body: dict) -> tuple[Any, str]:
        """РОВНО один POST, без ретраев: создание сервера тратит деньги."""
        token = (creds.get("token") or "").strip()
        try:
            async with self._client() as c:
                r = await c.post(f"{_BASE}{path}", json=body,
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json"})
        except Exception as exc:
            return None, redact(f"Hetzner недоступен: {exc}", token)
        if r.status_code >= 400:
            return None, _reason(r, map_http_error(r.status_code), token)
        try:
            return r.json(), ""
        except Exception:
            return None, "Hetzner вернул не-JSON"

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        _, err = await self._get(creds, "/servers?per_page=1")
        return (False, err) if err else (True, "")

    async def services(self, creds: dict) -> list[ServiceItem]:
        if self.check_fields(creds):
            return []
        data, err = await self._get(creds, "/servers?per_page=50")
        if err or not isinstance(data, dict):
            return []
        out: list[ServiceItem] = []
        for s in data.get("servers") or []:
            if not isinstance(s, dict):
                continue
            stype = s.get("server_type") or {}
            # Цена приходит списком по локациям — берём месячную брутто первой.
            price = None
            for p in stype.get("prices") or []:
                gross = ((p or {}).get("price_monthly") or {}).get("gross")
                if gross is not None:
                    try:
                        price = float(gross)
                    except (TypeError, ValueError):
                        price = None
                    break
            ipv4 = ((s.get("public_net") or {}).get("ipv4") or {}).get("ip") or ""
            out.append(ServiceItem(
                id=str(s.get("id") or ""),
                name=str(s.get("name") or s.get("id") or "server"),
                kind="vps",
                cost=price,
                currency="EUR",
                period="month",
                status=str(s.get("status") or ""),
                ip=str(ipv4),
                region=str((s.get("datacenter") or {}).get("name") or ""),
            ))
        return out

    async def order_options(self, creds: dict) -> Optional[OrderOptions]:
        if self.check_fields(creds):
            return None
        types, err = await self._get(creds, "/server_types?per_page=100")
        if err or not isinstance(types, dict):
            return None
        locations, _ = await self._get(creds, "/locations")
        images, _ = await self._get(creds, "/images?type=system&per_page=100")

        plans: list[OrderPlan] = []
        for t in (types.get("server_types") or []):
            if not isinstance(t, dict) or t.get("deprecated"):
                continue
            name = str(t.get("name") or "")
            if not name:
                continue
            # Цена у Hetzner зависит от локации (так с 2024 года), поэтому в
            # каталоге показываем минимальную — точная сумма станет известна
            # после выбора локации.
            prices = [_num(((p or {}).get("price_monthly") or {}).get("gross"))
                      for p in (t.get("prices") or [])]
            prices = [p for p in prices if p is not None]
            plans.append(OrderPlan(
                id=name,
                name=f"{name} — {t.get('description') or ''}".strip(" —"),
                specs=f"{int(t.get('cores') or 0)} vCPU · {_num(t.get('memory')) or 0:g} ГБ RAM"
                      f" · {int(t.get('disk') or 0)} ГБ · {t.get('architecture') or ''}".strip(),
                price=min(prices) if prices else None,
                currency="EUR",
                period="month",
            ))

        return OrderOptions(
            plans=plans,
            regions=[{"id": str(l.get("name") or ""),
                      "name": f"{l.get('city') or ''} ({l.get('country') or ''})".strip()}
                     for l in ((locations or {}).get("locations") or [])
                     if isinstance(l, dict) and l.get("name")],
            # `id` образа — имя («ubuntu-22.04»): POST принимает и его, и число,
            # а имя переживает пересборку образа вендором.
            images=[{"id": str(i.get("name") or i.get("id") or ""),
                     "name": str(i.get("description") or i.get("name") or "")}
                    for i in ((images or {}).get("images") or [])
                    if isinstance(i, dict) and (i.get("name") or i.get("id"))],
            custom=None,  # только фиксированные server_type
        )

    async def create_order(self, creds: dict, spec: dict) -> dict:
        fail = {"ok": False, "id": "", "name": "", "price": None, "currency": "EUR"}
        missing = self.check_fields(creds)
        if missing:
            return {**fail, "error": missing}

        spec = spec or {}
        body = {
            "name": str(spec.get("name") or "").strip(),
            "server_type": str(spec.get("plan_id") or "").strip(),
            "location": str(spec.get("region") or "").strip(),
            "image": str(spec.get("image") or "").strip(),
        }
        empty = [k for k, v in body.items() if not v]
        if empty:
            return {**fail, "error": "не заполнено: " + ", ".join(empty)}

        data, err = await self._post(creds, "/servers", body)
        if err:
            return {**fail, "error": err}
        server = (data or {}).get("server") if isinstance(data, dict) else None
        if not isinstance(server, dict) or not server.get("id"):
            return {**fail, "error": "Hetzner принял запрос, но не вернул сервер "
                                     "— проверьте консоль перед повторной попыткой"}
        # `root_password` из ответа сознательно НЕ возвращаем: в контракте заказа
        # секретов нет, а положить пароль в JSON карточки значит записать его в
        # localStorage и в лог.
        stype = server.get("server_type") or {}
        prices = [_num(((p or {}).get("price_monthly") or {}).get("gross"))
                  for p in (stype.get("prices") or [])]
        prices = [p for p in prices if p is not None]
        return {
            "ok": True,
            "id": str(server.get("id")),
            "name": str(server.get("name") or body["name"]),
            "price": min(prices) if prices else None,
            "currency": "EUR",
            "error": "",
        }


ADAPTER = HetznerAdapter()
