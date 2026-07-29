"""OpenStack adapter — VK Cloud, Procloud and any Keystone v3 cloud (Ф4).

One `kind` for every OpenStack deployment: the vendors differ only by `auth_url`,
so `PRESETS` carries the known defaults for the form and everything else is the
plain protocol.

Keystone/Nova specifics that shape this module:

- **The token is a HEADER, not a body field.** `POST {auth_url}/v3/auth/tokens`
  answers `201` with the token in `X-Subject-Token`; the JSON body holds only the
  catalog. Reading `body["token"]` looks plausible and yields a dict, never a
  token.
- **Service endpoints come from the catalog**, not from a template: VK Cloud's
  Nova lives on a different host and port than Keystone
  (`infra.mail.ru:8774/v2.1`), and a private cloud is anybody's guess. We look up
  `type == "compute"` + `interface == "public"`.
- **`auth_url` is user-supplied**, hence `net_guard.is_safe_url` at verify AND
  before every request — a stored URL can re-resolve to an internal address later
  (DNS rebinding), the same rule `nodeflow_client` follows. The catalog-derived
  Nova URL is guarded too: it is chosen by whoever answers `auth_url`, so an
  unguarded hop would hand back the SSRF pivot the first guard denied.
- **openrc files spell `auth_url` with `/v3` already included**, so a trailing
  `/v3` is stripped before the path is appended — otherwise the request goes to
  `/v3/v3/auth/tokens` and 404s.

No balance: neither VK Cloud nor Procloud publishes a billing API, so the amount
stays a manual entry in infra-billing and `CAPS` says so honestly.

Заказ (`order`) — тоже чистый протокол, но с тремя оговорками, каждая из которых
меняет поведение формы:

- **⚠️ У flavor'а НЕТ цены.** Nova описывает размер (`vcpus`/`ram`/`disk`), а не
  стоимость: тарификация живёт в биллинге вендора, которого в API нет. Поэтому у
  каждого плана `price=None`, а `quote_order` не реализован — выдумать сумму
  значило бы обойти подтверждение цены. Маршрут покупки такой заказ поддерживает
  через отдельную галочку «сумма заранее неизвестна».
- **⚠️ Сеть выбирается вместо региона.** Nova отказывает («Multiple possible
  networks found»), если у проекта больше одной сети, а `networks` не передан.
  Региона же в `POST /servers` нет вовсе — регион задаётся тем, ЧЕЙ endpoint
  compute мы взяли из каталога. Поэтому единственный свободный селектор формы
  (`regions`) заполняется СЕТЯМИ Neutron с явными именами («Сеть …»), а адаптер
  читает выбранное как `spec["network"]` либо, если его нет, как `spec["region"]`
  — форма присылает выбор именно вторым ключом. Neutron недоступен → список
  пустой (выдумывать сети нечем), и тогда `networks` просто не отправляется:
  проект с ровно одной сетью Nova разберёт сам, а остальным откажет своими
  словами.
- **Каталог образов — Glance из service catalog** (`type == "image"`), а не
  устаревший проксирующий `GET {nova}/images`. Путь пробуется как `/v2/images`,
  затем `/images`: часть каталогов публикует Glance уже с `/v2` в URL, и тогда
  первый вариант даёт 404 на `/v2/v2/images`.

`adminPass` из ответа на создание наружу НЕ отдаётся — в контракте заказа поля
для секрета нет (тот же запрет, что на `root_password` у Hetzner).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.services import net_guard
from app.services.hosting_providers.base import (
    CredField,
    OrderOptions,
    OrderPlan,
    ProviderAdapter,
    ServiceItem,
    map_http_error,
    redact,
)

log = logging.getLogger("hosting.openstack")

# UI defaults only — every value stays editable in the credential form.
# VK Cloud's are taken from its openrc (user domain is literally `users`);
# the Procloud entry is the vendor's Horizon host on the standard Keystone port
# and has NOT been verified against a live account.
PRESETS: dict[str, dict[str, str]] = {
    "vkcloud": {"title": "VK Cloud", "auth_url": "https://infra.mail.ru:35357",
                "domain": "users"},
    "procloud": {"title": "Procloud", "auth_url": "https://cloud.procloud.ru:5000",
                 "domain": "Default"},
}

_UNSAFE = "адрес недопустим: нужен http(s) с публичным (маршрутизируемым) хостом"


def _auth_base(auth_url: str) -> str:
    base = (auth_url or "").strip().rstrip("/")
    if base.lower().endswith("/v3"):
        base = base[:-3].rstrip("/")
    return base


def auth_body(creds: dict) -> dict:
    """Keystone v3 password-auth payload, scoped to the project. Pure."""
    username = str((creds or {}).get("username") or "").strip()
    password = str((creds or {}).get("password") or "")
    project_id = str((creds or {}).get("project_id") or "").strip()
    domain = str((creds or {}).get("domain") or "").strip()
    return {
        "auth": {
            "identity": {
                "methods": ["password"],
                "password": {"user": {"name": username, "password": password,
                                      "domain": {"name": domain}}},
            },
            "scope": {"project": {"id": project_id, "domain": {"name": domain}}},
        }
    }


def _num(raw: Any) -> Optional[float]:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def nova_reason(body: Any, fallback: str) -> str:
    """Причина отказа словами Nova.

    Ошибка приходит как `{"badRequest": {"message": …}}` / `{"forbidden": {…}}` /
    `{"itemNotFound": {…}}` — имя ключа зависит от кода, поэтому берём сообщение
    из первого вложенного объекта. Для заказа это не украшательство: «нет квоты»
    и «flavor не найден» лечатся по-разному."""
    if isinstance(body, dict):
        for value in body.values():
            if isinstance(value, dict) and str(value.get("message") or "").strip():
                return f"{fallback}: {str(value['message']).strip()}"
    return fallback


def find_endpoint(catalog: Any, service_type: str = "compute") -> tuple[str, str]:
    """(url, region) of the public endpoint of `service_type` in a token catalog."""
    if not isinstance(catalog, list):
        return "", ""
    for entry in catalog:
        if not isinstance(entry, dict) or entry.get("type") != service_type:
            continue
        for ep in entry.get("endpoints") or []:
            if isinstance(ep, dict) and ep.get("interface") == "public":
                return str(ep.get("url") or ""), str(ep.get("region") or
                                                     ep.get("region_id") or "")
    return "", ""


class OpenStackAdapter(ProviderAdapter):
    KIND = "openstack"
    TITLE = "OpenStack (VK Cloud, Procloud)"
    FIELDS = [
        CredField("auth_url", "Keystone auth URL"),
        CredField("username", "Пользователь"),
        CredField("password", "Пароль", "password"),
        CredField("project_id", "ID проекта"),
        CredField("domain", "Домен"),
    ]
    # No "balance": no public billing API at VK Cloud / Procloud (see docstring).
    CAPS = {"services", "order"}

    async def _auth(self, creds: dict) -> tuple[str, Any, str]:
        """Authenticate → (token, service catalog, error).

        Отдельно от `_token`, потому что заказу нужен ВЕСЬ каталог (Nova + Glance
        + Neutron), а не только compute-endpoint."""
        base = _auth_base(str((creds or {}).get("auth_url") or ""))
        password = str((creds or {}).get("password") or "")
        if not net_guard.is_safe_url(base):
            return "", None, _UNSAFE
        try:
            async with self._client() as c:
                r = await c.post(f"{base}/v3/auth/tokens", json=auth_body(creds))
        except httpx.HTTPError as exc:
            return "", None, f"Keystone недоступен: {redact(str(exc), password)}"

        if r.status_code >= 400:
            return "", None, map_http_error(r.status_code)
        token = r.headers.get("X-Subject-Token", "")
        if not token:
            return "", None, "Keystone не вернул токен (X-Subject-Token)"
        try:
            body = r.json()
        except ValueError:
            return "", None, "Keystone вернул не-JSON ответ"
        catalog = (body or {}).get("token", {}).get("catalog") if isinstance(body, dict) else None
        return token, catalog, ""

    async def _token(self, creds: dict) -> tuple[str, str, str, str]:
        """Authenticate → (token, nova_url, region, error)."""
        token, catalog, err = await self._auth(creds)
        if err:
            return "", "", "", err
        nova, region = find_endpoint(catalog)
        return token, nova, region, ""

    async def _api_get(self, token: str, url: str) -> tuple[Any, str]:
        """GET одного сервиса OpenStack по токену.

        URL приходит из каталога, то есть его выбирает тот, кто ответил на
        `auth_url` — без гарда это был бы SSRF-разворот через первый отказ."""
        if not net_guard.is_safe_url(url):
            return None, _UNSAFE
        try:
            async with self._client() as c:
                r = await c.get(url, headers={"X-Auth-Token": token})
        except httpx.HTTPError as exc:
            return None, f"OpenStack недоступен: {redact(str(exc), token)}"
        if r.status_code >= 400:
            return None, map_http_error(r.status_code)
        try:
            return r.json(), ""
        except ValueError:
            return None, "OpenStack вернул не-JSON ответ"

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        _token, _nova, _region, err = await self._token(creds)
        return (False, err) if err else (True, "")

    async def services(self, creds: dict) -> list[ServiceItem]:
        if self.check_fields(creds):
            return []
        token, nova, region, err = await self._token(creds)
        if err or not token:
            return []
        if not nova:
            log.warning("openstack: no public compute endpoint in the catalog")
            return []
        if not net_guard.is_safe_url(nova):
            log.warning("openstack: compute endpoint from the catalog is not public")
            return []
        try:
            async with self._client() as c:
                r = await c.get(f"{nova.rstrip('/')}/servers/detail",
                                headers={"X-Auth-Token": token})
        except httpx.HTTPError as exc:
            log.warning("openstack: nova unreachable: %s", redact(str(exc), token))
            return []
        if r.status_code >= 400:
            return []
        try:
            data = r.json()
        except ValueError:
            return []
        rows = data.get("servers") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            return []
        return [_server_item(raw, region) for raw in rows if isinstance(raw, dict)]

    async def _catalog_list(
        self, token: str, base: str, paths: tuple[str, ...], key: str
    ) -> list[dict]:
        """Список объектов сервиса, пробуя пути по очереди.

        Каталоги публикуют Glance/Neutron то с версией в URL, то без неё, поэтому
        `/v2/images` может дать `/v2/v2/images` → 404. Ошибка любого пути —
        просто пустой список: заказ должен открываться и без образов из каталога."""
        if not base:
            return []
        for path in paths:
            data, err = await self._api_get(token, f"{base.rstrip('/')}{path}")
            if err or not isinstance(data, dict):
                continue
            rows = data.get(key)
            if isinstance(rows, list):
                return [r for r in rows if isinstance(r, dict)]
        return []

    async def order_options(self, creds: dict) -> Optional[OrderOptions]:
        if self.check_fields(creds):
            return None
        token, catalog, err = await self._auth(creds)
        if err or not token:
            return None
        nova, _region = find_endpoint(catalog, "compute")
        if not nova:
            log.warning("openstack: no public compute endpoint in the catalog")
            return None
        flavors, err = await self._api_get(token, f"{nova.rstrip('/')}/flavors/detail")
        if err or not isinstance(flavors, dict):
            return None

        plans: list[OrderPlan] = []
        for f in (flavors.get("flavors") or []):
            if not isinstance(f, dict):
                continue
            fid = str(f.get("id") or "")
            if not fid or f.get("OS-FLV-DISABLED:disabled") is True:
                continue
            ram_mb = _num(f.get("ram")) or 0.0
            plans.append(OrderPlan(
                id=fid,
                name=str(f.get("name") or fid),
                # `ram` у Nova в МЕГАбайтах, `disk` — в ГБ.
                specs=f"{int(_num(f.get('vcpus')) or 0)} vCPU · {ram_mb / 1024:g} ГБ RAM"
                      f" · {int(_num(f.get('disk')) or 0)} ГБ",
                # Цены у flavor'а нет вовсе — см. модуль-докстринг.
                price=None,
                currency="",
                period="hour",
            ))

        glance, _ = find_endpoint(catalog, "image")
        images = [
            {"id": str(i.get("id") or ""),
             "name": str(i.get("name") or i.get("id") or ""),
             # Минимумы образа: flavor меньше них Nova не примет.
             "min_disk_gb": int(_num(i.get("min_disk")) or 0),
             "min_ram_gb": (_num(i.get("min_ram")) or 0.0) / 1024}
            for i in await self._catalog_list(
                token, glance, ("/v2/images", "/images"), "images")
            if i.get("id") and str(i.get("status") or "active").lower() == "active"
        ]

        neutron, _ = find_endpoint(catalog, "network")
        networks: list[dict] = []
        for n in await self._catalog_list(
                token, neutron, ("/v2.0/networks", "/networks"), "networks"):
            nid = str(n.get("id") or "")
            if not nid:
                continue
            label = str(n.get("name") or nid)
            if n.get("router:external"):
                label += " · внешняя"
            elif n.get("shared"):
                label += " · общая"
            # Имя начинается со слова «Сеть» намеренно: форма подписывает этот
            # селектор как регион, а выбирается здесь именно сеть.
            networks.append({"id": nid, "name": f"Сеть {label}"})

        return OrderOptions(
            plans=plans,
            regions=networks,
            images=images,
            # Размеры у OpenStack фиксированные (flavor), конструктора нет.
            custom=None,
        )

    async def create_order(self, creds: dict, spec: dict) -> dict:
        fail = {"ok": False, "id": "", "name": "", "price": None, "currency": ""}
        missing = self.check_fields(creds)
        if missing:
            return {**fail, "error": missing}

        spec = spec or {}
        name = str(spec.get("name") or "").strip()
        flavor = str(spec.get("plan_id") or "").strip()
        image = str(spec.get("image") or "").strip()
        empty = [label for label, value in (
            ("имя сервера", name), ("тип (flavor)", flavor), ("образ", image),
        ) if not value]
        if empty:
            return {**fail, "error": "не заполнено: " + ", ".join(empty)}
        # Форма присылает выбор сети в `region` — единственном свободном
        # селекторе; `network` принимается для прямых вызовов API.
        network = str(spec.get("network") or spec.get("region") or "").strip()

        token, catalog, err = await self._auth(creds)
        if err or not token:
            return {**fail, "error": err or "не удалось аутентифицироваться в Keystone"}
        nova, _region = find_endpoint(catalog, "compute")
        if not nova:
            return {**fail, "error": "в каталоге Keystone нет публичного compute-endpoint"}
        if not net_guard.is_safe_url(nova):
            return {**fail, "error": _UNSAFE}

        server: dict = {"name": name, "flavorRef": flavor, "imageRef": image}
        if network:
            server["networks"] = [{"uuid": network}]
        # РОВНО один POST, без ретраев: создание сервера тратит деньги, и таймаут
        # не означает «не создано».
        try:
            async with self._client() as c:
                r = await c.post(f"{nova.rstrip('/')}/servers", json={"server": server},
                                 headers={"X-Auth-Token": token,
                                          "Content-Type": "application/json"})
        except httpx.HTTPError as exc:
            return {**fail, "error": f"Nova недоступна: {redact(str(exc), token)}"}

        try:
            data = r.json()
        except ValueError:
            data = None
        if r.status_code >= 400:
            return {**fail, "error": redact(
                nova_reason(data, map_http_error(r.status_code)), token)}
        created = (data or {}).get("server") if isinstance(data, dict) else None
        if not isinstance(created, dict) or not created.get("id"):
            return {**fail, "error": "Nova приняла запрос, но не вернула сервер "
                                     "— проверьте панель перед повторной попыткой"}
        # `adminPass` из ответа наружу НЕ отдаём: в контракте заказа поля для
        # секрета нет, а карточка заказа персистится на клиенте.
        return {
            "ok": True,
            "id": str(created.get("id")),
            "name": str(created.get("name") or name),
            # Цену Nova не называет — маршрут покупки требует отдельного
            # подтверждения «сумма заранее неизвестна».
            "price": None,
            "currency": "",
            "error": "",
        }


def _server_ip(addresses: Any) -> str:
    """Floating address if the server has one, else the first fixed one."""
    if not isinstance(addresses, dict):
        return ""
    first = ""
    for entries in addresses.values():
        if not isinstance(entries, list):
            continue
        for addr in entries:
            if not isinstance(addr, dict) or not addr.get("addr"):
                continue
            if str(addr.get("OS-EXT-IPS:type") or "").lower() == "floating":
                return str(addr["addr"])
            first = first or str(addr["addr"])
    return first


def _server_item(raw: dict, region: str) -> ServiceItem:
    sid = str(raw.get("id") or "")
    flavor = raw.get("flavor") if isinstance(raw.get("flavor"), dict) else {}
    return ServiceItem(
        id=sid,
        name=str(raw.get("name") or "").strip() or f"VM {sid}",
        kind=str(flavor.get("original_name") or "vm"),
        cost=None,
        # Hourly pay-as-you-go and no price in the Nova API → no currency to state.
        currency="",
        period="hour",
        status=str(raw.get("status") or ""),
        ip=_server_ip(raw.get("addresses")),
        region=str(raw.get("OS-EXT-AZ:availability_zone") or region or ""),
        paid_till="",
    )


ADAPTER = OpenStackAdapter()
ADAPTERS = [ADAPTER]
