"""CLIProxyAPI Management API client — server-side only (Wave-7 Plan F Ф2).

Drives the headless OAuth onboarding: ask for a login URL, let the human sign in
and paste the redirect back, poll until the account lands in the pool.

⚠️ FIVE failed auths from one IP get that IP banned for 30 MINUTES. Behind a
shared network every request looks like one IP, so a retry loop against a wrong
key locks the whole gateway out. Therefore: every method issues EXACTLY ONE
request, and 401 raises a typed error that callers must not retry.

⚠️ `GET /config` returns the whole config with plaintext keys. It is deliberately
NOT wrapped here — an endpoint that does not exist cannot be accidentally piped
to a browser.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

log = logging.getLogger("cliproxy_mgmt")

_BASE_PATH = "/v0/management"

# provider alias → the endpoint that mints an OAuth URL
OAUTH_ENDPOINTS = {
    "anthropic": "/anthropic-auth-url",
    "claude": "/anthropic-auth-url",
    "codex": "/codex-auth-url",
    "openai": "/codex-auth-url",
    "xai": "/xai-auth-url",
    "grok": "/xai-auth-url",
    "kimi": "/kimi-auth-url",
    "antigravity": "/antigravity-auth-url",
}

# Gemini has no OAuth login at all — API key, Vertex import, or Antigravity
# (a Google account there yields gemini models). Surfacing this as a normal
# "unknown provider" would send the operator hunting for a button that cannot
# exist, so it gets its own message.
NO_OAUTH = {"gemini", "google", "vertex"}


class ManagementError(Exception):
    pass


class ManagementDisabled(ManagementError):
    """403/404 — remote management off, or no key configured at all."""


class ManagementAuthError(ManagementError):
    """401 — wrong key. DO NOT retry: five of these ban the IP for 30 minutes."""


class ManagementClient:
    def __init__(self, base_url: str, key: str, timeout: float = 15.0) -> None:
        if not key:
            raise ManagementError("Management-ключ не задан")
        self._base = base_url.rstrip("/") + _BASE_PATH
        self._headers = {"Authorization": f"Bearer {key}"}
        self._timeout = timeout

    async def _request(self, method: str, path: str, **kw) -> Any:
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            resp = await c.request(method, self._base + path, headers=self._headers, **kw)
        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError:
                return {}
        if resp.status_code == 401:
            # Logged without the key and NOT retried.
            log.error("cliproxy.management_auth_failed", extra={"path": path})
            raise ManagementAuthError("Management API отклонил ключ")
        if resp.status_code in (403, 404):
            raise ManagementDisabled("Management API выключен или недоступен извне")
        raise ManagementError(f"Management API вернул {resp.status_code}")

    # ── provider accounts ─────────────────────────────────────
    async def list_auth_files(self) -> list[dict]:
        data = await self._request("GET", "/auth-files")
        return (data or {}).get("files", [])

    async def delete_auth_file(self, name: str) -> None:
        await self._request("DELETE", "/auth-files", params={"name": name})

    async def set_auth_disabled(self, name: str, disabled: bool) -> dict:
        return await self._request(
            "PATCH", "/auth-files/status", json={"name": name, "disabled": disabled},
        )

    async def reset_quota(self, auth_index: str) -> dict:
        return await self._request("POST", "/reset-quota", json={"auth_index": auth_index})

    # ── headless OAuth ────────────────────────────────────────
    async def start_oauth(self, provider: str) -> dict:
        p = (provider or "").lower().strip()
        if p in NO_OAUTH:
            raise ManagementError(
                "У Gemini нет OAuth-входа — используйте Antigravity (вход Google) "
                "или API-ключ",
            )
        endpoint = OAUTH_ENDPOINTS.get(p)
        if endpoint is None:
            raise ManagementError(f"Нет OAuth-входа для провайдера {provider!r}")
        # NOTE: no `is_webui=1` — that spawns a loopback forwarder on ports we
        # neither publish nor need.
        return await self._request("GET", endpoint)

    async def finish_oauth(self, payload: dict) -> dict:
        return await self._request("POST", "/oauth-callback", json=payload)

    async def oauth_status(self, state: str) -> dict:
        return await self._request("GET", "/get-auth-status", params={"state": state})


def scrub_auth_file(entry: dict) -> dict[str, Any]:
    """Only the fields the UI needs. The raw record carries filesystem paths and
    other internals that have no business in a browser."""
    return {
        "name": entry.get("name") or entry.get("id"),
        "provider": entry.get("provider") or entry.get("type"),
        "label": entry.get("label") or entry.get("email") or "",
        "status": entry.get("status"),
        "disabled": bool(entry.get("disabled")),
        "unavailable": bool(entry.get("unavailable")),
        "last_refresh": entry.get("last_refresh"),
    }
