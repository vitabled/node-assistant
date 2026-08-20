"""
Thin async client for the BEDOLAGA (remnawave-bedolaga-telegram-bot) Web API.

Auth: X-API-Key header (service token, created in the bot's own admin panel
→ Settings → Web API). Nothing raises — every method degrades to a typed
error dict / None so a dead or misconfigured bot never 500s the panel page
that's polling it (same "nothing raises" contract as hosting_providers/base.py).
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

_TIMEOUT = 15


class BedolagaClient:
    def __init__(self, base_url: str, token: str, auth_header: str = "X-API-Key"):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.auth_header = auth_header

    def _headers(self) -> dict:
        return {self.auth_header: self.token, "Accept": "application/json"}

    async def _get(self, path: str, params: Optional[dict] = None) -> tuple[Optional[Any], Optional[str]]:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                r = await client.get(f"{self.base_url}{path}", headers=self._headers(), params=params)
            if r.status_code == 401:
                return None, "Неверный API-ключ или он отозван"
            if r.status_code == 404:
                return None, f"Эндпоинт не найден: {path}"
            r.raise_for_status()
            return r.json(), None
        except httpx.TimeoutException:
            return None, "Таймаут запроса к webapi бедолаги"
        except httpx.HTTPStatusError as exc:
            return None, f"HTTP {exc.response.status_code}: {str(exc)[:150]}"
        except Exception as exc:  # noqa: BLE001 — contract: never raise
            return None, str(exc)[:200]

    async def _post(self, path: str, json_body: Optional[dict] = None) -> tuple[Optional[Any], Optional[str]]:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                r = await client.post(f"{self.base_url}{path}", headers=self._headers(), json=json_body or {})
            if r.status_code == 401:
                return None, "Неверный API-ключ или он отозван"
            r.raise_for_status()
            return (r.json() if r.content else {}), None
        except Exception as exc:  # noqa: BLE001
            return None, str(exc)[:200]

    async def health(self) -> tuple[Optional[dict], Optional[str]]:
        return await self._get("/health")

    async def list_tickets(self, status: Optional[str] = None, limit: int = 50, offset: int = 0):
        params: dict = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        return await self._get("/tickets", params)

    async def get_ticket(self, ticket_id: int):
        return await self._get(f"/tickets/{ticket_id}")

    async def reply_ticket(self, ticket_id: int, message: str):
        return await self._post(f"/tickets/{ticket_id}/reply", {"message": message})

    async def set_priority(self, ticket_id: int, priority: str):
        return await self._post(f"/tickets/{ticket_id}/priority", {"priority": priority})

    async def get_user(self, user_id: int):
        return await self._get(f"/users/{user_id}")

    async def list_users(self, limit: int = 20, offset: int = 0):
        return await self._get("/users", {"limit": limit, "offset": offset})

    async def get_faq(self, language: str = "ru"):
        return await self._get("/pages/faq", {"language": language})
