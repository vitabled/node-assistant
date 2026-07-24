"""Wave-7 Plan B Ф2 — importing subscription nodes into «Доступность серверов»."""
import asyncio
import base64
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api import server_monitor as sm

client = TestClient(app)

VLESS = "vless://11111111-2222-3333-4444-555555555555@node1.example.com:443?type=tcp&security=tls#%F0%9F%87%B3%F0%9F%87%B1%20AMS"
TROJAN = "trojan://pw@node2.example.com:8443?security=tls#Германия"
BODY = base64.b64encode(f"{VLESS}\n{TROJAN}\n".encode()).decode()


def _auth():
    r = client.post("/api/auth/register", json={"login": f"si-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def stub(monkeypatch):
    """Serve a fixed subscription body and a deterministic DNS."""
    async def fake_fetch(url):
        return BODY

    async def fake_resolve(host, sem):
        return {"node1.example.com": "203.0.113.10", "node2.example.com": "203.0.113.20"}.get(host, "")

    monkeypatch.setattr(sm, "_fetch_subscription", fake_fetch)
    monkeypatch.setattr(sm, "_resolve", fake_resolve)


def test_dry_run_previews_without_writing(stub):
    h = _auth()
    r = client.post("/api/server-monitor/import/subscription", headers=h,
                    json={"url": "https://example.com/sub", "dry_run": True})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["imported"] == 0
    assert [c["status"] for c in d["candidates"]] == ["new", "new"]
    assert {c["ip"] for c in d["candidates"]} == {"203.0.113.10", "203.0.113.20"}
    # nothing persisted
    assert client.get("/api/server-monitor/servers", headers=h).json() == []


def test_import_creates_rows_with_origin_in_note(stub):
    h = _auth()
    r = client.post("/api/server-monitor/import/subscription", headers=h,
                    json={"url": "https://example.com/sub", "dry_run": False})
    assert r.json()["imported"] == 2
    rows = client.get("/api/server-monitor/servers", headers=h).json()
    assert len(rows) == 2
    by_ip = {s["ip"]: s for s in rows}
    assert by_ip["203.0.113.10"]["country"] == "NL"
    assert "node1.example.com" in by_ip["203.0.113.10"]["note"]
    # source stays 'manual' so the row can still be edited and deleted
    assert {s["source"] for s in rows} == {"manual"}


def test_second_import_reports_duplicates_instead_of_doubling(stub):
    h = _auth()
    client.post("/api/server-monitor/import/subscription", headers=h,
                json={"url": "https://example.com/sub", "dry_run": False})
    r = client.post("/api/server-monitor/import/subscription", headers=h,
                    json={"url": "https://example.com/sub", "dry_run": False})
    assert r.json()["imported"] == 0
    assert [c["status"] for c in r.json()["candidates"]] == ["duplicate", "duplicate"]
    assert len(client.get("/api/server-monitor/servers", headers=h).json()) == 2


def test_unresolved_host_is_flagged_and_not_imported(monkeypatch):
    h = _auth()

    async def fake_fetch(url):
        return BODY

    async def no_dns(host, sem):
        return ""

    monkeypatch.setattr(sm, "_fetch_subscription", fake_fetch)
    monkeypatch.setattr(sm, "_resolve", no_dns)
    r = client.post("/api/server-monitor/import/subscription", headers=h,
                    json={"url": "https://example.com/sub", "dry_run": False})
    assert r.json()["imported"] == 0
    assert [c["status"] for c in r.json()["candidates"]] == ["unresolved", "unresolved"]
    assert client.get("/api/server-monitor/servers", headers=h).json() == []


def test_ssrf_guard_rejects_a_private_url():
    h = _auth()
    r = client.post("/api/server-monitor/import/subscription", headers=h,
                    json={"url": "http://127.0.0.1/sub", "dry_run": True})
    assert r.status_code == 400


def test_unknown_subscription_id_is_404():
    h = _auth()
    r = client.post("/api/server-monitor/import/subscription", headers=h,
                    json={"subscription_id": "nope", "dry_run": True})
    assert r.status_code == 404


def test_requires_auth():
    assert client.post("/api/server-monitor/import/subscription", json={"url": "x"}).status_code == 401


# ── redirect handling (the "Не удалось загрузить подписку" bug) ──
def _mock_httpx(monkeypatch, handler):
    """Route _fetch_subscription's internal AsyncClient through a MockTransport."""
    transport = httpx.MockTransport(handler)
    real_init = httpx.AsyncClient.__init__

    def patched(self, *a, **kw):
        kw["transport"] = transport
        real_init(self, *a, **kw)
    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)


def test_fetch_follows_a_redirect_chain(monkeypatch):
    """Subscription CDNs 301-redirect; follow_redirects=False made
    raise_for_status() throw on the 301 → the reported 502."""
    def handler(request):
        host = request.url.host
        if host == "sub.example":
            return httpx.Response(301, headers={"location": "https://cdn.example/real"})
        if host == "cdn.example":
            return httpx.Response(200, text="vless://11111111-2222-3333-4444-555555555555@n.example:443#DE")
        return httpx.Response(404)

    _mock_httpx(monkeypatch, handler)
    monkeypatch.setattr(sm.net_guard, "is_safe_url", lambda u: True)

    body = asyncio.run(sm._fetch_subscription("https://sub.example/x"))
    assert "vless://" in body


def test_fetch_rejects_a_redirect_to_a_non_public_host(monkeypatch):
    """A redirect to an internal address is an SSRF pivot — each hop is
    re-validated, so it must be refused, not followed."""
    def handler(request):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})

    _mock_httpx(monkeypatch, handler)
    # entry URL passes; the redirect target does not
    monkeypatch.setattr(sm.net_guard, "is_safe_url",
                        lambda u: "169.254" not in u)

    with pytest.raises(sm.HTTPException) as e:
        asyncio.run(sm._fetch_subscription("https://sub.example/x"))
    assert e.value.status_code == 400


def test_fetch_caps_a_giant_body(monkeypatch):
    def handler(request):
        return httpx.Response(200, content=b"0" * (sm._MAX_SUB_BYTES + 10))

    _mock_httpx(monkeypatch, handler)
    monkeypatch.setattr(sm.net_guard, "is_safe_url", lambda u: True)

    with pytest.raises(sm.HTTPException) as e:
        asyncio.run(sm._fetch_subscription("https://sub.example/x"))
    assert e.value.status_code == 413
