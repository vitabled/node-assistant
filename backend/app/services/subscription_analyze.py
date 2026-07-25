"""Wave-8 §7 — subscription / domain / IP → geo + ASN analysis.

Input is a subscription URL, a bare domain, or an IP. For a URL we fetch it —
SSRF-guarded, manual redirects, byte-capped — decode the share links and take
each target host.

⚠️ Panels serve DIFFERENT formats by User-Agent, so we try a CHAIN and use the
first response that decodes to share links: the DEFAULT (non-client) UA first —
it returns the standard base64 share-link list for most panels (verified on
hardsub.digital: default UA → base64 list, but a `v2rayNG` UA → 129 KB JSON
config with 0 links) — then fall through the client UAs (Happ / incy / Streisand
/ Shadowrocket) for panels that only serve a usable list to a specific client.
The base64 share-link list is the lingua franca; the default UA usually gets it.

Every host is resolved to IPv4, then per unique IP we look up:
  - actual geo + ASN  → ip-api.com (fallback ipwho.is), no API key
  - registry country  → RDAP (rdap.org/ip)
  - ASN name/website  → RDAP autnum (rdap.org/autnum), cached per ASN

External APIs are FIXED public hosts, so the lookups themselves aren't SSRF-prone;
we still require every analysed IP to be public. Share links carry credentials —
nothing here logs a link or the raw input, mirroring `subscription_import`.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
from typing import Any, Optional

import httpx

from app.services import net_guard
from app.services.subscription_import import decode_subscription, link_to_candidate

# Neutral UA for the geo/RDAP lookups only.
_API_UA = "node-assistant"

# Subscription-fetch User-Agent fallback chain (Wave-8): the default httpx UA
# (None) first — it gets the standard base64 share-link list for most panels —
# then client UAs for panels that only serve a usable list to a specific client.
# Tried in order; the first body that decodes to share links wins.
_SUB_USER_AGENTS = [
    None,                    # default httpx UA (proven for hardsub.digital)
    "Happ/1.16.0",
    "incy/1.0",
    "Streisand/1.6.0",
    "Shadowrocket/2.2.9",
]

_FETCH_TIMEOUT = 15
_MAX_SUB_BYTES = 4 * 1024 * 1024
_MAX_REDIRECTS = 5
_API_TIMEOUT = 8
_LOOKUP_LIMIT = 8         # concurrent IP lookups
_RESOLVE_TIMEOUT = 3


# ── input classification ───────────────────────────────────────
def classify_input(raw: str) -> str:
    """'url' | 'ip' | 'domain'. Empty → 'domain' (caller rejects empty first)."""
    s = (raw or "").strip()
    if s.lower().startswith(("http://", "https://")):
        return "url"
    try:
        ipaddress.ip_address(s)
        return "ip"
    except ValueError:
        return "domain"


def _ip_is_public(ip: str) -> bool:
    try:
        obj = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (obj.is_private or obj.is_loopback or obj.is_link_local
                or obj.is_reserved or obj.is_multicast or obj.is_unspecified)


# ── subscription fetch (VPN-client UA, SSRF-guarded) ───────────
async def _fetch_once(url: str, user_agent) -> str:
    """One GET (manual redirects, per-hop SSRF re-check, byte cap) with the given
    UA. `user_agent=None` → the default httpx UA."""
    headers = {"User-Agent": user_agent} if user_agent else None
    async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT, follow_redirects=False, headers=headers) as c:
        current = url
        for _hop in range(_MAX_REDIRECTS + 1):
            async with c.stream("GET", current) as r:
                if r.is_redirect:
                    loc = r.headers.get("location", "")
                    nxt = str(httpx.URL(current).join(loc)) if loc else ""
                    if not nxt or not net_guard.is_safe_url(nxt):
                        raise ValueError("Редирект подписки ведёт на недопустимый хост")
                    current = nxt
                    continue
                r.raise_for_status()
                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if len(buf) > _MAX_SUB_BYTES:
                        raise ValueError("Подписка превышает лимит размера")
                return bytes(buf).decode("utf-8", "replace")
        raise ValueError("Слишком много редиректов подписки")


async def fetch_subscription(url: str) -> str:
    """Fetch through the UA fallback chain; return the first body that decodes to
    share links, else the first successfully-fetched body. Raises if every UA
    failed to fetch."""
    if not net_guard.is_safe_url(url):
        raise ValueError("URL подписки не разрешён: нужен http(s) с публичным хостом")
    first_body: Optional[str] = None
    last_err: Optional[Exception] = None
    for ua in _SUB_USER_AGENTS:
        try:
            body = await _fetch_once(url, ua)
        except Exception as exc:      # network / redirect-to-bad-host / size — try next UA
            last_err = exc
            continue
        if first_body is None:
            first_body = body
        if decode_subscription(body):  # this UA yielded parseable share links → done
            return body
    if first_body is not None:
        return first_body              # nothing parsed, but we got a body — return it
    raise ValueError("Не удалось загрузить подписку")


async def _resolve(host: str) -> str:
    """host → IPv4 (or the host itself if it's already an IP), "" when unresolvable."""
    if _ip_is_public(host):
        return host
    try:
        loop = asyncio.get_event_loop()
        infos = await asyncio.wait_for(loop.getaddrinfo(host, None, family=2), timeout=_RESOLVE_TIMEOUT)
        return infos[0][4][0] if infos else ""
    except Exception:
        return ""


# ── external lookups ───────────────────────────────────────────
_AS_NUM_RE = re.compile(r"AS(\d+)", re.IGNORECASE)


def _parse_as_field(value: str) -> tuple[int, str]:
    """ip-api's `as` field is like 'AS49505 Selectel Network' → (49505, 'Selectel Network')."""
    s = (value or "").strip()
    m = _AS_NUM_RE.search(s)
    if not m:
        return 0, ""
    num = int(m.group(1))
    name = s[m.end():].strip() or ""
    return num, name


async def _ip_api(ip: str, client: httpx.AsyncClient) -> Optional[dict]:
    try:
        r = await client.get(
            f"http://ip-api.com/json/{ip}",
            params={"fields": "status,countryCode,city,as,asname,org,isp"},
        )
        d = r.json()
    except Exception:
        return None
    if not isinstance(d, dict) or d.get("status") != "success":
        return None
    num, name = _parse_as_field(str(d.get("as") or ""))
    return {
        "cc": str(d.get("countryCode") or ""),
        "city": str(d.get("city") or ""),
        "asn_number": num,
        "asn_name": str(d.get("asname") or name or d.get("org") or ""),
    }


async def _ipwho(ip: str, client: httpx.AsyncClient) -> Optional[dict]:
    try:
        r = await client.get(f"https://ipwho.is/{ip}")
        d = r.json()
    except Exception:
        return None
    if not isinstance(d, dict) or not d.get("success"):
        return None
    conn = d.get("connection") or {}
    return {
        "cc": str(d.get("country_code") or ""),
        "city": str(d.get("city") or ""),
        "asn_number": int(conn.get("asn") or 0),
        "asn_name": str(conn.get("org") or conn.get("isp") or ""),
    }


async def _rdap_ip_cc(ip: str, client: httpx.AsyncClient) -> str:
    try:
        r = await client.get(f"https://rdap.org/ip/{ip}")
        d = r.json()
        return str(d.get("country") or "") if isinstance(d, dict) else ""
    except Exception:
        return ""


def _website_from_rdap_autnum(d: dict) -> str:
    """Best-effort ASN website from an RDAP autnum object: a 'related'/any link
    whose href looks like a homepage, else a vCard URL from the entities."""
    for link in d.get("links") or []:
        if not isinstance(link, dict):
            continue
        href = str(link.get("href") or "")
        rel = str(link.get("rel") or "")
        if href.startswith("http") and "rdap" not in href and rel in ("related", "about", ""):
            return href
    for ent in d.get("entities") or []:
        vcard = (ent.get("vcardArray") or [None, []])[1] if isinstance(ent, dict) else []
        for field in vcard or []:
            if isinstance(field, list) and field and field[0] == "url":
                val = field[-1]
                if isinstance(val, str) and val.startswith("http"):
                    return val
    return ""


async def _rdap_autnum(asn: int, client: httpx.AsyncClient) -> tuple[str, str]:
    if asn <= 0:
        return "", ""
    try:
        r = await client.get(f"https://rdap.org/autnum/{asn}")
        d = r.json()
        if not isinstance(d, dict):
            return "", ""
        return str(d.get("name") or ""), _website_from_rdap_autnum(d)
    except Exception:
        return "", ""


async def _resolve_ip(ip: str, client: httpx.AsyncClient, autnum_cache: dict) -> dict:
    actual = await _ip_api(ip, client) or await _ipwho(ip, client) or {}
    registry_cc = await _rdap_ip_cc(ip, client)
    asn_number = int(actual.get("asn_number") or 0)
    asn_name = str(actual.get("asn_name") or "")
    website = ""
    if asn_number:
        if asn_number not in autnum_cache:
            autnum_cache[asn_number] = await _rdap_autnum(asn_number, client)
        reg_name, website = autnum_cache[asn_number]
        asn_name = asn_name or reg_name
    return {
        "ip": ip,
        "asn": {"number": asn_number, "name": asn_name, "website": website},
        "geo_actual": {"cc": actual.get("cc", ""), "city": actual.get("city", "")},
        "geo_registry": {"cc": registry_cc},
    }


# ── orchestration ──────────────────────────────────────────────
async def analyze(raw: str) -> list[dict[str, Any]]:
    """Return one row per unique target IP (host label kept for context)."""
    kind = classify_input(raw)
    hosts: list[str] = []
    if kind == "url":
        links = decode_subscription(await fetch_subscription(raw.strip()))
        for link in links:
            cand = link_to_candidate(link)
            if cand and cand.get("host"):
                hosts.append(cand["host"])
    else:
        hosts.append(raw.strip())

    # host → IPv4, dedup by IP (keep first host label seen for each IP)
    resolved = await asyncio.gather(*(_resolve(h) for h in hosts))
    ip_host: dict[str, str] = {}
    for host, ip in zip(hosts, resolved):
        if ip and _ip_is_public(ip) and ip not in ip_host:
            ip_host[ip] = host

    if not ip_host:
        return []

    sem = asyncio.Semaphore(_LOOKUP_LIMIT)
    autnum_cache: dict[int, tuple[str, str]] = {}

    async with httpx.AsyncClient(timeout=_API_TIMEOUT, headers={"User-Agent": _API_UA}) as client:
        async def one(ip: str, host: str) -> dict:
            async with sem:
                row = await _resolve_ip(ip, client, autnum_cache)
            row["host"] = host
            return row

        rows = await asyncio.gather(*(one(ip, host) for ip, host in ip_host.items()))
    return list(rows)


# ── «Добавить в хостинги» — one HostingBody per ASN ────────────
def group_to_hostings(results: list[dict]) -> list[dict]:
    """Group analysis rows by ASN (dedup) → a HostingBody dict per ASN:
    name = ASN name (or AS<number>), website = ASN site, asns = [that ASN],
    locations = unique actual (cc, city). Rows without an ASN are skipped."""
    groups: dict[int, dict] = {}
    for r in results or []:
        asn = r.get("asn") or {}
        num = int(asn.get("number") or 0)
        if num <= 0:
            continue
        g = groups.get(num)
        if g is None:
            g = groups[num] = {
                "name": str(asn.get("name") or f"AS{num}")[:120] or f"AS{num}",
                "website": str(asn.get("website") or ""),
                "asns": [{"number": num, "name": str(asn.get("name") or ""),
                          "website": str(asn.get("website") or "")}],
                "_locs": {},
            }
        ga = r.get("geo_actual") or {}
        cc = str(ga.get("cc") or "")
        city = str(ga.get("city") or "")
        if cc or city:
            g["_locs"][(cc, city)] = {"city": city, "country_code": cc[:2], "lat": 0, "lng": 0, "note": ""}

    out: list[dict] = []
    for g in groups.values():
        locs = list(g.pop("_locs").values())
        out.append({
            "name": g["name"],
            "website": g["website"],
            "notes": "",
            "features": "",
            "tags": [],
            "tariffs": [],
            "locations": locs,
            "asns": g["asns"],
            "provider_ref": None,
        })
    return out
