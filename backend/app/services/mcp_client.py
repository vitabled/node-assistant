"""Minimal MCP client so the built-in assistant can use our own MCP server (Ф2).

We already SHIP 156 tools over MCP — the whole Remnawave contract plus nine
read-only handles into this backend — but only to EXTERNAL hosts like Claude
Desktop. The in-app assistant had four hand-written tools and could not touch the
panel at all. Rather than hand-writing more (and re-describing a contract we
already vendor), the assistant becomes a client of our own server.

No SDK: the transport is Streamable HTTP, i.e. plain JSON-RPC over POST with a
session id echoed back in a header. That is a handful of lines, and pulling an
SDK for it would be the larger dependency.

⚠️ Never retried on 401/403. The MCP container answers 403 to a wrong bearer,
and a retry loop against an auth failure is how you turn a typo into an outage
(the same rule the CLIProxyAPI management client will need for its IP-ban).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx

log = logging.getLogger("mcp_client")

_PROTOCOL = "2024-11-05"
_HEADERS = {
    "Content-Type": "application/json",
    # The server may answer either way; asking for both keeps it from 406-ing.
    "Accept": "application/json, text/event-stream",
}


class McpClientError(Exception):
    pass


class McpAuthError(McpClientError):
    """401/403 — bad or missing bearer. DO NOT retry."""


def _parse(body: str) -> dict:
    """Accept a bare JSON body or a single SSE `data:` frame."""
    text = (body or "").strip()
    # An SSE frame usually leads with `event: message`, so scan for the data line
    # rather than testing only the first characters.
    if not text.startswith("{"):
        for line in text.splitlines():
            if line.startswith("data:"):
                text = line[5:].strip()
                break
    try:
        return json.loads(text)
    except ValueError:
        raise McpClientError("Некорректный ответ MCP")


class McpSession:
    """One initialize → tools/list → tools/call conversation."""

    def __init__(self, base_url: str, token: str, timeout: float = 30.0) -> None:
        self._url = base_url.rstrip("/") + "/mcp"
        self._token = token
        self._timeout = timeout
        self._session_id: Optional[str] = None
        self._next_id = 0

    def _headers(self) -> dict[str, str]:
        h = dict(_HEADERS)
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    async def _rpc(self, client: httpx.AsyncClient, method: str, params: dict) -> Any:
        self._next_id += 1
        resp = await client.post(
            self._url, headers=self._headers(),
            json={"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params},
        )
        if resp.status_code in (401, 403):
            raise McpAuthError("MCP отклонил токен")
        if resp.status_code >= 400:
            raise McpClientError(f"MCP вернул {resp.status_code}")
        sid = resp.headers.get("mcp-session-id")
        if sid:
            self._session_id = sid
        payload = _parse(resp.text)
        if "error" in payload:
            raise McpClientError(str(payload["error"].get("message", "ошибка MCP"))[:300])
        return payload.get("result")

    async def list_tools(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            await self._rpc(c, "initialize", {
                "protocolVersion": _PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "node-assistant", "version": "1"},
            })
            result = await self._rpc(c, "tools/list", {})
        return (result or {}).get("tools", [])

    async def call_tool(self, name: str, arguments: dict) -> Any:
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            await self._rpc(c, "initialize", {
                "protocolVersion": _PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "node-assistant", "version": "1"},
            })
            result = await self._rpc(c, "tools/call", {"name": name, "arguments": arguments})
        return result


# ── read-only classification ──────────────────────────────────
# The container itself can run in REMNAWAVE_READONLY mode, but that is a global
# switch owned by whoever enabled MCP. The assistant makes its own decision per
# tool, so a read-only assistant stays read-only even against a writable server.
#
# ⚠️ ALLOWLIST, not a denylist. A substring denylist of mutating verbs is a hole
# against an evolving vendored contract: the Wave-7 review found live mutating
# tools that carry no listed verb — `ip_control_drop_connections`,
# `node_plugins_execute`, `node_plugins_torrent_truncate`, `metadata_*_upsert`
# ("upsert" is not "set"). A read tool has a small, stable set of leading verbs;
# anything else is treated as a write. Default-deny.
_READ_PREFIXES = (
    "get", "list", "stats", "status", "info", "health", "resolve", "metrics",
    "count", "search", "find", "show", "fetch", "read", "describe", "view",
    "download", "export",
)


def is_read_only(tool_name: str) -> bool:
    """True only when the tool's name STARTS with a known read verb.

    Names are usually `<domain>_<verb>[_...]` (nodes_get_all) — so we test the
    verb after the first underscore too, not just the whole-name prefix."""
    n = (tool_name or "").lower().lstrip("_")
    if any(n.startswith(p) for p in _READ_PREFIXES):
        return True
    _, _, rest = n.partition("_")
    return bool(rest) and any(rest.startswith(p) for p in _READ_PREFIXES)


def internal_base_url() -> str:
    """The MCP container as seen from THIS container.

    Same shape as `xray_checker`'s DooD branch: both sit on node-assistant-net,
    so we address it by container name.

    ⚠️ Uses the container's INTERNAL port, not `McpConfig.http_port` — the latter
    is the port published on the HOST, which is not on our own loopback. Taking
    it also dragged in `_cfg()`, and with it a dependency on the account
    ContextVar that this function has no reason to need."""
    from app.services.mcp_server import CONTAINER_NAME, _CONTAINER_HTTP_PORT

    return f"http://{CONTAINER_NAME}:{_CONTAINER_HTTP_PORT}"
