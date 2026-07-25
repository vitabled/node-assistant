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
        {"host": "a.com", "ip": "1.1.1.1",
         "asn": {"number": 49505, "name": "Selectel", "website": "https://selectel.ru"},
         "geo_actual": {"cc": "RU", "city": "Moscow"}, "geo_registry": {"cc": "RU"}},
        {"host": "b.com", "ip": "1.1.1.2",
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
def test_analyze_route_empty_input():
    a = _auth()
    assert client.post("/api/subscription-analyze", headers=a, json={"input": ""}).status_code == 400


def test_analyze_route_monkeypatched(monkeypatch):
    a = _auth()

    async def _fake(raw):
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
