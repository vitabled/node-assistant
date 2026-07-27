"""Wave-9 Plan B Ф1–Ф2 — Cloudflare connection + billing API (api/cloudflare.py).

Cloudflare is never reached: the billing routes monkeypatch `CfClient` methods, and
the envelope/redaction path drives the REAL client over `httpx.MockTransport`.
Covered: account gating, config CRUD + has_token + blank-keeps + encryption-at-rest,
the token never appearing in a response, the not-connected 400 gate, a CF error →
502 with the token redacted, `summary` degrading (403 on a sub-endpoint) instead of
500, and the 15-min cache incl. `?refresh=1`.
"""

import json
import uuid

import httpx
from fastapi import Depends
from fastapi.testclient import TestClient

from app.api import cloudflare as cf_api
from app.api.auth import require_account
from app.main import app
from app.services import accounts, cf_client, storage

# The router is wired into main.py by the integrating agent; add it here when it
# isn't there yet so this file tests the REAL app either way (same gating).
if not any(getattr(r, "path", "").startswith("/api/cloudflare") for r in app.routes):
    app.include_router(cf_api.router, dependencies=[Depends(require_account)])

client = TestClient(app)

TOKEN = "cf-tok-LIVE-DO-NOT-LEAK"


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"cf-{uuid.uuid4().hex[:8]}", "password": "pw"})
    body = r.json()
    return {"Authorization": f"Bearer {body['token']}"}, body["id"]


async def _async(value):
    """Await-able stand-in for a CfClient coroutine method."""
    return value


def _connect(headers, account_id="acc-1", token=TOKEN):
    return client.post("/api/cloudflare/config", headers=headers,
                       json={"enabled": True, "account_id": account_id, "api_token": token})


_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _mock_transport(monkeypatch, handler):
    """Route the real CfClient through MockTransport (no network, real unwrap).
    Bound to the class captured at import time so a second call inside one test
    doesn't wrap the previous factory."""

    def factory(**kw):
        kw.pop("transport", None)
        return _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(cf_client.httpx, "AsyncClient", factory)


def test_routes_require_account():
    assert client.get("/api/cloudflare/config").status_code == 401
    assert client.post("/api/cloudflare/config", json={"enabled": True}).status_code == 401
    assert client.post("/api/cloudflare/test").status_code == 401
    assert client.get("/api/cloudflare/accounts").status_code == 401
    assert client.get("/api/cloudflare/billing/summary").status_code == 401
    assert client.get("/api/cloudflare/subscriptions").status_code == 401
    assert client.get("/api/cloudflare/usage").status_code == 401
    assert client.get("/api/cloudflare/zones").status_code == 401


def test_default_config_is_empty():
    h, _ = _auth()
    cfg = client.get("/api/cloudflare/config", headers=h).json()
    assert cfg == {"enabled": False, "account_id": "", "has_token": False,
                   "default_contact": {}}


def test_config_crud_encrypts_token_and_blank_keeps_it():
    h, aid = _auth()
    r = _connect(h)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["enabled"] and body["account_id"] == "acc-1"
    assert body["has_token"] is True

    raw = (accounts.data_dir(aid) / "settings.json").read_text(encoding="utf-8")
    assert TOKEN not in raw and "api_token_enc" in raw
    stored = storage.load_settings(aid)["cloudflare"]["api_token_enc"]
    assert cf_client.decrypt_token(stored) == TOKEN

    # blank token keeps the stored one; contact omitted keeps the stored one
    client.post("/api/cloudflare/config", headers=h,
                json={"enabled": True, "account_id": "acc-1",
                      "default_contact": {"email": "a@b.c"}})
    r2 = client.post("/api/cloudflare/config", headers=h,
                     json={"enabled": False, "account_id": " acc-2 ", "api_token": "  "})
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["has_token"] is True and body2["enabled"] is False
    assert body2["account_id"] == "acc-2"                      # trimmed
    assert body2["default_contact"] == {"email": "a@b.c"}       # not wiped
    assert storage.load_settings(aid)["cloudflare"]["api_token_enc"] == stored


def test_token_never_leaves_the_backend(monkeypatch):
    h, _ = _auth()
    _connect(h)

    async def fake(self, *a, **kw):
        return []

    for name in ("accounts", "zones", "subscriptions"):
        monkeypatch.setattr(cf_client.CfClient, name, fake)
    monkeypatch.setattr(cf_client.CfClient, "billing_profile",
                        lambda self, acc: _async({"balance": 1}))
    monkeypatch.setattr(cf_client.CfClient, "paygo_info", lambda self, acc: _async({}))

    for url in ("/api/cloudflare/config", "/api/cloudflare/accounts",
                "/api/cloudflare/zones", "/api/cloudflare/subscriptions",
                "/api/cloudflare/billing/summary"):
        r = client.get(url, headers=h)
        assert r.status_code == 200, url
        assert TOKEN not in json.dumps(r.json(), ensure_ascii=False), url


def test_not_connected_gate():
    h, _ = _auth()
    for url in ("/api/cloudflare/accounts", "/api/cloudflare/billing/summary",
                "/api/cloudflare/subscriptions", "/api/cloudflare/usage",
                "/api/cloudflare/zones"):
        r = client.get(url, headers=h)
        assert r.status_code == 400, url
        assert "Настройки" in r.json()["detail"]
    r = client.post("/api/cloudflare/test", headers=h)
    assert r.status_code == 400 and "не подключён" in r.json()["detail"]


def test_no_cf_account_selected_gate(monkeypatch):
    h, _ = _auth()
    _connect(h, account_id="")          # token present, account not chosen yet
    for url in ("/api/cloudflare/billing/summary", "/api/cloudflare/subscriptions",
                "/api/cloudflare/usage"):
        r = client.get(url, headers=h)
        assert r.status_code == 400, url
        assert "аккаунт Cloudflare" in r.json()["detail"]
    # account-less routes still work — that's how the UI populates the picker
    monkeypatch.setattr(cf_client.CfClient, "accounts", lambda self: _async([{"id": "a"}]))
    assert client.get("/api/cloudflare/accounts", headers=h).json() == [{"id": "a"}]


def test_cf_error_becomes_502_with_redacted_token(monkeypatch):
    h, _ = _auth()
    _connect(h)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        # Cloudflare echoing the token back is exactly the leak `_redact` closes.
        return httpx.Response(403, json={
            "success": False,
            "errors": [{"code": 9109, "message": f"Invalid access token {TOKEN}"}],
            "result": None,
        })

    _mock_transport(monkeypatch, handler)
    r = client.get("/api/cloudflare/zones", headers=h)
    assert r.status_code == 403          # upstream status is preserved
    detail = r.json()["detail"]
    assert TOKEN not in detail and "«redacted»" in detail

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    _mock_transport(monkeypatch, boom)
    r = client.get("/api/cloudflare/zones?refresh=1", headers=h)
    assert r.status_code == 502 and TOKEN not in r.json()["detail"]


def test_envelope_is_unwrapped(monkeypatch):
    h, _ = _auth()
    _connect(h)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/zones")
        return httpx.Response(200, json={"success": True, "errors": [],
                                         "result": [{"id": "z1", "name": "example.com"}]})

    _mock_transport(monkeypatch, handler)
    r = client.get("/api/cloudflare/zones", headers=h)
    assert r.status_code == 200 and r.json() == [{"id": "z1", "name": "example.com"}]


def test_test_endpoint_reports_failure_in_body(monkeypatch):
    h, _ = _auth()
    _connect(h, account_id="")

    monkeypatch.setattr(cf_client.CfClient, "accounts",
                        lambda self: _async([{"id": "acc-9", "name": "Мой аккаунт"}]))

    probed = {}

    async def profile(self, acc):
        probed["acc"] = acc
        return {"balance": 0, "currency": "USD"}

    monkeypatch.setattr(cf_client.CfClient, "billing_profile", profile)
    body = client.post("/api/cloudflare/test", headers=h).json()
    assert body == {"ok": True, "accounts": [{"id": "acc-9", "name": "Мой аккаунт"}],
                    "error": None}
    assert probed["acc"] == "acc-9"     # falls back to the first account

    async def denied(self, acc):
        raise cf_client.CfError(403, "Cloudflare: no billing scope")

    monkeypatch.setattr(cf_client.CfClient, "billing_profile", denied)
    body = client.post("/api/cloudflare/test", headers=h).json()
    assert body["ok"] is False and body["error"] == "Cloudflare: no billing scope"


def test_summary_composes_and_degrades_on_403(monkeypatch):
    h, _ = _auth()
    _connect(h)

    monkeypatch.setattr(cf_client.CfClient, "billing_profile", lambda self, acc: _async(
        {"balance": -12.5, "currency": "USD", "payment_method": {"id": "pm_1"}}))
    monkeypatch.setattr(cf_client.CfClient, "paygo_info",
                        lambda self, acc: _async({"covered": True, "plan": "workers"}))
    monkeypatch.setattr(cf_client.CfClient, "subscriptions", lambda self, acc: _async([
        {"price": 20, "frequency": "monthly", "state": "Paid",
         "current_period_end": "2026-08-10T00:00:00Z"},
        {"price": 120, "frequency": "yearly", "state": "Paid",
         "current_period_end": "2026-08-01T00:00:00Z"},
        {"price": 999, "frequency": "monthly", "state": "Cancelled",
         "current_period_end": "2026-07-01T00:00:00Z"},
    ]))

    body = client.get("/api/cloudflare/billing/summary", headers=h).json()
    assert body["profile"] == {"balance": -12.5, "currency": "USD",
                              "payment_method_present": True}
    assert body["paygo"] == {"covered": True, "plan": "workers"}
    assert body["subscriptions_total_monthly"] == 30.0      # 20 + 120/12; cancelled skipped
    assert body["next_charge_at"] == "2026-08-01T00:00:00Z"
    assert body["degraded"] == []

    # a partially-scoped token 403s on two sub-endpoints → degraded, not 500
    async def denied(self, acc):
        raise cf_client.CfError(403, "Cloudflare: Authentication error")

    monkeypatch.setattr(cf_client.CfClient, "billing_profile", denied)
    monkeypatch.setattr(cf_client.CfClient, "paygo_info", denied)
    r = client.get("/api/cloudflare/billing/summary?refresh=1", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["degraded"] == ["billing/profile", "paygo-usage-info"]
    assert body["profile"] == {"balance": None, "currency": "",
                               "payment_method_present": False}
    assert body["subscriptions_total_monthly"] == 30.0     # the scoped part survives


def test_cache_serves_second_call_and_refresh_bypasses(monkeypatch):
    h, _ = _auth()
    _connect(h)
    calls = {"n": 0}

    async def zones(self):
        calls["n"] += 1
        return [{"id": f"z{calls['n']}"}]

    monkeypatch.setattr(cf_client.CfClient, "zones", zones)

    first = client.get("/api/cloudflare/zones", headers=h).json()
    second = client.get("/api/cloudflare/zones", headers=h).json()
    assert first == second == [{"id": "z1"}] and calls["n"] == 1

    third = client.get("/api/cloudflare/zones?refresh=1", headers=h).json()
    assert third == [{"id": "z2"}] and calls["n"] == 2

    # saving the connection drops the cache (the numbers may be another account's)
    _connect(h, account_id="acc-2")
    assert client.get("/api/cloudflare/zones", headers=h).json() == [{"id": "z3"}]
    assert calls["n"] == 3


def test_cache_is_per_account(monkeypatch):
    a, _ = _auth()
    b, _ = _auth()
    _connect(a)
    _connect(b)
    calls = {"n": 0}

    async def zones(self):
        calls["n"] += 1
        return [{"id": f"z{calls['n']}"}]

    monkeypatch.setattr(cf_client.CfClient, "zones", zones)
    assert client.get("/api/cloudflare/zones", headers=a).json() == [{"id": "z1"}]
    assert client.get("/api/cloudflare/zones", headers=b).json() == [{"id": "z2"}]
    assert client.get("/api/cloudflare/zones", headers=a).json() == [{"id": "z1"}]


def test_usage_passes_the_window_and_keys_the_cache_by_it(monkeypatch):
    h, _ = _auth()
    _connect(h)
    seen = []

    async def usage(self, acc, start=None, end=None):
        seen.append((acc, start, end))
        return {"start": start, "end": end}

    monkeypatch.setattr(cf_client.CfClient, "paygo_usage", usage)
    r = client.get("/api/cloudflare/usage?from=2026-07-01&to=2026-07-27", headers=h)
    assert r.status_code == 200 and r.json() == {"start": "2026-07-01", "end": "2026-07-27"}
    client.get("/api/cloudflare/usage?from=2026-07-01&to=2026-07-27", headers=h)  # cached
    client.get("/api/cloudflare/usage?from=2026-06-01&to=2026-06-30", headers=h)  # other key
    assert seen == [("acc-1", "2026-07-01", "2026-07-27"),
                    ("acc-1", "2026-06-01", "2026-06-30")]


def test_fernet_roundtrip():
    ct = cf_client.encrypt_token("hello")
    assert ct != "hello"
    assert cf_client.decrypt_token(ct) == "hello"
    assert cf_client.decrypt_token("") is None
    assert cf_client.decrypt_token("not-valid-fernet") is None


# ── Domains: the purchase gates (Plan B Ф3) ───────────────────
# Registration SPENDS MONEY, so each gate gets its own test: a regression here
# is not a broken screen, it is an unintended charge.
def _mock_domains(monkeypatch, *, price=9.15, currency="USD", available=True,
                  payment_method=True):
    monkeypatch.setattr(cf_client.CfClient, "billing_profile",
                        lambda self, acc: _async(
                            {"payment_method": {"id": "pm"} if payment_method else None,
                             "balance": 0, "currency": currency}))
    monkeypatch.setattr(cf_client.CfClient, "domain_check",
                        lambda self, acc, names: _async(
                            [{"name": n, "available": available, "price": price,
                              "currency": currency, "years": 1} for n in names]))
    calls: list[dict] = []
    monkeypatch.setattr(cf_client.CfClient, "register",
                        lambda self, acc, payload: (calls.append(payload),
                                                    _async({"status": "succeeded"}))[1])
    return calls


def _buy(h, **over):
    body = {"domain_name": "example-shop.com", "years": 1, "confirm": True,
            "expected_price": 9.15, "expected_currency": "USD"}
    body.update(over)
    return client.post("/api/cloudflare/domains/register", headers=h, json=body)


def test_register_requires_confirm(monkeypatch):
    h, _ = _auth()
    _connect(h)
    calls = _mock_domains(monkeypatch)
    r = _buy(h, confirm=False)
    assert r.status_code == 400 and not calls


def test_register_refuses_when_price_drifted(monkeypatch):
    h, _ = _auth()
    _connect(h)
    calls = _mock_domains(monkeypatch, price=14.40)   # registry raised the price
    r = _buy(h)                                       # user still sends the old one
    assert r.status_code == 409 and "14.4" in r.json()["detail"]
    assert not calls, "покупка не должна уходить по устаревшей цене"


def test_register_refuses_without_payment_method(monkeypatch):
    h, _ = _auth()
    _connect(h)
    calls = _mock_domains(monkeypatch, payment_method=False)
    r = _buy(h)
    assert r.status_code == 400 and "способ оплаты" in r.json()["detail"]
    assert not calls


def test_register_refuses_unknown_price(monkeypatch):
    """No price from the registry → fail closed: we cannot prove what gets charged."""
    h, _ = _auth()
    _connect(h)
    calls = _mock_domains(monkeypatch, price=None)
    assert _buy(h, expected_price=None).status_code == 400
    assert not calls


def test_register_refuses_taken_domain(monkeypatch):
    h, _ = _auth()
    _connect(h)
    calls = _mock_domains(monkeypatch, available=False)
    assert _buy(h).status_code == 400
    assert not calls


def test_register_happy_path_sends_explicit_payload(monkeypatch):
    h, _ = _auth()
    _connect(h)
    calls = _mock_domains(monkeypatch)
    r = _buy(h, years=2, contacts={"registrant": {"email": "a@b.c"}})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "succeeded" and r.json()["price"] == 9.15
    assert len(calls) == 1
    sent = calls[0]
    assert sent["domain_name"] == "example-shop.com" and sent["years"] == 2
    # auto_renew must never be sent as true unless the user asked: CF treats it as
    # standing permission to charge at renewal.
    assert sent["auto_renew"] is False
    assert sent["privacy_mode"] == "redaction"
    assert sent["contacts"] == {"registrant": {"email": "a@b.c"}}


def test_register_rejects_bogus_domain_name(monkeypatch):
    h, _ = _auth()
    _connect(h)
    calls = _mock_domains(monkeypatch)
    for bad in ("not a domain", "no-tld", "-lead.com", "a.com/../x"):
        assert _buy(h, domain_name=bad).status_code == 422, bad
    assert not calls
