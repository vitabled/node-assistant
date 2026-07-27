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
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.services import net_guard
from app.services.hosting_providers.base import (
    CredField,
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
    CAPS = {"services"}

    async def _token(self, creds: dict) -> tuple[str, str, str, str]:
        """Authenticate → (token, nova_url, region, error)."""
        base = _auth_base(str((creds or {}).get("auth_url") or ""))
        password = str((creds or {}).get("password") or "")
        if not net_guard.is_safe_url(base):
            return "", "", "", _UNSAFE
        try:
            async with self._client() as c:
                r = await c.post(f"{base}/v3/auth/tokens", json=auth_body(creds))
        except httpx.HTTPError as exc:
            return "", "", "", f"Keystone недоступен: {redact(str(exc), password)}"

        if r.status_code >= 400:
            return "", "", "", map_http_error(r.status_code)
        token = r.headers.get("X-Subject-Token", "")
        if not token:
            return "", "", "", "Keystone не вернул токен (X-Subject-Token)"
        try:
            body = r.json()
        except ValueError:
            return "", "", "", "Keystone вернул не-JSON ответ"
        catalog = (body or {}).get("token", {}).get("catalog") if isinstance(body, dict) else None
        nova, region = find_endpoint(catalog)
        return token, nova, region, ""

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
