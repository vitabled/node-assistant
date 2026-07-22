"""Turning a subscription body into «Доступность серверов» candidates (Wave-7 Plan B).

The Server-uptime tab knew two sources — manual entry and the browser's
`deploy_jobs` — but not subscriptions, which is what the Xray-uptime tab watches.
This module is the parsing half: bytes in, `{host, port, name, country}` out.

⚠️ Share links carry credentials. Nothing here logs a link, and every error is
phrased without echoing the input — the same rule `test_tools.parse_xray_link`
already follows.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Any, Optional
from urllib.parse import unquote

from app.services.test_tools import parse_xray_link

_SCHEMES = ("vless://", "vmess://", "trojan://", "ss://")


def _b64_maybe(text: str) -> Optional[str]:
    """Decode a base64 subscription body, or None when it isn't one."""
    compact = re.sub(r"\s+", "", text or "")
    if not compact or len(compact) < 8:
        return None
    # A plain-text subscription starts with a scheme; don't try to decode it.
    if any(compact.lower().startswith(s.replace("://", "")) for s in ("vless", "vmess", "trojan", "ss")):
        return None
    pad = "=" * (-len(compact) % 4)
    try:
        raw = base64.b64decode(compact + pad, validate=True)
    except (binascii.Error, ValueError):
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def decode_subscription(body: str) -> list[str]:
    """Extract share links from a subscription body.

    Accepts both forms seen in the wild: base64 of a newline-separated list, and
    the plain list itself. Lines that are not links are dropped silently — a
    subscription may carry comments or blank lines.
    """
    text = _b64_maybe(body) or (body or "")
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s.lower().startswith(_SCHEMES):
            out.append(s)
    return out


def _link_name(link: str) -> str:
    """The human label a link carries: the URI fragment, or vmess's `ps` field."""
    if link.lower().startswith("vmess://"):
        payload = link[8:].strip()
        pad = "=" * (-len(payload) % 4)
        try:
            data = json.loads(base64.b64decode(payload + pad).decode("utf-8"))
        except Exception:
            return ""
        return str(data.get("ps") or "")[:120]
    _, _, frag = link.partition("#")
    return unquote(frag).strip()[:120] if frag else ""


def _address_of(outbound: dict[str, Any]) -> tuple[str, int]:
    settings = outbound.get("settings") or {}
    for key in ("vnext", "servers"):
        arr = settings.get(key)
        if isinstance(arr, list) and arr:
            first = arr[0] or {}
            host = str(first.get("address") or "").strip()
            port = int(first.get("port") or 0)
            if host and port:
                return host, port
    raise ValueError("В ссылке нет адреса сервера")


def link_to_candidate(link: str) -> Optional[dict[str, Any]]:
    """`{host, port, name, country}` for one share link, or None when it can't be
    parsed. Never raises and never includes the link in any message."""
    try:
        cfg = parse_xray_link(link)
        host, port = _address_of((cfg.get("outbounds") or [{}])[0])
    except Exception:
        return None
    name = _link_name(link)
    return {"host": host, "port": port, "name": name or host, "country": country_of(name)}


# ── country guessing (mirrors the frontend's resolveCountryCode) ──
# Kept deliberately small: the label is free-form, and a wrong guess is worse
# than an empty country (the operator can set it afterwards).
_RU_COUNTRY = {
    "нидерланды": "NL", "голландия": "NL", "германия": "DE", "франция": "FR",
    "финляндия": "FI", "швеция": "SE", "норвегия": "NO", "дания": "DK",
    "польша": "PL", "чехия": "CZ", "австрия": "AT", "швейцария": "CH",
    "испания": "ES", "италия": "IT", "португалия": "PT", "великобритания": "GB",
    "англия": "GB", "ирландия": "IE", "сша": "US", "америка": "US",
    "канада": "CA", "япония": "JP", "сингапур": "SG", "гонконг": "HK",
    "турция": "TR", "россия": "RU", "казахстан": "KZ", "украина": "UA",
    "латвия": "LV", "литва": "LT", "эстония": "EE", "молдова": "MD",
    "румыния": "RO", "болгария": "BG", "сербия": "RS", "венгрия": "HU",
    "индия": "IN", "бразилия": "BR", "австралия": "AU", "оаэ": "AE",
    "эмираты": "AE", "израиль": "IL", "юар": "ZA", "корея": "KR",
}
_RI_LO, _RI_HI = 0x1F1E6, 0x1F1FF


def country_of(label: str) -> str:
    """alpha-2 from a free-form node label: embedded flag emoji → 2-letter token
    → Russian country name. "" when nothing matches."""
    s = (label or "").strip()
    if not s:
        return ""
    chars = list(s)
    for i in range(len(chars) - 1):
        a, b = ord(chars[i]), ord(chars[i + 1])
        if _RI_LO <= a <= _RI_HI and _RI_LO <= b <= _RI_HI:
            return chr(a - _RI_LO + 65) + chr(b - _RI_LO + 65)
    low = s.lower().replace("ё", "е")
    for token in re.split(r"[^A-Za-z]+", s):
        if len(token) == 2 and token.isalpha() and token.upper() in set(_RU_COUNTRY.values()):
            return token.upper()
    for name, code in _RU_COUNTRY.items():
        if name in low:
            return code
    return ""
