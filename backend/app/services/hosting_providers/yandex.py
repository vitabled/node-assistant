"""Yandex Cloud adapter — billing balance + compute instances (Wave-9 Plan C, Ф4).

Authorized by a service-account key: IAM exchanges a **signed JWT** for a
short-lived IAM token, and that token authorizes the billing/compute calls. The
JWT is assembled here by hand (base64url header + payload + signature) because
`PS256` needs nothing beyond `cryptography`, which is already a dependency for
Fernet — a new pip package for one three-line signature isn't worth it.

Quirks, each of which costs an afternoon if missed:

- **`alg` must be `PS256`** — the only algorithm IAM accepts for SA keys, i.e.
  RSA-PSS with MGF1(SHA-256) and a salt as long as the digest. Signing RS256
  (`PKCS1v15`) fails with a bare 401 that reads like «wrong key».
- **`kid` is the KEY id, `iss` the SERVICE-ACCOUNT id** — two similar-looking ids
  that are easy to swap; `aud` is the token endpoint itself and `exp ≤ iat+3600`.
- **The IAM token lives up to 12 h**, so it is cached in memory for 55 min per
  service account: the exchange is a signed round-trip we don't want on every
  dashboard poll or background sync tick.
- **`balance` is a STRING** in `billingAccounts` (`"1234.56"`) and the currency is
  one of RUB/USD/KZT — coerced through `float()` in a `try`, never assumed RUB.
- **A key exported by `yc iam key create` is JSON**, so its PEM arrives with
  literal `\\n` sequences; they are un-escaped before parsing, otherwise every
  paste straight from the CLI fails to load.
- **`folderId` is mandatory** for the instance list and there is no «list all
  folders of the SA» shortcut, so without the optional field the service list is
  honestly empty instead of a guess.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any, Optional

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.services.hosting_providers.base import (
    Balance,
    CredField,
    ProviderAdapter,
    ServiceItem,
    map_http_error,
    redact,
)

log = logging.getLogger("hosting.yandex")

_IAM_URL = "https://iam.api.cloud.yandex.net/iam/v1/tokens"
_BILLING_URL = "https://billing.api.cloud.yandex.net/billing/v1/billingAccounts"
_COMPUTE_URL = "https://compute.api.cloud.yandex.net/compute/v1/instances"

_JWT_TTL = 3600          # IAM rejects anything longer
_IAM_TTL = 55 * 60       # token is valid up to 12 h; refresh well inside the hour

# service_account_id → (iam token, expires_at). Module-level on purpose: the
# dashboard poll and the background sync loop share one process and one cache.
_IAM_CACHE: dict[str, tuple[str, float]] = {}


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _load_key(pem: str) -> rsa.RSAPrivateKey:
    text = (pem or "").strip()
    # `yc iam key create` emits the PEM inside JSON, i.e. with escaped newlines.
    if "\\n" in text and "\n" not in text:
        text = text.replace("\\n", "\n")
    key = serialization.load_pem_private_key(text.encode(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError("нужен RSA-ключ сервисного аккаунта")
    return key


def build_jwt(creds: dict, now: Optional[int] = None) -> str:
    """Signed PS256 JWT for the IAM exchange. Pure — unit-tested against the
    public half of the key, which is what catches a wrong padding/digest."""
    iat = int(now if now is not None else time.time())
    key = _load_key(str((creds or {}).get("private_key") or ""))
    header = {"alg": "PS256", "typ": "JWT", "kid": str(creds.get("key_id") or "").strip()}
    payload = {
        "iss": str(creds.get("service_account_id") or "").strip(),
        "aud": _IAM_URL,
        "iat": iat,
        "exp": iat + _JWT_TTL,
    }
    signing_input = "{}.{}".format(
        _b64u(json.dumps(header, separators=(",", ":")).encode()),
        _b64u(json.dumps(payload, separators=(",", ":")).encode()),
    )
    signature = key.sign(
        signing_input.encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=hashes.SHA256.digest_size),
        hashes.SHA256(),
    )
    return f"{signing_input}.{_b64u(signature)}"


class YandexAdapter(ProviderAdapter):
    KIND = "yandex"
    TITLE = "Yandex Cloud"
    FIELDS = [
        CredField("service_account_id", "ID сервисного аккаунта"),
        CredField("key_id", "ID ключа"),
        CredField("private_key", "Приватный ключ (PEM)", "textarea"),
        CredField("folder_id", "ID каталога (для списка ВМ)", "text", required=False),
    ]
    CAPS = {"balance", "services"}

    async def _iam_token(self, creds: dict) -> tuple[str, str]:
        """Cached IAM token → (token, error)."""
        sa_id = str((creds or {}).get("service_account_id") or "").strip()
        pem = str((creds or {}).get("private_key") or "")
        cached = _IAM_CACHE.get(sa_id)
        if cached and cached[1] > time.time():
            return cached[0], ""

        try:
            jwt = build_jwt(creds)
        except Exception as exc:  # unreadable/encrypted PEM, wrong key type
            return "", "не удалось прочитать приватный ключ: " + redact(str(exc), pem)

        try:
            async with self._client() as c:
                r = await c.post(_IAM_URL, json={"jwt": jwt})
        except httpx.HTTPError as exc:
            return "", f"Yandex IAM недоступен: {redact(str(exc), pem, jwt)}"

        if r.status_code >= 400:
            return "", map_http_error(r.status_code)
        try:
            token = str((r.json() or {}).get("iamToken") or "")
        except ValueError:
            return "", "Yandex IAM вернул не-JSON ответ"
        if not token:
            return "", "Yandex IAM не вернул токен"
        _IAM_CACHE[sa_id] = (token, time.time() + _IAM_TTL)
        return token, ""

    async def _get(self, creds: dict, url: str,
                   params: Optional[dict] = None) -> tuple[Any, str]:
        token, err = await self._iam_token(creds)
        if err:
            return None, err
        try:
            async with self._client() as c:
                r = await c.get(url, params=params,
                                headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as exc:
            return None, f"Yandex Cloud недоступен: {redact(str(exc), token)}"

        if r.status_code >= 400:
            return None, map_http_error(r.status_code)
        try:
            return r.json(), ""
        except ValueError:
            return None, "Yandex Cloud вернул не-JSON ответ"

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        # The IAM exchange IS the credential check; per-service permissions are
        # reported by the balance/services calls themselves.
        _token, err = await self._iam_token(creds)
        return (False, err) if err else (True, "")

    async def balance(self, creds: dict) -> Optional[Balance]:
        if self.check_fields(creds):
            return None
        data, err = await self._get(creds, _BILLING_URL)
        if err or not isinstance(data, dict):
            return None
        accounts = [a for a in (data.get("billingAccounts") or []) if isinstance(a, dict)]
        if not accounts:
            return None
        # First active account; a suspended one still has a balance worth showing,
        # so it is the fallback rather than a `None`.
        account = next((a for a in accounts if a.get("active")), accounts[0])
        try:
            amount = float(str(account["balance"]).strip())
        except (KeyError, TypeError, ValueError):
            log.warning("yandex: unexpected billingAccounts shape")
            return None
        return Balance(amount, str(account.get("currency") or "RUB").upper())

    async def services(self, creds: dict) -> list[ServiceItem]:
        if self.check_fields(creds):
            return []
        folder = str((creds or {}).get("folder_id") or "").strip()
        if not folder:
            return []
        data, err = await self._get(creds, _COMPUTE_URL, {"folderId": folder})
        if err or not isinstance(data, dict):
            return []
        return [_instance_item(raw) for raw in (data.get("instances") or [])
                if isinstance(raw, dict)]


def _instance_ip(raw: dict) -> str:
    """Public (one-to-one NAT) address if the VM has one, else the internal one."""
    for nic in raw.get("networkInterfaces") or []:
        if not isinstance(nic, dict):
            continue
        primary = nic.get("primaryV4Address")
        if not isinstance(primary, dict):
            continue
        nat = primary.get("oneToOneNat")
        if isinstance(nat, dict) and nat.get("address"):
            return str(nat["address"])
        if primary.get("address"):
            return str(primary["address"])
    return ""


def _instance_item(raw: dict) -> ServiceItem:
    iid = str(raw.get("id") or "")
    return ServiceItem(
        id=iid,
        name=str(raw.get("name") or "").strip() or f"VM {iid}",
        kind="vm",
        cost=None,
        # Pay-as-you-go: there is no per-VM price in the compute API, and the
        # account currency may be RUB/USD/KZT — left empty rather than guessed.
        currency="",
        period="hour",
        status=str(raw.get("status") or ""),
        ip=_instance_ip(raw),
        region=str(raw.get("zoneId") or ""),
        paid_till="",
    )


ADAPTER = YandexAdapter()
ADAPTERS = [ADAPTER]
