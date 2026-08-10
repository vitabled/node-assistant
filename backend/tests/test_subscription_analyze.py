"""Wave-8 §7 — subscription/domain/IP analysis: pure helpers + routes."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import subscription_analyze as sa

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"sa-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ── pure helpers ───────────────────────────────────────────────
def test_classify_input():
    assert sa.classify_input("https://sub.example.com/x") == "url"
    assert sa.classify_input("http://a/b") == "url"
    assert sa.classify_input("8.8.8.8") == "ip"
    assert sa.classify_input("example.com") == "domain"


def test_parse_as_field():
    assert sa._parse_as_field("AS49505 Selectel Network") == (49505, "Selectel Network")
    assert sa._parse_as_field("AS13335") == (13335, "")
    assert sa._parse_as_field("garbage") == (0, "")


def test_ip_public():
    assert sa._ip_is_public("8.8.8.8") is True
    assert sa._ip_is_public("127.0.0.1") is False
    assert sa._ip_is_public("10.0.0.1") is False
    assert sa._ip_is_public("169.254.169.254") is False
    assert sa._ip_is_public("not-an-ip") is False


def test_group_to_hostings_dedup_by_asn():
    results = [
        {"host": "a.com", "ip": "1.1.1.1", "names": ["🇷🇺 Москва"],
         "asn": {"number": 49505, "name": "Selectel", "website": "https://selectel.ru"},
         "geo_actual": {"cc": "RU", "city": "Moscow"}, "geo_registry": {"cc": "RU"}},
        {"host": "b.com", "ip": "1.1.1.2", "names": ["🇷🇺 СПб"],
         "asn": {"number": 49505, "name": "Selectel", "website": "https://selectel.ru"},
         "geo_actual": {"cc": "RU", "city": "Saint Petersburg"}, "geo_registry": {"cc": "RU"}},
        {"host": "c.com", "ip": "2.2.2.2",
         "asn": {"number": 13335, "name": "Cloudflare", "website": ""},
         "geo_actual": {"cc": "US", "city": "SF"}, "geo_registry": {"cc": "US"}},
        # no ASN → skipped
        {"host": "d.com", "ip": "3.3.3.3", "asn": {"number": 0, "name": "", "website": ""},
         "geo_actual": {"cc": "DE", "city": "Berlin"}, "geo_registry": {"cc": "DE"}},
    ]
    out = sa.group_to_hostings(results)
    assert len(out) == 2                                     # 2 ASNs, no-ASN dropped
    sel = next(h for h in out if h["asns"][0]["number"] == 49505)
    assert sel["name"] == "Selectel" and sel["website"] == "https://selectel.ru"
    # two distinct cities merged into one Selectel entry
    cities = sorted(l["city"] for l in sel["locations"])
    assert cities == ["Moscow", "Saint Petersburg"]
    # subscription link names carried into notes (Wave-8 feedback)
    assert "🇷🇺 Москва" in sel["notes"] and "🇷🇺 СПб" in sel["notes"]


def test_analyze_dedups_hosts_and_aggregates_names(monkeypatch):
    """Item 4/5: the same host in several links → ONE row with all its names."""
    import asyncio

    async def fake_fetch(url, user_agent=""):
        return "ignored"

    cand = {
        "l1": {"host": "github.com", "port": 443, "name": "Авто", "country": ""},
        "l2": {"host": "github.com", "port": 443, "name": "NL", "country": ""},
        "l3": {"host": "de.example.com", "port": 443, "name": "DE", "country": ""},
    }

    async def fake_resolve(h):
        return {"github.com": "1.1.1.1", "de.example.com": "2.2.2.2"}[h]

    async def fake_resolve_ip(ip, client, cache):
        return {"ip": ip, "asn": {"number": 1, "name": "X", "website": ""},
                "geo_actual": {"cc": "DE", "city": ""}, "geo_registry": {"cc": "DE"}}

    monkeypatch.setattr(sa, "fetch_subscription", fake_fetch)
    monkeypatch.setattr(sa, "decode_subscription", lambda body: ["l1", "l2", "l3"])
    monkeypatch.setattr(sa, "link_to_candidate", lambda l: cand[l])
    monkeypatch.setattr(sa, "_resolve", fake_resolve)
    monkeypatch.setattr(sa, "_resolve_ip", fake_resolve_ip)

    rows = asyncio.run(sa.analyze("https://p/sub"))
    assert len(rows) == 2                                    # github.com deduped to 1
    gh = next(r for r in rows if r["host"] == "github.com")
    assert gh["names"] == ["Авто", "NL"]                     # both names aggregated


# ── SSRF guard on the URL fetch ────────────────────────────────
def test_analyze_rejects_private_url():
    import asyncio
    with pytest.raises(ValueError):
        asyncio.run(sa.fetch_subscription("http://127.0.0.1/sub"))


# base64 of "vless://a@example.com:1000" — a parseable share-link body.
_LINK_B64 = b"dmxlc3M6Ly9hQGV4YW1wbGUuY29tOjEwMDA="


def _fake_httpx(monkeypatch, body_for_ua, seen):
    """Patch httpx.AsyncClient so fetch_subscription returns body_for_ua(ua) and
    records each UA tried in `seen`."""
    class _Stream:
        def __init__(self, headers): self._h = headers
        async def __aenter__(self):
            ua = (self._h or {}).get("User-Agent")
            seen.append(ua)
            data = body_for_ua(ua)
            class _R:
                is_redirect = False
                def raise_for_status(self): pass
                async def aiter_bytes(self):
                    yield data
            return _R()
        async def __aexit__(self, *a): pass

    class _Client:
        def __init__(self, *a, **k): self._h = k.get("headers")
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        def stream(self, m, u): return _Stream(self._h)

    monkeypatch.setattr(sa.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(sa.net_guard, "is_safe_url", lambda u: True)


def test_fetch_default_ua_first_when_it_parses(monkeypatch):
    """When the default UA already yields share links, no client UA is tried."""
    import asyncio
    seen = []
    _fake_httpx(monkeypatch, lambda ua: _LINK_B64, seen)
    body = asyncio.run(sa.fetch_subscription("https://p.example.com/sub"))
    assert seen == [None]                        # only the default UA was used
    assert sa.decode_subscription(body)


def test_fetch_ua_fallback_to_client(monkeypatch):
    """Default UA returns a JSON config (0 links) → fall through to the client UAs
    (Happ first) and use the first that parses — the hardsub.digital scenario."""
    import asyncio
    seen = []
    # None (default) → JSON with no share links; any client UA → a link list.
    _fake_httpx(monkeypatch,
                lambda ua: _LINK_B64 if ua else b'[{"outbounds":[{"protocol":"vless"}]}]',
                seen)
    body = asyncio.run(sa.fetch_subscription("https://p.example.com/sub"))
    assert seen[0] is None and seen[1] == "Happ/1.16.0"   # default first, then Happ
    assert sa.decode_subscription(body)                    # returned the parseable body


# ── routes ─────────────────────────────────────────────────────
def test_resolve_ip_fallbacks(monkeypatch):
    """Registry falls back RDAP→RIPEstat; ASN website falls back RDAP→PeeringDB;
    actual geo comes from the traceroute last hop when it differs from the dest."""
    import asyncio

    async def ip_api(ip, c):
        # dest 1.1.1.1 → ASN + US baseline; trace hop 2.2.2.2 → DE
        return ({"cc": "US", "city": "NY", "asn_number": 42, "asn_name": "Foo"}
                if ip == "1.1.1.1"
                else {"cc": "DE", "city": "Berlin", "asn_number": 0, "asn_name": ""})

    async def rdap_cc(ip, c):
        return ""                                    # RDAP empty → RIPEstat used

    async def ripestat(ip, c):
        return "GB"

    async def rdap_autnum(asn, c):
        return ("FooNet", "")                        # no website → PeeringDB used

    async def peeringdb(asn, c):
        return "https://foo.example"

    async def trace(ip):
        return "2.2.2.2"                             # hop differs from dest

    monkeypatch.setattr(sa, "_ip_api", ip_api)
    monkeypatch.setattr(sa, "_rdap_ip_cc", rdap_cc)
    monkeypatch.setattr(sa, "_ripestat_cc", ripestat)
    monkeypatch.setattr(sa, "_rdap_autnum", rdap_autnum)
    monkeypatch.setattr(sa, "_peeringdb_website", peeringdb)
    monkeypatch.setattr(sa, "_traceroute_last_hop", trace)

    row = asyncio.run(sa._resolve_ip("1.1.1.1", None, {}))
    assert row["geo_registry"]["cc"] == "GB"          # RIPEstat fallback
    assert row["asn"]["website"] == "https://foo.example"  # PeeringDB fallback
    assert row["asn"]["number"] == 42                 # ASN from the destination
    assert row["geo_actual"]["cc"] == "DE"            # geo from the trace hop


def test_analyze_route_empty_input():
    a = _auth()
    assert client.post("/api/subscription-analyze", headers=a, json={"input": ""}).status_code == 400


def test_analyze_route_monkeypatched(monkeypatch):
    a = _auth()

    async def _fake(raw, user_agent=""):
        return [{"host": "x.com", "ip": "1.2.3.4",
                 "asn": {"number": 42, "name": "Foo", "website": "https://foo"},
                 "geo_actual": {"cc": "NL", "city": "AMS"}, "geo_registry": {"cc": "NL"}}]

    monkeypatch.setattr(sa, "analyze", _fake)
    r = client.post("/api/subscription-analyze", headers=a, json={"input": "example.com"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "domain"
    assert body["results"][0]["asn"]["number"] == 42


def test_to_hostings_creates_then_updates():
    a = _auth()
    results = [
        {"host": "a.com", "ip": "1.1.1.1",
         "asn": {"number": 49505, "name": "Selectel", "website": "https://selectel.ru"},
         "geo_actual": {"cc": "RU", "city": "Moscow"}, "geo_registry": {"cc": "RU"}},
    ]
    r = client.post("/api/subscription-analyze/to-hostings", headers=a, json={"results": results})
    assert r.status_code == 200 and r.json() == {"ok": True, "created": 1, "updated": 0}

    # a hosting now exists, with the ASN + one location
    lst = client.get("/api/hostings", headers=a).json()
    assert len(lst) == 1 and lst[0]["asns"][0]["number"] == 49505
    assert lst[0]["locations"][0]["city"] == "Moscow"

    # same ASN, new city → merged into the SAME card (updated, not created)
    results2 = [{**results[0], "geo_actual": {"cc": "RU", "city": "Kazan"}}]
    r2 = client.post("/api/subscription-analyze/to-hostings", headers=a, json={"results": results2})
    assert r2.json() == {"ok": True, "created": 0, "updated": 1}
    lst2 = client.get("/api/hostings", headers=a).json()
    assert len(lst2) == 1
    cities = sorted(l["city"] for l in lst2[0]["locations"])
    assert cities == ["Kazan", "Moscow"]


def test_to_hostings_no_asn_400():
    a = _auth()
    results = [{"host": "d.com", "ip": "3.3.3.3", "asn": {"number": 0}}]
    assert client.post("/api/subscription-analyze/to-hostings", headers=a,
                       json={"results": results}).status_code == 400


# ── Wave-4 PR-4: фиксированный UA, расшифровка IP, источник website ──
def test_fetch_fixed_user_agent_skips_chain(monkeypatch):
    """Селектор UA в UI → только этот UA, цепочка не ходит."""
    import asyncio
    seen = []
    _fake_httpx(monkeypatch, lambda ua: b'[{"outbounds":[]}]', seen)
    body = asyncio.run(sa.fetch_subscription("https://p.example.com/sub", "v2rayNG/1.9.39"))
    assert seen == ["v2rayNG/1.9.39"]          # ровно один запрос, выбранным UA
    assert "outbounds" in body                  # тело возвращается как есть


def test_resolve_ip_net_and_website_source(monkeypatch):
    """net-расшифровка из _ip_api попадает в строку; website_source = peeringdb
    при пустом RDAP и rdap, когда RDAP отдал сайт."""
    import asyncio

    async def ip_api(ip, c):
        return {"cc": "US", "city": "NY", "asn_number": 42, "asn_name": "Foo",
                "net": {"org": "FooNet LLC", "isp": "Foo ISP",
                        "ptr": "host.foo.example", "hosting": True, "proxy": False}}

    async def rdap_autnum_empty(asn, c):
        return ("FooNet", "")

    async def peeringdb(asn, c):
        return "https://foo.example"

    monkeypatch.setattr(sa, "_ip_api", ip_api)
    monkeypatch.setattr(sa, "_ipwho", None)
    monkeypatch.setattr(sa, "_rdap_ip_cc", lambda ip, c: asyncio.sleep(0, "US"))
    monkeypatch.setattr(sa, "_ripestat_cc", lambda ip, c: asyncio.sleep(0, ""))
    monkeypatch.setattr(sa, "_traceroute_last_hop", lambda ip: asyncio.sleep(0, None))
    monkeypatch.setattr(sa, "_rdap_autnum", rdap_autnum_empty)
    monkeypatch.setattr(sa, "_peeringdb_website", peeringdb)

    row = asyncio.run(sa._resolve_ip("1.1.1.1", None, {}))
    assert row["net"]["org"] == "FooNet LLC"
    assert row["net"]["ptr"] == "host.foo.example"
    assert row["net"]["hosting"] is True
    assert row["asn"]["website"] == "https://foo.example"
    assert row["asn"]["website_source"] == "peeringdb"

    async def rdap_autnum_site(asn, c):
        return ("FooNet", "https://rdap-site.example")

    async def peeringdb_unused(asn, c):
        raise AssertionError("не должен зваться, когда RDAP дал сайт")

    monkeypatch.setattr(sa, "_rdap_autnum", rdap_autnum_site)
    monkeypatch.setattr(sa, "_peeringdb_website", peeringdb_unused)
    row = asyncio.run(sa._resolve_ip("1.1.1.1", None, {}))
    assert row["asn"]["website"] == "https://rdap-site.example"
    assert row["asn"]["website_source"] == "rdap"


def test_analyze_route_passes_user_agent(monkeypatch):
    a = _auth()
    seen = {}

    async def _fake(raw, user_agent=""):
        seen["ua"] = user_agent
        return [{"host": "x.com", "ip": "1.2.3.4",
                 "asn": {"number": 1, "name": "", "website": ""},
                 "geo_actual": {"cc": "", "city": ""}, "geo_registry": {"cc": ""},
                 "net": {"org": "", "isp": "", "ptr": "", "hosting": False, "proxy": False}}]

    monkeypatch.setattr(sa, "analyze", _fake)
    r = client.post("/api/subscription-analyze", headers=a,
                    json={"input": "https://p/sub", "user_agent": "Streisand/1.6.0"})
    assert r.status_code == 200
    assert seen["ua"] == "Streisand/1.6.0"
