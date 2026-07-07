"""SSRF guard for account-supplied outbound URLs (remote checker instances).

Mirrors `subs-aggregator/app.py`'s `_host_is_public`/`_safe_fetch`: allow only
http(s) and only PUBLIC (routable) hosts — blocks loopback / link-local
(IMDS 169.254.169.254) / private / reserved ranges, so an authenticated account
can't make the backend fetch internal services or cloud metadata. Applied both at
instance registration AND at every fetch (a stored URL can re-resolve to an
internal IP later — DNS rebinding).
"""
from __future__ import annotations

import ipaddress
import os
import socket
import urllib.parse

# Test-only escape hatch (same name/semantics as subs-aggregator). NEVER in prod.
_ALLOW_PRIVATE = os.getenv("ALLOW_PRIVATE_HOSTS", "").strip() == "1"


def host_is_public(host: str) -> bool:
    """Resolve `host` and require EVERY resolved IP to be public/routable."""
    if _ALLOW_PRIVATE:
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def is_safe_url(url: str) -> bool:
    """True iff `url` is http(s) with a resolvable, public host."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname or ""
    return bool(host) and host_is_public(host)


def assert_safe_url(url: str) -> None:
    if not is_safe_url(url):
        raise ValueError("URL не разрешён: нужен http(s) с публичным (маршрутизируемым) хостом")
