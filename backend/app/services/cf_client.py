"""
Cloudflare API client + Fernet vault (Wave-9 Plan B «Cloudflare»: billing + domains).

The account's API token is Fernet-encrypted at rest (same key derivation as the
MCP/haproxy/auto-backup vaults) and never returned to the client — only `has_token`.

Differences from `services/nodeflow_client.py` (the other "connected external
service" client):
- **No SSRF guard.** The host is the fixed `api.cloudflare.com`, hard-coded here;
  there is no user-supplied base URL that could re-resolve to an internal address,
  which is the only thing `net_guard` protects against.
- Cloudflare wraps every payload in `{result, success, errors[], messages[]}`, so
  `_req` unwraps it and turns `success: false` (or an HTTP error) into `CfError`.

Every error string that leaves this module goes through `_redact`: a token can end
up echoed in an upstream message, and `CfError.detail` is surfaced to the UI.

⚠️ Separate from `services/cloudflare.py` (the DNS A-record helper the deploy
pipeline uses with the DNS-edit token) — that module stays as is.
"""
from __future__ import annotations

import base64
import hashlib
from typing import Any, Optional

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

CF_BASE = "https://api.cloudflare.com/client/v4"

_TIMEOUT = httpx.Timeout(20.0, connect=8.0)


# ── Fernet vault (shared key = SHA-256 of encryption_key) ─────────────────────
def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.encryption_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_token(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(enc: str) -> Optional[str]:
    if not enc:
        return None
    try:
        return _fernet().decrypt(enc.encode()).decode()
    except (InvalidToken, ValueError):
        return None


def _redact(text: str, token: str) -> str:
    """Cut the API token out of any text (mirrors `telegram.redact`). Applied to
    every message that becomes a `CfError.detail` — those reach the UI."""
    out = text or ""
    if token:
        out = out.replace(token, "«redacted»")
    return out


class CfError(Exception):
    """Cloudflare refused or is unreachable. `detail` is already token-redacted and
    safe to surface; `status` is the upstream HTTP status (403/404 drive the
    `degraded` list in the billing summary)."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _error_detail(data: dict, status: int) -> str:
    errors = data.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict) and first.get("message"):
            return f"Cloudflare: {first['message']}"
    return f"Cloudflare вернул ошибку (HTTP {status})"


class CfClient:
    """Thin per-request client. Construct from an account's CloudflareConfig token."""

    def __init__(self, token: str) -> None:
        self.token = (token or "").strip()

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def _unwrap(self, resp: httpx.Response) -> Any:
        """Return `result` from the Cloudflare envelope; raise CfError otherwise."""
        try:
            data = resp.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise CfError(
                resp.status_code if resp.status_code >= 400 else 502,
                f"Некорректный ответ Cloudflare (HTTP {resp.status_code})",
            )
        if resp.status_code >= 400 or not data.get("success"):
            status = resp.status_code if resp.status_code >= 400 else 502
            raise CfError(status, _redact(_error_detail(data, resp.status_code), self.token))
        return data.get("result")

    async def _req(
        self,
        method: str,
        path: str,
        *,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> Any:
        url = f"{CF_BASE}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as c:
            try:
                resp = await c.request(
                    method.upper(), url, json=json, params=params, headers=self._headers()
                )
            except httpx.HTTPError as exc:
                raise CfError(
                    502, _redact(f"Cloudflare недоступен: {exc}", self.token)
                ) from exc
        return self._unwrap(resp)

    # ── accounts / billing ───────────────────────────────────────────────────
    async def accounts(self) -> list[dict]:
        return await self._req("GET", "accounts") or []

    async def billing_profile(self, acc: str) -> dict:
        return await self._req("GET", f"accounts/{acc}/billing/profile") or {}

    async def paygo_info(self, acc: str) -> dict:
        return await self._req("GET", f"accounts/{acc}/paygo-usage-info") or {}

    async def paygo_usage(
        self, acc: str, start: Optional[str] = None, end: Optional[str] = None
    ) -> Any:
        params = {k: v for k, v in (("start", start), ("end", end)) if v}
        return await self._req(
            "GET", f"accounts/{acc}/paygo-usage", params=params or None
        )

    async def subscriptions(self, acc: str) -> list[dict]:
        return await self._req("GET", f"accounts/{acc}/subscriptions") or []

    async def zones(self) -> list[dict]:
        return await self._req("GET", "zones") or []

    # ── registrar ────────────────────────────────────────────────────────────
    async def registrar_domains(self, acc: str) -> list[dict]:
        return await self._req("GET", f"accounts/{acc}/registrar/domains") or []

    async def registrations(self, acc: str) -> list[dict]:
        return await self._req("GET", f"accounts/{acc}/registrar/registrations") or []

    async def registration(self, acc: str, name: str) -> dict:
        return await self._req(
            "GET", f"accounts/{acc}/registrar/registrations/{name}"
        ) or {}

    async def patch_registration(self, acc: str, name: str, **fields: Any) -> dict:
        return await self._req(
            "PATCH", f"accounts/{acc}/registrar/registrations/{name}", json=fields
        ) or {}

    async def domain_search(self, acc: str, q: str) -> Any:
        return await self._req(
            "GET", f"accounts/{acc}/registrar/domain-search", params={"query": q}
        )

    async def domain_check(self, acc: str, names: list[str]) -> Any:
        return await self._req(
            "POST",
            f"accounts/{acc}/registrar/domain-check",
            json={"domain_names": names},
        )

    async def register(self, acc: str, payload: dict) -> Any:
        """POST a registration — **billable**. The purchase gates live in the API
        layer (confirm + price match + payment-method preflight), not here."""
        return await self._req(
            "POST", f"accounts/{acc}/registrar/registrations", json=payload
        )
