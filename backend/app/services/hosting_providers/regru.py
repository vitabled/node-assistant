"""Reg.ru adapters — CloudVPS servers and account balance (Wave-9 Plan C, Ф3).

Reg.ru speaks TWO unrelated APIs, so this module carries TWO `kind`s (one vendor,
two vault entries — the UI hints that a full picture needs both):

- **CloudVPS** — `https://api.cloudvps.reg.ru`, `Authorization: Bearer <token>`, a
  DigitalOcean-shaped API over the VPS fleet (`GET /v1/reglets`). It has **no
  balance endpoint**: the v2 surface publishes only `/v2/images` and `/v2/plans`,
  so `balance()` stays the base's honest `None` instead of guessing a path.
- **Рег.API 2** — `https://api.reg.ru/api/regru2/...`, the billing account,
  authenticated by the control-panel username+password. Balance lives here.

Рег.API 2 quirks, each of which bites:

- **POST with form-data only.** The vendor documents «query string parameters are
  disallowed», so the credentials go in the BODY. Sending them as query params
  gets the request rejected outright — and would also put the password in every
  proxy log on the way.
- **HTTP 200 on failure.** Errors arrive as `{"result": "error", "error_code": …,
  "error_text": …}`, so the status code alone reads as success. Auth failures show
  up as `PASSWORD_AUTH_FAILED`/`NO_SUCH_USER`, never as 401 — they are mapped onto
  the same «неверные креды» phrase the HTTP path produces.
- **The balance field is not called `balance`.** `user/get_balance` answers with
  `prepay` (available funds) alongside `blocked`/`credit`, and the currency can
  come back as the legacy `RUR`. Both are looked up from a short list of plausible
  keys; an unrecognised shape yields `None` + a log warning rather than a number
  we made up.

Заказ (`order`, только CloudVPS) — квирки из документации вендора:

- **⚠️ В `POST /v1/reglets` НЕТ региона.** Документированные поля — `name`
  (необязательное, вендор сгенерирует сам), `size` (slug тарифа), `image` (slug
  образа), плюс `ssh_keys`/`backups`/`isp_license_size`. Регион неявно задаёт сам
  тариф: каталог у каждого региона свой. Поэтому `OrderOptions.regions` остаётся
  **пустым** — селектор региона, который создание игнорирует, врал бы
  пользователю; вместо этого регион едет в `OrderPlan.region` и в имени плана.
- **Каталог живёт в v2 и требует региона** (`GET /v2/plans` и `GET /v2/images` с
  обязательными `region`, `page`, `items_per_page`). Список регионов вендор
  отдельной ручкой не отдаёт — он зафиксирован в `_REGIONS` по документации.
  Регион, ответивший ошибкой, просто пропускается: каталог остальных полезен.
- **Цена — `price_per_month`**, но у части линеек тарификация почасовая
  (`unit: "hour"`), и тогда берётся `price_per_hour` с периодом `hour`. Пересчёт
  час→месяц не делается: это была бы выдуманная сумма.
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

log = logging.getLogger("hosting.regru")

_CLOUDVPS = "https://api.cloudvps.reg.ru"
_REGRU_API = "https://api.reg.ru/api/regru2"

# Регионы каталога v2 по документации вендора: отдельной ручки со списком нет.
_REGIONS = ("msk1", "openstack-msk1", "openstack-spb1", "openstack-sam1")
_PER_PAGE = 100

_AMOUNT_KEYS = ("prepay", "balance", "available", "amount")
_CURRENCY_KEYS = ("currency", "currency_code", "curr")
# Рег.API 2 never answers 401 — these codes are its way of saying «wrong login».
_AUTH_ERROR_CODES = {"PASSWORD_AUTH_FAILED", "NO_SUCH_USER"}


def _price(raw: Any) -> Optional[float]:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _public_ip(networks: Any) -> str:
    """First public IPv4 of a reglet (`networks.v4[]`), else the first address."""
    if not isinstance(networks, dict):
        return ""
    v4 = networks.get("v4")
    if not isinstance(v4, list):
        return ""
    entries = [n for n in v4 if isinstance(n, dict)]
    for net in entries:
        if str(net.get("type") or "").lower() == "public":
            return str(net.get("ip_address") or "")
    return str(entries[0].get("ip_address") or "") if entries else ""


def _plan(raw: Any, region: str) -> Optional[OrderPlan]:
    """Одна позиция `GET /v2/plans` → `OrderPlan`, либо None у мусорной записи."""
    if not isinstance(raw, dict):
        return None
    slug = str(raw.get("slug") or "").strip()
    if not slug:
        return None
    price, period = _price(raw.get("price_per_month")), "month"
    if price is None:
        # Часовая линейка: месячной суммы у неё нет, а считать её самим —
        # значит назвать цену, которой вендор не называл.
        price, period = _price(raw.get("price_per_hour")), "hour"
    return OrderPlan(
        id=slug,
        name=f"{raw.get('name') or slug} ({region})",
        specs=f"{int(_price(raw.get('vcpus')) or 0)} vCPU · "
              f"{_price(raw.get('memory')) or 0:g} ГБ RAM · "
              f"{int(_price(raw.get('disk')) or 0)} ГБ",
        price=price,
        currency="RUB",
        period=period,
        region=region,
    )


def _reglet_item(raw: dict) -> ServiceItem:
    rid = raw.get("id")
    size = raw.get("size") if isinstance(raw.get("size"), dict) else {}
    region = raw.get("region")
    if isinstance(region, dict):
        region_name = str(region.get("name") or region.get("slug") or "")
    else:
        region_name = str(region or "")
    return ServiceItem(
        id=str(rid if rid is not None else ""),
        name=str(raw.get("name") or "").strip() or f"VPS #{rid}",
        kind="vps",
        cost=_price(size.get("price_monthly", raw.get("price_monthly"))),
        currency="RUB",
        period="month",
        status=str(raw.get("status") or ""),
        ip=_public_ip(raw.get("networks")),
        region=region_name,
        paid_till="",
    )


class RegruCloudVps(ProviderAdapter):
    KIND = "regru_cloudvps"
    TITLE = "Reg.ru CloudVPS"
    FIELDS = [CredField("token", "API-токен", "password")]
    # No "balance": CloudVPS has no balance endpoint — the account balance comes
    # from the separate `regru_account` adapter below.
    CAPS = {"services", "order"}

    async def _get(self, creds: dict, path: str,
                   params: Optional[dict] = None) -> tuple[Any, str]:
        token = str((creds or {}).get("token") or "").strip()
        try:
            async with self._client() as c:
                r = await c.get(
                    f"{_CLOUDVPS}{path}",
                    params=params,
                    headers={"Authorization": f"Bearer {token}",
                             "Content-Type": "application/json"},
                )
        except httpx.HTTPError as exc:
            return None, f"Reg.ru CloudVPS недоступен: {redact(str(exc), token)}"

        if r.status_code >= 400:
            return None, map_http_error(r.status_code)
        try:
            return r.json(), ""
        except ValueError:
            return None, "Reg.ru CloudVPS вернул не-JSON ответ"

    async def _post(self, creds: dict, path: str, body: dict) -> tuple[Any, str]:
        """РОВНО один POST, без ретраев: создание сервера тратит деньги, и
        таймаут не означает «не создано»."""
        token = str((creds or {}).get("token") or "").strip()
        try:
            async with self._client() as c:
                r = await c.post(
                    f"{_CLOUDVPS}{path}", json=body,
                    headers={"Authorization": f"Bearer {token}",
                             "Content-Type": "application/json"},
                )
        except httpx.HTTPError as exc:
            return None, f"Reg.ru CloudVPS недоступен: {redact(str(exc), token)}"

        if r.status_code >= 400:
            # Слова вендора («тариф недоступен», «недостаточно средств») лечатся
            # по-разному, а общая фраза по коду не подсказывает ничего.
            try:
                payload = r.json() or {}
            except ValueError:
                payload = {}
            msg = ""
            if isinstance(payload, dict):
                msg = str(payload.get("message") or payload.get("error")
                          or payload.get("detail") or "").strip()
            base = map_http_error(r.status_code)
            return None, redact(f"{base}: {msg}" if msg else base, token)
        try:
            return r.json(), ""
        except ValueError:
            return None, "Reg.ru CloudVPS вернул не-JSON ответ"

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        _data, err = await self._get(creds, "/v1/reglets")
        return (False, err) if err else (True, "")

    async def services(self, creds: dict) -> list[ServiceItem]:
        if self.check_fields(creds):
            return []
        data, err = await self._get(creds, "/v1/reglets")
        if err:
            return []
        rows = data.get("reglets") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            log.warning("regru_cloudvps: unexpected /v1/reglets shape")
            return []
        return [_reglet_item(raw) for raw in rows if isinstance(raw, dict)]

    async def order_options(self, creds: dict) -> Optional[OrderOptions]:
        if self.check_fields(creds):
            return None
        plans: list[OrderPlan] = []
        images: list[dict] = []
        answered = False
        for region in _REGIONS:
            page = {"region": region, "page": 1, "items_per_page": _PER_PAGE}
            data, err = await self._get(creds, "/v2/plans", page)
            if err or not isinstance(data, dict):
                # Регион мог быть выведен из продажи — остальные всё равно нужны.
                continue
            answered = True
            for p in (data.get("plans") or []):
                plan = _plan(p, region)
                if plan is not None:
                    plans.append(plan)
            imgs, err = await self._get(creds, "/v2/images",
                                        dict(page, type="distribution"))
            if err or not isinstance(imgs, dict):
                continue
            for i in (imgs.get("images") or []):
                if not isinstance(i, dict) or not i.get("slug"):
                    continue
                images.append({
                    "id": str(i["slug"]),
                    "name": f"{i.get('distribution') or ''} {i.get('name') or ''}".strip()
                            or str(i["slug"]),
                    "region": str(i.get("region_slug") or region),
                    "min_disk_gb": i.get("min_disk_size"),
                })
        if not answered:
            return None
        return OrderOptions(
            plans=plans,
            # Пусто намеренно: у создания региона нет, он зашит в тариф.
            regions=[],
            images=images,
            custom=None,  # только готовые тарифы, конструктора у CloudVPS нет
        )

    async def create_order(self, creds: dict, spec: dict) -> dict:
        fail = {"ok": False, "id": "", "name": "", "price": None, "currency": "RUB"}
        missing = self.check_fields(creds)
        if missing:
            return {**fail, "error": missing}

        spec = spec or {}
        size = str(spec.get("plan_id") or "").strip()
        image = str(spec.get("image") or "").strip()
        empty = [label for label, value in (
            ("тариф", size), ("образ", image),
        ) if not value]
        if empty:
            return {**fail, "error": "не заполнено: " + ", ".join(empty)}

        body = {"size": size, "image": image}
        name = str(spec.get("name") or "").strip()
        if name:
            # Имя необязательно: без него вендор сгенерирует своё.
            body["name"] = name

        data, err = await self._post(creds, "/v1/reglets", body)
        if err:
            return {**fail, "error": err}
        reglet = (data or {}).get("reglet") if isinstance(data, dict) else None
        if not isinstance(reglet, dict) or not reglet.get("id"):
            return {**fail, "error": "Reg.ru принял запрос, но не вернул сервер "
                                     "— проверьте панель перед повторной попыткой"}
        # Из ответа берём только id и имя: пароль/ключи, если вендор их пришлёт,
        # наружу отдавать нельзя, а цену маршрут покупки уже знает из каталога.
        return {
            "ok": True,
            "id": str(reglet.get("id")),
            "name": str(reglet.get("name") or name),
            "price": None,
            "currency": "RUB",
            "error": "",
        }


class RegruAccount(ProviderAdapter):
    KIND = "regru_account"
    TITLE = "Reg.ru (аккаунт)"
    FIELDS = [
        CredField("username", "Логин Reg.ru"),
        CredField("password", "Пароль Reg.ru", "password"),
    ]
    CAPS = {"balance"}

    async def _call(self, creds: dict, method: str) -> tuple[Optional[dict], str]:
        """POST one Рег.API 2 method → (`answer`, error). Credentials go as
        form-data because the vendor disallows query-string parameters."""
        username = str((creds or {}).get("username") or "").strip()
        password = str((creds or {}).get("password") or "")
        try:
            async with self._client() as c:
                r = await c.post(f"{_REGRU_API}/{method}", data={
                    "username": username,
                    "password": password,
                    "output_content_type": "plain",
                })
        except httpx.HTTPError as exc:
            return None, f"Reg.ru недоступен: {redact(str(exc), username, password)}"

        if r.status_code >= 400:
            return None, map_http_error(r.status_code)
        try:
            data = r.json()
        except ValueError:
            return None, "Reg.ru вернул не-JSON ответ"
        if not isinstance(data, dict):
            return None, "неожиданный формат ответа Reg.ru"

        if str(data.get("result") or "").lower() != "success":
            return None, redact(_envelope_error(data), username, password)
        answer = data.get("answer")
        if not isinstance(answer, dict):
            return None, "неожиданный формат ответа Reg.ru"
        return answer, ""

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        _answer, err = await self._call(creds, "user/get_balance")
        return (False, err) if err else (True, "")

    async def balance(self, creds: dict) -> Optional[Balance]:
        if self.check_fields(creds):
            return None
        answer, err = await self._call(creds, "user/get_balance")
        if err or not answer:
            return None
        for key in _AMOUNT_KEYS:
            if key in answer:
                amount = _price(str(answer[key]).strip())
                if amount is not None:
                    return Balance(amount, _currency(answer))
        log.warning("regru_account: no recognised amount key in user/get_balance")
        return None


def _envelope_error(data: dict) -> str:
    """Error text of a `result: error` envelope (auth codes → the shared phrase)."""
    code = str(data.get("error_code") or "").strip().upper()
    if code in _AUTH_ERROR_CODES:
        return map_http_error(401)
    text = str(data.get("error_text") or "").strip()
    return text or code or "Reg.ru отклонил запрос"


def _currency(answer: dict) -> str:
    for key in _CURRENCY_KEYS:
        code = str(answer.get(key) or "").strip().upper()
        if code:
            # RUR is the pre-1998 code Reg.ru still emits in places.
            return "RUB" if code == "RUR" else code
    return "RUB"


ADAPTER = RegruCloudVps()
ADAPTER_ACCOUNT = RegruAccount()
# Two adapters in one module — a registry that scans for a single `ADAPTER`
# would silently drop the account one, so the list is the authoritative export.
ADAPTERS = [ADAPTER, ADAPTER_ACCOUNT]
