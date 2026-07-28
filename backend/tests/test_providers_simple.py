"""Адаптеры Aeza / Timeweb Cloud / VDSina / NetAngels.

Живых вызовов нет: у каждого адаптера подменяется `_client()` на MockTransport,
поэтому переименованное вендором поле ловится здесь, а сетевой сбой не красит тест.
"""
import asyncio

import httpx
import pytest

from app.services.hosting_providers import registry
from app.services.hosting_providers.aeza import AezaAdapter
from app.services.hosting_providers.netangels import NetangelsAdapter
from app.services.hosting_providers.timeweb import TimewebAdapter
from app.services.hosting_providers.vdsina import VdsinaAdapter


def _wire(monkeypatch, adapter, handler):
    monkeypatch.setattr(
        adapter, "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _json(payload, status=200):
    return lambda request: httpx.Response(status, json=payload)


def test_registry_publishes_the_new_kinds():
    kinds = set(registry.kinds())
    assert {"aeza", "timeweb", "vdsina", "netangels"} <= kinds


# ── Aeza ──────────────────────────────────────────────────────
def test_aeza_balance_and_auth_header(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("x-api-key")
        return httpx.Response(200, json={"data": {"balance": 1234.5, "currency": "RUB"}})

    a = AezaAdapter()
    _wire(monkeypatch, a, handler)
    bal = asyncio.run(a.balance({"api_key": "tok-aeza"}))

    assert bal is not None and bal.amount == pytest.approx(1234.5)
    assert seen["key"] == "tok-aeza", "ключ уходит заголовком X-API-Key"


# ── Timeweb Cloud ─────────────────────────────────────────────
def test_timeweb_balance_uses_bearer(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["path"] = request.url.path
        return httpx.Response(200, json={"finances": {"balance": 500, "currency": "RUB"}})

    a = TimewebAdapter()
    _wire(monkeypatch, a, handler)
    bal = asyncio.run(a.balance({"token": "tw"}))

    assert bal is not None and bal.amount == pytest.approx(500)
    assert seen["auth"] == "Bearer tw"
    assert "finances" in seen["path"]


# ── VDSina ────────────────────────────────────────────────────
def test_vdsina_balance(monkeypatch):
    a = VdsinaAdapter()
    _wire(monkeypatch, a, _json({"status": "ok", "data": {"balance": "42.00", "currency": "RUB"}}))
    bal = asyncio.run(a.balance({"token": "v"}))
    assert bal is not None and bal.amount == pytest.approx(42.0)


# ── NetAngels: двухшаговая авторизация ────────────────────────
def test_netangels_reuses_the_token(monkeypatch):
    """Токен живёт 24 часа — брать его на каждый запрос значит зря дёргать
    авторизацию (и упереться в лимиты)."""
    calls = {"token": 0, "account": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "gateway/token" in str(request.url):
            calls["token"] += 1
            return httpx.Response(200, json={"token": "T-1"})
        calls["account"] += 1
        assert request.headers.get("authorization") == "Bearer T-1"
        return httpx.Response(200, json={"balance": 77.5})

    a = NetangelsAdapter()
    _wire(monkeypatch, a, handler)
    creds = {"api_key": "k"}
    first = asyncio.run(a.balance(creds))
    second = asyncio.run(a.balance(creds))

    assert first is not None and second is not None
    assert first.amount == pytest.approx(77.5)
    assert calls["account"] == 2
    assert calls["token"] == 1, "второй вызов должен взять токен из кэша"


# ── Общее поведение контракта ─────────────────────────────────
@pytest.mark.parametrize("adapter, creds", [
    (AezaAdapter(), {"api_key": "s3cret-aeza"}),
    (TimewebAdapter(), {"token": "s3cret-tw"}),
    (VdsinaAdapter(), {"token": "s3cret-vd"}),
    (NetangelsAdapter(), {"api_key": "s3cret-na"}),
])
def test_401_is_a_readable_refusal_without_the_secret(monkeypatch, adapter, creds):
    _wire(monkeypatch, adapter, _json({"error": "unauthorized"}, status=401))

    ok, err = asyncio.run(adapter.verify(creds))

    assert ok is False and err
    secret = next(iter(creds.values()))
    assert secret not in err, "секрет не должен попадать в текст ошибки"
    # И ни один метод данных не бросает наружу — контракт base.py.
    assert asyncio.run(adapter.balance(creds)) is None
    assert asyncio.run(adapter.services(creds)) == []


# ── Бывшие «без API» провайдеры, получившие синхронизацию ──────
from app.services.hosting_providers.digitalocean import DigitalOceanAdapter
from app.services.hosting_providers.hetzner import HetznerAdapter
from app.services.hosting_providers.selectel import SelectelAdapter


def test_digitalocean_balance_services_payments(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/balance"):
            # DO отдаёт суммы СТРОКАМИ — на этом легко ошибиться.
            return httpx.Response(200, json={"account_balance": "25.50",
                                             "month_to_date_usage": "4.00"})
        if p.endswith("/droplets"):
            return httpx.Response(200, json={"droplets": [{
                "id": 7, "name": "web", "status": "active",
                "size": {"price_monthly": 12.0},
                "region": {"slug": "ams3"},
                "networks": {"v4": [{"type": "private", "ip_address": "10.0.0.2"},
                                     {"type": "public", "ip_address": "1.2.3.4"}]},
            }]})
        return httpx.Response(200, json={"billing_history": [
            {"description": "Invoice", "amount": "10.00", "date": "2026-07-01"},
            {"description": "Payment", "amount": "-10.00", "date": "2026-07-02"},
        ]})

    a = DigitalOceanAdapter()
    _wire(monkeypatch, a, handler)
    creds = {"token": "do"}

    bal = asyncio.run(a.balance(creds))
    assert bal is not None and bal.amount == pytest.approx(25.5) and bal.currency == "USD"

    svc = asyncio.run(a.services(creds))
    assert len(svc) == 1 and svc[0].ip == "1.2.3.4", "берём ПУБЛИЧНЫЙ адрес, не приватный"

    pays = asyncio.run(a.payments(creds))
    assert [p["type"] for p in pays] == ["charge", "topup"], "знак суммы задаёт тип"
    assert all(p["amount"] == pytest.approx(10.0) for p in pays)


def test_hetzner_lists_servers_but_admits_it_has_no_balance(monkeypatch):
    a = HetznerAdapter()
    _wire(monkeypatch, a, _json({"servers": [{
        "id": 1, "name": "node-1", "status": "running",
        "server_type": {"prices": [{"price_monthly": {"gross": "5.83"}}]},
        "public_net": {"ipv4": {"ip": "5.6.7.8"}},
        "datacenter": {"name": "fsn1-dc14"},
    }]}))

    svc = asyncio.run(a.services({"token": "h"}))
    assert len(svc) == 1 and svc[0].cost == pytest.approx(5.83) and svc[0].ip == "5.6.7.8"
    # Баланса в Cloud API нет — адаптер не должен его заявлять.
    assert "balance" not in a.CAPS
    assert asyncio.run(a.balance({"token": "h"})) is None


def test_selectel_sums_billings(monkeypatch):
    """Ответ вложенный: несколько типов биллинга, у каждого свои балансы."""
    a = SelectelAdapter()
    _wire(monkeypatch, a, _json({"status": "ok", "data": {
        "settings": {"currency": "rub"},
        "billings": [
            {"billing_type": "cloud", "final_sum": 1500},
            {"billing_type": "dedicated", "balances": [{"value": 200}, {"value": 300}]},
        ],
    }}))

    bal = asyncio.run(a.balance({"token": "s"}))
    assert bal is not None and bal.amount == pytest.approx(2000)
    assert bal.currency == "RUB", "валюта нормализуется в верхний регистр"
