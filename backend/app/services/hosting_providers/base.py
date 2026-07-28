"""Contract shared by every hosting-provider adapter (Wave-9 Plan C, Ф1).

Depth of the integration is «balance + service list» (+ payments where the vendor
API already exposes them), so the surface is deliberately tiny: four coroutines
and four class attributes that let the frontend build the credential form itself.

Two rules hold for ALL adapters and are the reason this module exists:

- **Nothing raises.** A dead vendor, a 500, an HTML error page, a renamed field —
  each degrades to `(False, msg)` / `None` / `[]`. The caller (a route or the
  background sync loop) must never need a try/except around an adapter, and one
  broken provider must never abort a multi-provider sync.
- **Nothing echoes credentials.** Several vendors authenticate by login+password
  in the QUERY STRING (Beget), so an httpx error string can carry the secret
  verbatim *and* percent-encoded. `redact()` strips both forms; every adapter
  routes error text through it before returning or logging.
"""
from __future__ import annotations

import abc
import urllib.parse
from dataclasses import dataclass, field
from typing import Literal, Optional

import httpx

_TIMEOUT = 20

# Adapters advertise their capabilities so the UI can tell «no API balance, enter
# it by hand» from «sync failed» (VK/Procloud/Oracle have no balance endpoint).
# «order» is advertised ONLY by adapters that really create a server through the
# public API — a button that silently does nothing is worse than no button.
CAPS_ALL = {"balance", "services", "payments", "order"}


@dataclass
class CredField:
    """One input of the vendor's credential form, rendered by the frontend."""

    key: str
    label: str
    kind: Literal["text", "password", "textarea"] = "text"
    required: bool = True


@dataclass
class Balance:
    amount: float
    currency: str


@dataclass
class ServiceItem:
    """A billable unit at the provider (VPS, cluster, …) — a LIVE listing, not
    persisted: the local `services` table stays the user's own bookkeeping."""

    id: str
    name: str
    kind: str
    cost: Optional[float]
    currency: str
    period: str
    status: str
    ip: str = ""
    region: str = ""
    paid_till: str = ""


@dataclass
class OrderPlan:
    """Одна позиция каталога заказа.

    `price` — Optional, потому что «тариф» у разных вендоров значит разное: у DO
    и Hetzner это готовая конфигурация с ценой, а у RuVDS — ПРАЙС-ЛИСТ (цена за
    ядро и за гигабайт), и итоговой суммы у него нет до сборки конфигурации."""

    id: str
    name: str
    specs: str = ""
    price: Optional[float] = None
    currency: str = ""
    period: str = "month"
    region: str = ""


@dataclass
class OrderOptions:
    """Каталог для формы заказа.

    `regions` и `images` — списки словарей вида `{"id": …, "name": …}`; адаптер
    вправе доложить свои ключи (например минимальные требования образа), фронт
    читает эти два.

    `custom` — описание КОНСТРУКТОРА:
    `{"cpu": {"min","max","step"}, "ram_gb": {…}, "disk_gb": {…}}` либо None,
    если у провайдера только фиксированные размеры. `max: None` внутри значит
    «вендор не публикует верхнюю границу», а не «безлимит»: последнее слово
    всё равно за валидацией провайдера."""

    plans: list[OrderPlan] = field(default_factory=list)
    regions: list[dict] = field(default_factory=list)
    images: list[dict] = field(default_factory=list)
    custom: Optional[dict] = None


def _client() -> httpx.AsyncClient:
    """The one client factory for all adapters.

    `follow_redirects=False`: a vendor 3xx is reported, never chased to another
    host — the same rule as `nodeflow_client`, and it keeps credentials
    (Basic/bearer/query) from being replayed to a redirect target."""
    return httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False)


def redact(text: str, *secrets: str) -> str:
    """Mask `secrets` in a string bound for an error message or the log.

    Also masks the percent-encoded form: adapters that pass credentials as query
    parameters get them back URL-quoted inside `httpx` error strings, so the raw
    `replace` alone would leak `p@ss` as `p%40ss`."""
    out = text or ""
    for secret in secrets:
        if not secret:
            continue
        out = out.replace(secret, "«redacted»")
        quoted = urllib.parse.quote(secret, safe="")
        if quoted != secret:
            out = out.replace(quoted, "«redacted»")
    return out


def map_http_error(status: int) -> str:
    """HTTP status → a phrase we can show the user as-is."""
    if status in (401, 403):
        return "неверные креды"
    if status == 404:
        return "ручка API не найдена (провайдер изменил API?)"
    if status == 429:
        return "превышен лимит запросов, попробуйте позже"
    if status >= 500:
        return "сервис недоступен"
    if status >= 400:
        return f"провайдер отклонил запрос (HTTP {status})"
    return ""


class ProviderAdapter(abc.ABC):
    """Base for every adapter. Only `verify` is abstract; the three data methods
    default to «this vendor doesn't expose it», which is the honest answer for a
    provider whose `CAPS` omits them (Beget has no payments API at all)."""

    KIND: str = ""
    TITLE: str = ""
    FIELDS: list[CredField] = []
    CAPS: set[str] = set()

    def _client(self) -> httpx.AsyncClient:
        # Indirection on purpose: tests swap this per-adapter for an
        # httpx.MockTransport client, so no adapter ever needs a live call.
        return _client()

    def check_fields(self, creds: dict) -> str:
        """"" when every required field is filled, else a Russian complaint."""
        missing = [f.label for f in self.FIELDS
                   if f.required and not str((creds or {}).get(f.key) or "").strip()]
        if missing:
            return "не заполнено: " + ", ".join(missing)
        return ""

    @abc.abstractmethod
    async def verify(self, creds: dict) -> tuple[bool, str]:
        """(ok, human-readable error). Never raises, never echoes a credential."""

    async def balance(self, creds: dict) -> Optional[Balance]:
        return None

    async def services(self, creds: dict) -> list[ServiceItem]:
        return []

    async def payments(self, creds: dict) -> list[dict]:
        return []

    async def order_options(self, creds: dict) -> Optional[OrderOptions]:
        """Каталог для формы заказа, либо None — «этот вендор заказ не умеет».

        Дефолт None, поэтому адаптеры без заказа менять не нужно."""
        return None

    async def quote_order(self, creds: dict, spec: dict) -> Optional[dict]:
        """Стоимость КОНКРЕТНОЙ конфигурации, ничего не создавая: `{"price", "currency"}`.

        Нужен там, где «тариф» — это прайс-лист, а не готовая позиция с ценой
        (RuVDS: цена считается от ядер/памяти/диска). Без такого расчёта маршрут
        покупки обязан отказать: подтвердить цену, которой никто не назвал, нельзя.
        Дефолт None — «вендор предварительный расчёт не умеет»."""
        return None

    async def create_order(self, creds: dict, spec: dict) -> dict:
        """⚠️ ТРАТИТ ДЕНЬГИ ПОЛЬЗОВАТЕЛЯ и создаёт реальный сервер.

        `spec` = `{"plan_id", "region", "image", "name", "cpu", "ram_gb",
        "disk_gb", "period"}` (лишние ключи адаптер вправе читать, отсутствующие
        — подставить по умолчанию).

        Возврат: `{"ok": bool, "id": str, "name": str, "price": float|None,
        "currency": str, "error": str}`.

        Правило реализации, обязательное для всех: **ровно один создающий
        запрос, никаких ретраев**. Таймаут или разорванное соединение НЕ значат,
        что сервер не создан — повтор рискует оплатить второй сервер, поэтому
        неопределённость возвращается пользователю текстом, а не «чинится»
        повтором."""
        return {"ok": False, "error": "Провайдер не поддерживает заказ"}
