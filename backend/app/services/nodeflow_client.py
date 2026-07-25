"""
NodeFlow HAProxy-panel client + Fernet vault (Wave-7 «HAPROXY» integration).

node-installer registers a per-account NodeFlow instance (base URL + PANEL_ADMIN_TOKEN)
and proxies its `/api/v1/*`. NodeFlow's admin middleware accepts
`Authorization: Bearer <PANEL_ADMIN_TOKEN>` and — for the bearer path only — SKIPS the
same-origin/cookie/CSRF checks, so a server-side proxy that injects the token drives every
panel function without a browser session.

Security:
- The admin token is Fernet-encrypted at rest (same key derivation as the MCP/cliproxy
  vaults) and never returned to the client.
- Every outbound request is SSRF-guarded (`net_guard.is_safe_url`) at register time AND per
  call (a stored URL can re-resolve to an internal IP later — DNS rebinding).
- `follow_redirects=False`: a 3xx from the panel is surfaced, never chased to a new host.
"""
from __future__ import annotations

import base64
import hashlib
from typing import Optional

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.config import settings
from app.services import net_guard

_TIMEOUT = httpx.Timeout(20.0, connect=8.0)
# Cap the panel response we buffer through the proxy (agent-release download is the only
# large payload and it is streamed by NodeFlow itself; JSON stays small).
_MAX_BODY = 32 * 1024 * 1024


# ── Fernet vault (same key derivation as mcp_server/cliproxy_server) ──────────
def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.encryption_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(enc: str) -> Optional[str]:
    if not enc:
        return None
    try:
        return _fernet().decrypt(enc.encode()).decode()
    except (InvalidToken, ValueError):
        return None


class NodeFlowError(Exception):
    """Raised when the NodeFlow panel is unreachable/unsafe or misconfigured."""


def _normalize_base(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise NodeFlowError("NodeFlow не настроен: укажите URL панели")
    return base


class NodeFlowClient:
    """Thin per-request client. Construct from an account's HaproxyConfig.

    `allow_internal` skips the SSRF guard — used ONLY for the local shared panel
    reached by its container name (`http://nodeflow-panel:8080`), which is not a
    public host but is our own trusted DooD container (mirrors how xray_checker
    exempts the local checker URL). Never set it for a remote, account-supplied URL."""

    def __init__(self, base_url: str, admin_token: str, allow_internal: bool = False) -> None:
        self.base = _normalize_base(base_url)
        self.token = admin_token or ""
        self.allow_internal = allow_internal

    def _guard(self) -> None:
        if self.allow_internal:
            return
        if not net_guard.is_safe_url(self.base):
            raise NodeFlowError(
                "URL панели не разрешён: нужен http(s) с публичным (маршрутизируемым) хостом"
            )

    def _headers(self, extra: Optional[dict] = None) -> dict:
        h = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        if extra:
            h.update(extra)
        return h

    async def health(self) -> dict:
        """GET /healthz — reachability + panel version (does NOT need the token)."""
        self._guard()
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as c:
            try:
                r = await c.get(f"{self.base}/healthz")
            except httpx.HTTPError as exc:
                raise NodeFlowError(f"Панель недоступна: {exc}") from exc
        return {"status": r.status_code, "ok": r.status_code == 200,
                "body": _safe_json(r)}

    async def check(self) -> dict:
        """Health + an authenticated probe (GET /api/v1/settings) so a wrong token is
        reported as 401 rather than a false 'reachable'."""
        health = await self.health()
        auth_ok = False
        detail = ""
        try:
            resp = await self.request("GET", "settings")
            auth_ok = 200 <= resp.status_code < 300
            if not auth_ok:
                detail = f"HTTP {resp.status_code}"
        except NodeFlowError as exc:
            detail = str(exc)
        return {"reachable": health["ok"], "authenticated": auth_ok,
                "version": (health.get("body") or {}).get("version"), "detail": detail}

    async def request(
        self,
        method: str,
        subpath: str,
        *,
        params: Optional[dict] = None,
        content: Optional[bytes] = None,
        headers: Optional[dict] = None,
    ) -> httpx.Response:
        """Forward one request to `{base}/api/v1/{subpath}` with the admin bearer.
        `content` is the raw request body (JSON or multipart) — passed through verbatim
        with the caller's Content-Type so both JSON and agent-release uploads work."""
        self._guard()
        sub = (subpath or "").lstrip("/")
        url = f"{self.base}/api/v1/{sub}"
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False,
                                     max_redirects=0) as c:
            try:
                r = await c.request(
                    method.upper(), url, params=params,
                    content=content, headers=self._headers(headers),
                )
            except httpx.HTTPError as exc:
                raise NodeFlowError(f"Панель недоступна: {exc}") from exc
        return r


def _safe_json(resp: httpx.Response) -> Optional[dict]:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}
    except Exception:
        return None
