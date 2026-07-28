"""Адаптеры IONOS / OVHcloud / Infomaniak / Latitude.sh.

Живых вызовов нет: `_client()` каждого адаптера подменяется на MockTransport,
поэтому переименованное вендором поле ловится здесь, а сетевой сбой не красит тест.

Главное здесь — подпись OVHcloud: она собирается в тесте РУКАМИ по формуле из
документации и сверяется с тем, что адаптер положил в `X-Ovh-Signature`. Так
ловится опечатка в порядке склейки — единственный симптом которой на живом API
это `401`, неотличимый от неверных ключей.
"""
import asyncio
import base64
import hashlib
import time

import httpx
import pytest

from app.services.hosting_providers.infomaniak import InfomaniakAdapter
from app.services.hosting_providers.ionos import IonosAdapter
from app.services.hosting_providers.latitude import LatitudeAdapter
from app.services.hosting_providers.ovhcloud import OvhcloudAdapter, endpoint_base

_OVH_CREDS = {"application_key": "AK", "application_secret": "s3cret-AS",
              "consumer_key": "s3cret-CK", "endpoint": "eu"}
_SERVER_TIME = 1_000_000_000  # заведомо «не сейчас» — см. тест ниже


def _wire(monkeypatch, adapter, handler):
    monkeypatch.setattr(
        adapter, "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _json(payload, status=200):
    return lambda request: httpx.Response(status, json=payload)


def _ovh_handler(routes: dict):
    """Отдаёт время по /auth/time, остальное — из словаря «путь → ответ»."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/time"):
            return httpx.Response(200, text=str(_SERVER_TIME))
        payload = routes.get(request.url.path, {})
        return httpx.Response(200, json=payload)
    return handler


# ── OVHcloud: подпись ─────────────────────────────────────────
def test_ovh_signature_matches_the_documented_concatenation(monkeypatch):
    """Формула: "$1$" + sha1(AS + CK + METHOD + FULL_URL + BODY + TS через "+")."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/time"):
            return httpx.Response(200, text=str(_SERVER_TIME))
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, json={"nichandle": "ab-ovh"})

    a = OvhcloudAdapter()
    _wire(monkeypatch, a, handler)
    ok, err = asyncio.run(a.verify(_OVH_CREDS))
    assert ok is True, err

    headers = seen["headers"]
    assert seen["url"] == "https://eu.api.ovh.com/1.0/me"
    assert headers["x-ovh-application"] == "AK"
    assert headers["x-ovh-consumer"] == "s3cret-CK"

    # Независимая сборка: строка склеивается здесь заново, а не берётся из кода.
    expected = "$1$" + hashlib.sha1("+".join([
        "s3cret-AS",                          # application secret
        "s3cret-CK",                          # consumer key
        "GET",                                # метод в ВЕРХНЕМ регистре
        "https://eu.api.ovh.com/1.0/me",      # полный URL, а не путь
        "",                                   # тело GET — пустая строка
        headers["x-ovh-timestamp"],
    ]).encode()).hexdigest()
    assert headers["x-ovh-signature"] == expected


def test_ovh_signature_covers_the_query_string(monkeypatch):
    """Подписывается ПОЛНЫЙ URL: без query подпись разошлась бы с отправленной."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/time"):
            return httpx.Response(200, text=str(_SERVER_TIME))
        if request.url.path.endswith("/me/bill"):
            seen["url"] = str(request.url)
            seen["sig"] = request.headers["x-ovh-signature"]
            seen["ts"] = request.headers["x-ovh-timestamp"]
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={})

    a = OvhcloudAdapter()
    _wire(monkeypatch, a, handler)
    asyncio.run(a.payments(_OVH_CREDS))

    assert "date.from=" in seen["url"], "фильтр по дате уходит в запрос"
    expected = "$1$" + hashlib.sha1("+".join([
        "s3cret-AS", "s3cret-CK", "GET", seen["url"], "", seen["ts"],
    ]).encode()).hexdigest()
    assert seen["sig"] == expected
    # И URL не был перекодирован по дороге — иначе подпись не сошлась бы вживую.
    assert seen["url"].startswith("https://eu.api.ovh.com/1.0/me/bill?date.from=")


def test_ovh_timestamp_comes_from_auth_time_not_the_local_clock(monkeypatch):
    calls = {"time": 0}
    stamps: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/time"):
            calls["time"] += 1
            return httpx.Response(200, text=str(_SERVER_TIME))
        stamps.append(request.headers["x-ovh-timestamp"])
        return httpx.Response(200, json={})

    a = OvhcloudAdapter()
    _wire(monkeypatch, a, handler)
    asyncio.run(a.verify(_OVH_CREDS))
    asyncio.run(a.verify(_OVH_CREDS))

    assert stamps, "запрос вообще не ушёл"
    for raw in stamps:
        stamp = int(raw)
        assert abs(stamp - _SERVER_TIME) <= 2, "метка берётся из /auth/time"
        assert abs(stamp - time.time()) > 10 ** 8, "а НЕ из локальных часов"
    assert calls["time"] == 1, "разница часов кэшируется, а не запрашивается каждый раз"


def test_ovh_endpoint_map_knows_the_odd_us_domain():
    assert endpoint_base("eu") == "https://eu.api.ovh.com/1.0"
    assert endpoint_base("ca") == "https://ca.api.ovh.com/1.0"
    # ⚠️ У US отдельный домен, а не us.api.ovh.com.
    assert endpoint_base("us") == "https://api.us.ovhcloud.com/1.0"
    assert endpoint_base("") == "https://eu.api.ovh.com/1.0", "пусто → eu"
    assert endpoint_base("moon") == "", "неизвестный регион не угадываем"


def test_ovh_unknown_region_is_refused_without_a_request(monkeypatch):
    called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(200, json={})

    a = OvhcloudAdapter()
    _wire(monkeypatch, a, handler)
    ok, err = asyncio.run(a.verify({**_OVH_CREDS, "endpoint": "moon"}))

    assert ok is False and "регион" in err
    assert called["n"] == 0


def test_ovh_payments_reads_bill_details(monkeypatch):
    """/me/bill отдаёт только идентификаторы — суммы лежат в /me/bill/{id}."""
    a = OvhcloudAdapter()
    _wire(monkeypatch, a, _ovh_handler({
        "/1.0/me/bill": ["FR-1", "FR-2"],
        "/1.0/me/bill/FR-1": {
            "billId": "FR-1", "date": "2026-06-01T00:00:00+02:00",
            "priceWithTax": {"value": 12.5, "currencyCode": "EUR"},
        },
        "/1.0/me/bill/FR-2": {
            "billId": "FR-2", "date": "2026-07-01T00:00:00+02:00",
            "priceWithTax": {"value": 30.0, "currencyCode": "EUR"},
        },
    }))

    rows = asyncio.run(a.payments(_OVH_CREDS))

    assert [r["note"] for r in rows] == ["FR-2", "FR-1"], "новые счета сверху"
    assert rows[0]["amount"] == pytest.approx(30.0)
    assert rows[0]["currency"] == "EUR"
    assert {r["type"] for r in rows} == {"charge"}
    # Баланса у OVH нет — адаптер его не заявляет и не выдумывает.
    assert "balance" not in a.CAPS
    assert asyncio.run(a.balance(_OVH_CREDS)) is None


# ── IONOS: два способа авторизации ────────────────────────────
def test_ionos_uses_bearer_when_the_token_is_filled(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["path"] = request.url.path
        return httpx.Response(200, json={"items": []})

    a = IonosAdapter()
    _wire(monkeypatch, a, handler)
    ok, err = asyncio.run(a.verify({"token": "tok-ionos", "username": "u@e.com",
                                    "password": "pw"}))

    assert ok is True, err
    assert seen["auth"] == "Bearer tok-ionos", "токен главнее логина с паролем"
    assert seen["path"].endswith("/invoices")


def test_ionos_falls_back_to_basic_login(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"items": []})

    a = IonosAdapter()
    _wire(monkeypatch, a, handler)
    ok, err = asyncio.run(a.verify({"username": "u@e.com", "password": "pw"}))

    assert ok is True, err
    # Заголовок собирается здесь независимо, а не берётся у httpx.
    encoded = base64.b64encode(b"u@e.com:pw").decode()
    assert seen["auth"] == f"Basic {encoded}"


def test_ionos_without_credentials_says_what_to_fill(monkeypatch):
    called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(200, json={})

    a = IonosAdapter()
    _wire(monkeypatch, a, handler)

    # Одного логина без пароля мало — это не «половина Basic», это ничего.
    ok, err = asyncio.run(a.verify({"username": "u@e.com"}))
    assert ok is False and err == "заполните токен или логин с паролем"
    assert asyncio.run(a.payments({})) == []
    assert called["n"] == 0, "без кредов в сеть не ходим"


def test_ionos_unwraps_the_properties_envelope(monkeypatch):
    """Коллекции IONOS кладут поля записи внутрь `properties`."""
    a = IonosAdapter()
    _wire(monkeypatch, a, _json({"items": [{
        "id": "inv-1",
        "properties": {"invoiceDate": "2026-07-01", "totalGross": "120.00",
                       "currency": "eur", "documentNumber": "R-1"},
    }]}))

    rows = asyncio.run(a.payments({"token": "t"}))

    assert len(rows) == 1
    assert rows[0]["ts"] == "2026-07-01"
    assert rows[0]["amount"] == pytest.approx(120.0)
    assert rows[0]["currency"] == "EUR", "валюта нормализуется в верхний регистр"
    assert rows[0]["note"] == "R-1"
    assert rows[0]["type"] == "charge"


# ── Infomaniak ────────────────────────────────────────────────
def test_infomaniak_treats_an_error_envelope_as_a_failure(monkeypatch):
    """⚠️ Отказ приезжает HTTP 200 с `result: error` — молча съесть его нельзя."""
    a = InfomaniakAdapter()
    _wire(monkeypatch, a, _json({"result": "error",
                                 "error": {"description": "scope invoicing manquant"}}))

    ok, err = asyncio.run(a.verify({"token": "t"}))

    assert ok is False and "отклонил" in err
    assert asyncio.run(a.payments({"token": "t"})) == []


def test_infomaniak_resolves_the_account_then_reads_invoices(monkeypatch):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append(path)
        if path == "/1/account":
            return httpx.Response(200, json={"result": "success",
                                             "data": [{"id": 42}]})
        return httpx.Response(200, json={"result": "success", "data": [{
            "date": "2026-07-01", "amount_tax_incl": "9.90",
            "currency": "chf", "number": "F-1",
        }]})

    a = InfomaniakAdapter()
    _wire(monkeypatch, a, handler)
    rows = asyncio.run(a.payments({"token": "t"}))

    assert "/1/invoicing/42/invoices" in seen, "аккаунт подставляется в путь"
    assert len(rows) == 1
    assert rows[0]["amount"] == pytest.approx(9.9)
    assert rows[0]["currency"] == "CHF"
    assert rows[0]["note"] == "F-1"


def test_infomaniak_explicit_account_id_skips_the_lookup(monkeypatch):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"result": "success", "data": []})

    a = InfomaniakAdapter()
    _wire(monkeypatch, a, handler)
    asyncio.run(a.payments({"token": "t", "account_id": "77"}))

    assert seen == ["/1/invoicing/77/invoices"]


# ── Latitude.sh ───────────────────────────────────────────────
def test_latitude_reads_json_api_attributes(monkeypatch):
    """⚠️ Поля лежат в `attributes` — «плоское» чтение вернуло бы пустые строки."""
    a = LatitudeAdapter()
    _wire(monkeypatch, a, _json({"data": [{
        "id": "srv-1", "type": "servers",
        "attributes": {
            "hostname": "eu-1", "status": "on", "primary_ipv4": "1.2.3.4",
            "plan": {"name": "c2-small"},
            "region": {"city": "Frankfurt", "site": {"slug": "FRA"}},
        },
    }]}))

    svc = asyncio.run(a.services({"token": "t"}))

    assert len(svc) == 1
    assert svc[0].id == "srv-1" and svc[0].name == "eu-1"
    assert svc[0].ip == "1.2.3.4" and svc[0].region == "FRA"
    assert svc[0].kind == "c2-small"
    assert svc[0].cost is None, "цена живёт в плане — не выдумываем ноль"
    assert svc[0].status == "on"


def test_latitude_usage_becomes_charges(monkeypatch):
    a = LatitudeAdapter()
    _wire(monkeypatch, a, _json({"data": [{
        "id": "u1", "type": "usage",
        "attributes": {"period_start": "2026-07-01", "amount": "31.20",
                       "currency": "usd", "description": "bare metal"},
    }]}))

    rows = asyncio.run(a.payments({"token": "t"}))

    assert len(rows) == 1
    assert rows[0]["ts"] == "2026-07-01"
    assert rows[0]["amount"] == pytest.approx(31.2)
    assert rows[0]["currency"] == "USD"
    assert rows[0]["type"] == "charge"
    assert rows[0]["note"] == "bare metal"
    # Баланса у Latitude нет — заявлять его нельзя.
    assert "balance" not in a.CAPS
    assert asyncio.run(a.balance({"token": "t"})) is None


# ── Общее поведение контракта ─────────────────────────────────
@pytest.mark.parametrize("adapter, creds, secret", [
    (IonosAdapter(), {"token": "s3cret-ionos"}, "s3cret-ionos"),
    (OvhcloudAdapter(), dict(_OVH_CREDS), "s3cret-AS"),
    (InfomaniakAdapter(), {"token": "s3cret-ik"}, "s3cret-ik"),
    (LatitudeAdapter(), {"token": "s3cret-lat"}, "s3cret-lat"),
])
def test_401_is_a_readable_refusal_without_the_secret(monkeypatch, adapter,
                                                      creds, secret):
    _wire(monkeypatch, adapter, _json({"message": "unauthorized"}, status=401))

    ok, err = asyncio.run(adapter.verify(creds))

    assert ok is False and err == "неверные креды"
    assert secret not in err, "секрет не должен попадать в текст ошибки"
    # И ни один метод данных не бросает наружу — контракт base.py.
    assert asyncio.run(adapter.balance(creds)) is None
    assert asyncio.run(adapter.services(creds)) == []
    assert asyncio.run(adapter.payments(creds)) == []


@pytest.mark.parametrize("adapter, creds, secret", [
    (IonosAdapter(), {"token": "s3cret-ionos"}, "s3cret-ionos"),
    (OvhcloudAdapter(), dict(_OVH_CREDS), "s3cret-AS"),
    (InfomaniakAdapter(), {"token": "s3cret-ik"}, "s3cret-ik"),
    (LatitudeAdapter(), {"token": "s3cret-lat"}, "s3cret-lat"),
])
def test_a_dead_vendor_degrades_and_keeps_the_secret(monkeypatch, adapter,
                                                     creds, secret):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"no route to {request.url} ({secret})")

    _wire(monkeypatch, adapter, boom)

    ok, err = asyncio.run(adapter.verify(creds))

    assert ok is False and err
    assert secret not in err, "redact() обязан вычистить секрет из текста httpx"
    assert asyncio.run(adapter.payments(creds)) == []
