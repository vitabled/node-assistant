"""
Resolves and caches the backend's own external IPv4 address.
Used to whitelist the deploy panel in UFW / Fail2Ban / iptables on target servers.
"""
from __future__ import annotations
import asyncio
import logging

import httpx

_cached_ip: str = ""
_lock = asyncio.Lock()
_log = logging.getLogger(__name__)

_SERVICES = [
    "https://api.ipify.org",
    "https://checkip.amazonaws.com",
    "https://icanhazip.com",
]


async def get_backend_ip() -> str:
    """Return backend's external IPv4, cached after first successful call.
    Returns empty string on failure — callers must handle this gracefully."""
    global _cached_ip
    if _cached_ip:
        return _cached_ip
    async with _lock:
        if _cached_ip:
            return _cached_ip
        for url in _SERVICES:
            try:
                async with httpx.AsyncClient(timeout=5) as c:
                    r = await c.get(url)
                    ip = r.text.strip()
                    if ip:
                        _cached_ip = ip
                        _log.info("Backend external IP resolved: %s", ip)
                        return ip
            except Exception:
                continue
        _log.warning("Could not resolve backend external IP from any service")
        return ""
