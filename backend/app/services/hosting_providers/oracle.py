"""Oracle Cloud (OCI) adapter — instances + monthly spend (Wave-9 Plan C, Ф5).

OCI authenticates every request with an **HTTP signature** (draft-cavage), which
is why this is the heaviest adapter: there is no token to fetch, each call is
signed with the API key of the user. `cryptography` does the RSA part, so the
official `oci` SDK (dozens of transitive packages for two endpoints) stays out.

What the signature must look like, and why each detail matters:

- The signing string is the header list, **in the advertised order**, one
  `name: value` per line joined by `\\n`. For a GET that is
  `(request-target)`, `date`, `host`; a POST appends `x-content-sha256`,
  `content-type`, `content-length` — in exactly that order. OCI rebuilds the
  string from the `headers="…"` field, so the order in the string and in that
  field are one decision, made in one function here.
- `(request-target)` is `<lowercase method> <path>[?query]` — the query is part of
  it, so the URL is assembled once and both signed and sent verbatim (re-encoding
  it later would break the signature).
- `keyId` is `{tenancy}/{user}/{fingerprint}`, algorithm `rsa-sha256`
  (`PKCS1v15` + SHA-256 — NOT the PSS padding Yandex needs).
- ⚠️ **A clock skew over 5 minutes gets a 401**, indistinguishable from a bad key.
  If verify fails on a freshly pasted key, check the host clock first.

No balance: OCI is post-paid with credits and has no «account balance» endpoint,
so `CAPS` advertises the monthly spend via `payments()` instead and infra-billing
keeps the manual amount.
"""
from __future__ import annotations

import base64
import email.utils
import hashlib
import json
import logging
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.services.hosting_providers.base import (
    CredField,
    ProviderAdapter,
    ServiceItem,
    map_http_error,
    redact,
)

log = logging.getLogger("hosting.oracle")

_IAAS_TPL = "https://iaas.{region}.oraclecloud.com/20160918/instances"
_USAGE_TPL = "https://usageapi.{region}.oci.oraclecloud.com/20200107/usage"

# The region is interpolated into a HOSTNAME, so it is validated as a strict
# slug — otherwise a crafted value could redirect the signed request elsewhere.
_REGION_RE = re.compile(r"[a-z0-9-]{2,40}")

_JSON = "application/json"


def _load_key(pem: str) -> rsa.RSAPrivateKey:
    key = serialization.load_pem_private_key((pem or "").strip().encode(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError("нужен RSA-ключ API")
    return key


def signing_string(method: str, url: str, date: str, body: Optional[bytes] = None,
                   content_type: str = _JSON) -> tuple[str, list[str]]:
    """(string to sign, header names) — built together so they cannot drift."""
    parsed = urllib.parse.urlparse(url)
    target = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    names = ["(request-target)", "date", "host"]
    lines = [f"(request-target): {method.lower()} {target}",
             f"date: {date}",
             f"host: {parsed.netloc}"]
    if body is not None:
        digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
        names += ["x-content-sha256", "content-type", "content-length"]
        lines += [f"x-content-sha256: {digest}",
                  f"content-type: {content_type}",
                  f"content-length: {len(body)}"]
    return "\n".join(lines), names


def sign_headers(creds: dict, method: str, url: str,
                 body: Optional[bytes] = None) -> dict[str, str]:
    """Signed request headers. Raises ValueError/TypeError on an unusable key —
    the caller turns that into a message, never into a traceback."""
    date = email.utils.formatdate(usegmt=True)
    string, names = signing_string(method, url, date, body)
    key = _load_key(str((creds or {}).get("private_key") or ""))
    signature = base64.b64encode(
        key.sign(string.encode(), padding.PKCS1v15(), hashes.SHA256())
    ).decode()
    key_id = "{}/{}/{}".format(
        str(creds.get("tenancy_ocid") or "").strip(),
        str(creds.get("user_ocid") or "").strip(),
        str(creds.get("fingerprint") or "").strip(),
    )
    headers = {
        "date": date,
        "accept": _JSON,
        "authorization": (
            'Signature version="1",keyId="{}",algorithm="rsa-sha256",'
            'headers="{}",signature="{}"'.format(key_id, " ".join(names), signature)
        ),
    }
    if body is not None:
        headers["x-content-sha256"] = base64.b64encode(
            hashlib.sha256(body).digest()).decode()
        headers["content-type"] = _JSON
        headers["content-length"] = str(len(body))
    return headers


def month_range(now: Optional[datetime] = None) -> tuple[str, str]:
    """[start of this month, start of next month) as UTC midnight timestamps —
    OCI refuses MONTHLY granularity for anything off a month boundary."""
    now = now or datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (start + timedelta(days=32)).replace(day=1)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return start.strftime(fmt), end.strftime(fmt)


class OracleAdapter(ProviderAdapter):
    KIND = "oracle"
    TITLE = "Oracle Cloud (OCI)"
    FIELDS = [
        CredField("tenancy_ocid", "OCID тенанта"),
        CredField("user_ocid", "OCID пользователя"),
        CredField("fingerprint", "Отпечаток ключа"),
        CredField("private_key", "Приватный ключ (PEM)", "textarea"),
        CredField("region", "Регион (например eu-frankfurt-1)"),
        CredField("compartment_id", "OCID компартмента (по умолчанию — тенант)",
                  "text", required=False),
    ]
    # No "balance": OCI is post-paid, the spend is reported through payments().
    CAPS = {"services", "payments"}

    async def _request(self, creds: dict, method: str, url: str,
                       body: Optional[bytes] = None) -> tuple[Any, str]:
        pem = str((creds or {}).get("private_key") or "")
        try:
            headers = sign_headers(creds, method, url, body)
        except Exception as exc:  # unreadable/encrypted PEM, wrong key type
            return None, "не удалось прочитать приватный ключ: " + redact(str(exc), pem)

        try:
            async with self._client() as c:
                r = await c.request(method.upper(), url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            return None, f"Oracle Cloud недоступен: {redact(str(exc), pem)}"

        if r.status_code >= 400:
            return None, map_http_error(r.status_code)
        try:
            return r.json(), ""
        except ValueError:
            return None, "Oracle Cloud вернул не-JSON ответ"

    def _region(self, creds: dict) -> str:
        region = str((creds or {}).get("region") or "").strip().lower()
        return region if _REGION_RE.fullmatch(region) else ""

    def _instances_url(self, creds: dict) -> str:
        region = self._region(creds)
        if not region:
            return ""
        compartment = (str((creds or {}).get("compartment_id") or "").strip()
                       or str((creds or {}).get("tenancy_ocid") or "").strip())
        query = urllib.parse.urlencode({"compartmentId": compartment})
        return f"{_IAAS_TPL.format(region=region)}?{query}"

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        url = self._instances_url(creds)
        if not url:
            return False, "регион указан неверно"
        _data, err = await self._request(creds, "GET", url)
        return (False, err) if err else (True, "")

    async def services(self, creds: dict) -> list[ServiceItem]:
        if self.check_fields(creds):
            return []
        url = self._instances_url(creds)
        if not url:
            return []
        data, err = await self._request(creds, "GET", url)
        if err:
            return []
        rows = data.get("items") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            log.warning("oracle: unexpected /instances shape")
            return []
        return [_instance_item(raw) for raw in rows if isinstance(raw, dict)]

    async def payments(self, creds: dict) -> list[dict]:
        """Monthly spend as ONE record — OCI has no ledger of payments, only a
        usage aggregation, and summing it is the closest honest equivalent."""
        if self.check_fields(creds):
            return []
        region = self._region(creds)
        tenancy = str((creds or {}).get("tenancy_ocid") or "").strip()
        if not region or not tenancy:
            return []
        start, end = month_range()
        body = json.dumps({
            "tenantId": tenancy,
            "timeUsageStarted": start,
            "timeUsageEnded": end,
            "granularity": "MONTHLY",
        }).encode()
        data, err = await self._request(creds, "POST",
                                        _USAGE_TPL.format(region=region), body)
        if err or not isinstance(data, dict):
            return []
        items = data.get("items")
        if not isinstance(items, list):
            aggregation = data.get("usageAggregation")
            items = aggregation.get("items") if isinstance(aggregation, dict) else None
        if not isinstance(items, list):
            return []

        total = 0.0
        currency = ""
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                total += float(item.get("computedAmount") or 0)
            except (TypeError, ValueError):
                continue
            currency = currency or str(item.get("currency") or "")
        if not items:
            return []
        return [{
            "ts": start,
            "amount": round(total, 2),
            "currency": currency.upper(),
            "type": "charge",
            "note": "расход за месяц",
        }]


def _instance_item(raw: dict) -> ServiceItem:
    iid = str(raw.get("id") or "")
    return ServiceItem(
        id=iid,
        name=str(raw.get("displayName") or "").strip() or f"instance {iid[-8:]}",
        kind=str(raw.get("shape") or "instance"),
        cost=None,
        # Hourly post-paid; per-instance price is not in the compute API.
        currency="",
        period="hour",
        status=str(raw.get("lifecycleState") or ""),
        # The address needs a separate VNIC-attachment lookup per instance
        # (two extra signed requests each) — not worth it for a list view.
        ip="",
        region=str(raw.get("availabilityDomain") or raw.get("region") or ""),
        paid_till="",
    )


ADAPTER = OracleAdapter()
ADAPTERS = [ADAPTER]
