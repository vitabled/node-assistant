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
  - ASN + fallback geo → ip-api.com (fallback ipwho.is), no API key
  - actual geo         → TRACEROUTE last public hop, geolocated (falls back to
    the destination IP's geo when traceroute is unavailable/blackholed) — a
    non-responsive VPS shows its datacentre router's location, closer to reality
    than an IP-DB guess
  - registry country   → RDAP (rdap.org/ip) → RIPEstat rir-geo fallback (fills the
    ARIN gaps where RDAP has no top-level country)
  - ASN name/website   → RDAP autnum → PeeringDB fallback (net?asn=), cached per ASN

External APIs are FIXED public hosts, so the lookups themselves aren't SSRF-prone;
we still require every analysed IP to be public. Share links carry credentials —
nothing here logs a link or the raw input, mirroring `subscription_import`.
"""
from __future__ import annotations

import asyncio
import ipaddress
import platform
import re
import shutil
from typing import Any, Optional

import httpx

from app.services import net_guard
from app.services.subscription_import import decode_subscription, link_to_candidate

# Neutral UA for the geo/RDAP lookups only.
_API_UA = "node-assistant"

# Traceroute concurrency (item: actual geo via traceroute). Bounded so a big
# subscription can't spawn dozens of trace processes at once.
_TRACE_SEM = asyncio.Semaphore(8)
_TRACE_TIMEOUT = 20

# Subscription-fetch User-Agent fallback chain (Wave-8): the default httpx UA
# (None) first — it gets the standard base64 share-link list for most panels —
# then client UAs for panels that only serve a usable list to a specific client.
# Tried in order; the first body that decodes to share links wins.
_SUB_USER_AGENTS = [
    None,                    # default httpx UA (proven for hardsub.digital)
    "Happ/1.16.0",
    "INCY/3.3.7/android",
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


async def fetch_subscription(url: str, user_agent: str = "") -> str:
    """Fetch through the UA fallback chain; return the first body that decodes to
    share links, else the first successfully-fetched body. Raises if every UA
    failed to fetch. `user_agent` (Wave-4): фиксированный UA из селектора UI —
    цепочка не нужна, берём одно тело (расшифровываемое, если повезло)."""
    if not net_guard.is_safe_url(url):
        raise ValueError("URL подписки не разрешён: нужен http(s) с публичным хостом")
    if user_agent.strip():
        return await _fetch_once(url, user_agent.strip())
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
            params={"fields": "status,countryCode,city,as,asname,org,isp,reverse,hosting,proxy"},
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
        # Расшифровка конечного IP (Wave-4): организация/провайдер/PTR/тип.
        "net": {
            "org": str(d.get("org") or ""),
            "isp": str(d.get("isp") or ""),
            "ptr": str(d.get("reverse") or ""),
            "hosting": bool(d.get("hosting")),
            "proxy": bool(d.get("proxy")),
        },
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
        "net": {
            "org": str(conn.get("org") or ""),
            "isp": str(conn.get("isp") or ""),
            "ptr": "",
            "hosting": False,
            "proxy": False,
        },
    }


async def _rdap_get(url: str, client: httpx.AsyncClient) -> Optional[dict]:
    """RDAP GET with retries. ⚠️ rdap.org 301-redirects to the RIR RDAP server
    (rdap.db.ripe.net / rdap.arin.net / …), so `follow_redirects=True` is
    REQUIRED — without it the «Реестр» column was empty for every IP. rdap.org is
    also Cloudflare-fronted and intermittently ConnectError's → retry."""
    for attempt in range(3):
        try:
            r = await client.get(url, timeout=6, follow_redirects=True)
            d = r.json()
            return d if isinstance(d, dict) else None
        except Exception:
            if attempt < 2:
                await asyncio.sleep(0.4 * (attempt + 1))
    return None


def _entity_country(entities) -> str:
    """Registry country can sit on an org entity when the network object omits a
    top-level `country` (ARIN). Best-effort scan (incl. nested) for a 2-letter code."""
    for ent in entities or []:
        if not isinstance(ent, dict):
            continue
        c = ent.get("country")
        if isinstance(c, str) and len(c) == 2:
            return c
        nested = _entity_country(ent.get("entities"))
        if nested:
            return nested
    return ""


async def _rdap_ip_cc(ip: str, client: httpx.AsyncClient) -> str:
    d = await _rdap_get(f"https://rdap.org/ip/{ip}", client)
    if not d:
        return ""
    cc = str(d.get("country") or "").strip() or _entity_country(d.get("entities"))
    return cc[:2].upper() if cc else ""


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
    d = await _rdap_get(f"https://rdap.org/autnum/{asn}", client)
    if not d:
        return "", ""
    return str(d.get("name") or ""), _website_from_rdap_autnum(d)


async def _ripestat_cc(ip: str, client: httpx.AsyncClient) -> str:
    """Registry country from RIPEstat's rir-geo dataset (RIR delegation stats).
    Covers ALL RIRs incl. ARIN, so it fills the gaps where RDAP has no top-level
    country. Fixed public host — no SSRF concern."""
    try:
        r = await client.get(
            "https://stat.ripe.net/data/rir-geo/data.json",
            params={"resource": ip}, timeout=6,
        )
        locs = (r.json().get("data") or {}).get("located_resources") or []
        for loc in locs:
            cc = str(loc.get("location") or "").strip()
            if cc:
                return cc[:2].upper()
    except Exception:
        pass
    return ""


async def _peeringdb_website(asn: int, client: httpx.AsyncClient) -> str:
    """ASN website from PeeringDB (net?asn=). Fallback for the RDAP autnum website,
    which is often empty. Fixed public host. Анонимные запросы rate-limit'ятся и
    иногда отвечают HTML-челленджем вместо JSON — оба случая молча пропускаем."""
    if asn <= 0:
        return ""
    try:
        r = await client.get(f"https://www.peeringdb.com/api/net?asn={asn}", timeout=6)
        if r.status_code != 200 or "json" not in (r.headers.get("content-type") or ""):
            return ""
        data = r.json().get("data") or []
        if data:
            return str(data[0].get("website") or "")
    except Exception:
        pass
    return ""


async def _traceroute_last_hop(ip: str) -> Optional[str]:
    """Run a system traceroute to `ip` and return the LAST PUBLIC hop IP — the
    destination if it answers, else the closest responding router (its geo is the
    real datacentre). None when traceroute is unavailable/blackholed. Bounded by
    `_TRACE_SEM` + `_TRACE_TIMEOUT`; never raises."""
    is_win = platform.system() == "Windows"
    exe = "tracert" if is_win else "traceroute"
    if not shutil.which(exe):
        return None
    args = ([exe, "-d", "-w", "1000", "-h", "12", ip] if is_win
            else [exe, "-n", "-q", "1", "-w", "1", "-m", "12", ip])
    async with _TRACE_SEM:
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=_TRACE_TIMEOUT)
        except Exception:
            if proc:
                try:
                    proc.kill()
                except Exception:
                    pass
            return None
    text = (out or b"").decode("utf-8", "replace")
    hops = re.findall(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", text)
    for hop in reversed(hops):
        if _ip_is_public(hop):
            return hop
    return None


async def _resolve_ip(ip: str, client: httpx.AsyncClient, autnum_cache: dict) -> dict:
    # ASN + a geolocation baseline from the DESTINATION IP.
    dest = await _ip_api(ip, client) or await _ipwho(ip, client) or {}

    # Actual geo via traceroute's last public hop (item: geo by trace). Reuse the
    # destination geo when the last hop IS the destination (or trace unavailable);
    # otherwise geolocate the hop. Either way, ASN stays the destination's.
    hop = await _traceroute_last_hop(ip)
    if hop and hop != ip:
        hop_geo = await _ip_api(hop, client) or await _ipwho(hop, client) or {}
        geo_cc = hop_geo.get("cc") or dest.get("cc", "")
        geo_city = hop_geo.get("city") if hop_geo.get("cc") else dest.get("city", "")
    else:
        geo_cc, geo_city = dest.get("cc", ""), dest.get("city", "")

    # Registry country: RDAP, then RIPEstat (fills ARIN/US gaps).
    registry_cc = await _rdap_ip_cc(ip, client) or await _ripestat_cc(ip, client)

    asn_number = int(dest.get("asn_number") or 0)
    asn_name = str(dest.get("asn_name") or "")
    website = ""
    website_source = ""
    if asn_number:
        if asn_number not in autnum_cache:
            reg_name, site = await _rdap_autnum(asn_number, client)
            src = "rdap" if site else ""
            if not site:
                site = await _peeringdb_website(asn_number, client)
                src = "peeringdb" if site else ""
            autnum_cache[asn_number] = (reg_name, site, src)
        reg_name, website, website_source = autnum_cache[asn_number]
        asn_name = asn_name or reg_name
    return {
        "ip": ip,
        "asn": {"number": asn_number, "name": asn_name, "website": website,
                "website_source": website_source},
        "geo_actual": {"cc": geo_cc or "", "city": geo_city or ""},
        "geo_registry": {"cc": registry_cc},
        "net": dest.get("net") or {"org": "", "isp": "", "ptr": "",
                                   "hosting": False, "proxy": False},
    }


# ── orchestration ──────────────────────────────────────────────
async def analyze(raw: str, user_agent: str = "") -> list[dict[str, Any]]:
    """One row per unique target IP. Hosts are deduped by HOSTNAME first (each
    address resolved once — fixes the same host appearing N× and round-robin DNS
    giving different IPs), then merged by resolved IP; the subscription link names
    for every address on an IP are aggregated (`names`, comma-listed in the UI).
    `user_agent` — фиксированный UA из селектора UI (пусто → цепочка Авто)."""
    kind = classify_input(raw)
    pairs: list[tuple[str, str]] = []       # (host address, link name)
    if kind == "url":
        links = decode_subscription(await fetch_subscription(raw.strip(), user_agent))
        for link in links:
            cand = link_to_candidate(link)
            if cand and cand.get("host"):
                pairs.append((cand["host"], cand.get("name") or ""))
    else:
        pairs.append((raw.strip(), ""))

    # group link names by hostname (dedup, preserve first-seen order)
    host_names: dict[str, list[str]] = {}
    for host, name in pairs:
        names = host_names.setdefault(host, [])
        if name and name not in names:
            names.append(name)
    if not host_names:
        return []

    # resolve each UNIQUE hostname once
    hosts = list(host_names.keys())
    resolved = await asyncio.gather(*(_resolve(h) for h in hosts))

    # merge hostnames that resolve to the same public IP; aggregate hosts + names
    by_ip: dict[str, dict] = {}
    for host, ip in zip(hosts, resolved):
        if not ip or not _ip_is_public(ip):
            continue
        g = by_ip.setdefault(ip, {"hosts": [], "names": []})
        if host not in g["hosts"]:
            g["hosts"].append(host)
        for n in host_names[host]:
            if n not in g["names"]:
                g["names"].append(n)
    if not by_ip:
        return []

    sem = asyncio.Semaphore(_LOOKUP_LIMIT)
    autnum_cache: dict[int, tuple[str, str, str]] = {}

    async with httpx.AsyncClient(timeout=_API_TIMEOUT, headers={"User-Agent": _API_UA}) as client:
        async def one(ip: str, g: dict) -> dict:
            async with sem:
                row = await _resolve_ip(ip, client, autnum_cache)
            row["host"] = g["hosts"][0]
            row["hosts"] = g["hosts"]
            row["names"] = g["names"]
            return row

        rows = await asyncio.gather(*(one(ip, g) for ip, g in by_ip.items()))
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
                "_names": [],      # subscription link names seen for this ASN
            }
        ga = r.get("geo_actual") or {}
        cc = str(ga.get("cc") or "")
        city = str(ga.get("city") or "")
        if cc or city:
            g["_locs"][(cc, city)] = {"city": city, "country_code": cc[:2], "lat": 0, "lng": 0, "note": ""}
        for n in r.get("names") or []:
            if n and n not in g["_names"]:
                g["_names"].append(n)

    out: list[dict] = []
    for g in groups.values():
        locs = list(g.pop("_locs").values())
        names = g.pop("_names")
        out.append({
            "name": g["name"],
            "website": g["website"],
            # keep the subscription names so the info isn't lost on «в хостинги»
            "notes": ("Из подписки: " + ", ".join(names))[:500] if names else "",
            "features": "",
            "tags": [],
            "tariffs": [],
            "locations": locs,
            "asns": g["asns"],
            "provider_ref": None,
        })
    return out
