"""Wave-4 PR-8 — выходной IP: маршрут /egress, кэш подписки, ошибки ссылки."""
import asyncio
import time
import uuid as _uuid

from fastapi.testclient import TestClient

from app.main import app
import app.api.sub_analysis as sa
from app.services import egress_check as ec

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"eg-{_uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_egress_requires_input_and_host():
    a = _auth()
    assert client.post("/api/subscription-analyze/egress", headers=a, json={}).status_code == 400
    assert client.post("/api/subscription-analyze/egress", headers=a,
                       json={"input": "https://p/sub"}).status_code == 400


def test_egress_404_when_host_absent(monkeypatch):
    a = _auth()
    monkeypatch.setattr(sa, "_subscription_links",
                        lambda raw, ua: asyncio.sleep(0, {"other.example.com": ["x"]}))
    r = client.post("/api/subscription-analyze/egress", headers=a,
                    json={"input": "https://p/sub", "host": "missing.example.com"})
    assert r.status_code == 404


def test_egress_success(monkeypatch):
    a = _auth()
    monkeypatch.setattr(sa, "_subscription_links",
                        lambda raw, ua: asyncio.sleep(0, {"de.example.com": ["vless://x"]}))

    async def fake_check(link):
        assert link == "vless://x"
        return {"ok": True, "egress": {"ip": "5.6.7.8", "cc": "DE", "city": "Berlin",
                                       "org": "FooNet", "isp": "", "as": "AS42",
                                       "hosting": True, "proxy": False}}

    monkeypatch.setattr(sa, "check_egress", fake_check)
    r = client.post("/api/subscription-analyze/egress", headers=a,
                    json={"input": "https://p/sub", "host": "de.example.com"})
    assert r.status_code == 200
    assert r.json()["egress"]["ip"] == "5.6.7.8"


def test_egress_check_failure_is_502(monkeypatch):
    a = _auth()
    monkeypatch.setattr(sa, "_subscription_links",
                        lambda raw, ua: asyncio.sleep(0, {"de.example.com": ["vless://x"]}))
    monkeypatch.setattr(sa, "check_egress",
                        lambda link: asyncio.sleep(0, {"ok": False, "error": "Туннель не поднялся"}))
    r = client.post("/api/subscription-analyze/egress", headers=a,
                    json={"input": "https://p/sub", "host": "de.example.com"})
    assert r.status_code == 502 and "Туннель не поднялся" in r.json()["detail"]


def test_subscription_cache(monkeypatch):
    """Повторный запрос той же подписки в TTL не перекачивает её."""
    calls = []

    async def fake_fetch(url, ua=""):
        calls.append(url)
        return "body"

    links = ["vless://a@de.example.com:443#DE"]
    monkeypatch.setattr(sa.analyzer, "fetch_subscription", fake_fetch)
    monkeypatch.setattr("app.services.subscription_import.decode_subscription", lambda b: links)
    monkeypatch.setattr("app.services.subscription_import.link_to_candidate",
                        lambda l: {"host": "de.example.com", "name": "DE"})
    sa._SUB_CACHE.clear()

    out1 = asyncio.run(sa._subscription_links("https://p/sub", ""))
    out2 = asyncio.run(sa._subscription_links("https://p/sub", ""))
    assert out1 == out2 == {"de.example.com": links}
    assert calls == ["https://p/sub"]                    # один фетч на двоих
    # другой UA — другой ключ кэша
    asyncio.run(sa._subscription_links("https://p/sub", "v2rayNG/1.9.39"))
    assert calls == ["https://p/sub", "https://p/sub"]


def test_check_egress_bad_link_no_xray_needed():
    """Некорректная ссылка отвечает ошибкой ДО запуска xray."""
    out = asyncio.run(ec.check_egress("not-a-link", xray_bin=None))
    assert out["ok"] is False and "схема" in out["error"].lower()


def test_http_body_identity_and_chunked():
    assert ec._http_body(b"HTTP/1.1 200 OK\r\n\r\n{\"a\":1}") == b'{"a":1}'
    chunked = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n7\r\n{\"a\":1}\r\n0\r\n\r\n"
    assert ec._http_body(chunked) == b'{"a":1}'
