"""Beget adapter — account balance only (Wave-9 Plan C, Ф2).

`GET https://api.beget.com/api/user/getAccountInfo?login=&passwd=&output_format=json`

Two things make this adapter unusual:

- **The credentials are the control-panel login and password**, not an API token.
  The UI must say so (amber notice) — a leak here is the whole hosting account.
  Beget wants them as QUERY parameters, so the URL itself is a secret: nothing
  here logs a URL, and every returned string goes through `redact()` (which also
  masks the percent-encoded form the query string produces).
- **The envelope is DOUBLE**: `{status, answer: {status, result: {...}}}`. Either
  level can carry `status: "error"` with an error text, and the vendor spells the
  key differently per level (`error_text` outer, `errortext` inner) — both
  spellings are accepted so a rename on one level doesn't read as success.

Services are NOT exposed: `getAccountInfo` describes the shared-hosting plan, and
VPS live in a different API module (`/api/vps/getList`) whose exact contract we
have not verified — a separate `kind` when someone needs it. Payments: no API.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.services.hosting_providers.base import (
    Balance,
    CredField,
    ProviderAdapter,
    map_http_error,
    redact,
)

log = logging.getLogger("hosting.beget")

_URL = "https://api.beget.com/api/user/getAccountInfo"


def _envelope_error(node: Any) -> str:
    """Error text of one envelope level, "" if that level looks fine."""
    if not isinstance(node, dict):
        return "неожиданный формат ответа Beget"
    if str(node.get("status") or "").lower() != "error":
        return ""
    for key in ("errortext", "error_text", "errorcode", "error_code"):
        text = str(node.get(key) or "").strip()
        if text:
            return text
    return "Beget вернул ошибку без описания"


class BegetAdapter(ProviderAdapter):
    KIND = "beget"
    TITLE = "Beget"
    FIELDS = [
        CredField("login", "Логин личного кабинета"),
        CredField("password", "Пароль личного кабинета", "password"),
    ]
    CAPS = {"balance"}

    async def _account_info(self, creds: dict) -> tuple[Optional[dict], str]:
        """→ (`answer.result`, error). Unwraps both envelope levels."""
        login = str((creds or {}).get("login") or "").strip()
        password = str((creds or {}).get("password") or "")
        try:
            async with self._client() as c:
                r = await c.get(_URL, params={
                    "login": login, "passwd": password, "output_format": "json",
                })
        except httpx.HTTPError as exc:
            return None, f"Beget недоступен: {redact(str(exc), login, password)}"

        if r.status_code >= 400:
            return None, map_http_error(r.status_code)
        try:
            data = r.json()
        except ValueError:
            return None, "Beget вернул не-JSON ответ"

        err = _envelope_error(data)
        if err:
            return None, redact(err, login, password)
        answer = data.get("answer")
        err = _envelope_error(answer)
        if err:
            return None, redact(err, login, password)
        result = answer.get("result")
        if not isinstance(result, dict):
            return None, "неожиданный формат ответа Beget"
        return result, ""

    async def verify(self, creds: dict) -> tuple[bool, str]:
        missing = self.check_fields(creds)
        if missing:
            return False, missing
        _result, err = await self._account_info(creds)
        return (False, err) if err else (True, "")

    async def balance(self, creds: dict) -> Optional[Balance]:
        if self.check_fields(creds):
            return None
        result, err = await self._account_info(creds)
        if err or not result:
            return None
        try:
            return Balance(float(result["user_balance"]), "RUB")
        except (KeyError, TypeError, ValueError):
            log.warning("beget: unexpected getAccountInfo result shape")
            return None


ADAPTER = BegetAdapter()
