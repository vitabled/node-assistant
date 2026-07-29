"""Адаптеры IShosting / HostKey / BILLmanager / Servers.com.

Живых вызовов нет: у каждого адаптера подменяется `_client()` на MockTransport,
поэтому переименованное вендором поле ловится здесь, а сетевой сбой не красит тест.

Два требования к этой группе адаптеров проверяются отдельно, потому что нарушить
их можно молча:

- **read-only.** У IShosting и HostKey есть ручки, которые ТРАТЯТ ДЕНЬГИ. В
  транспорт вкручена ловушка: любой не-GET и любой путь оплаты роняют тест.
- **гард адреса у BILLmanager.** `base_url` вводит пользователь, поэтому
  приватный/локальный адрес обязан отвергаться ДО обращения в сеть.
"""
import asyncio
import urllib.parse

import httpx
import pytest

from app.services import net_guard
from app.services.hosting_providers import billmanager as bm
from app.services.hosting_providers.billmanager import BillmanagerAdapter
from app.services.hosting_providers.hostkey import HostkeyAdapter
from app.services.hosting_providers.ishosting import IshostingAdapter
from app.services.hosting_providers.servers_com import ServersComAdapter

# Пути, ведущие к трате денег. Ни один адаптер этой группы их не знает — ловушка
# ниже проверяет это, а не веру в комментарий.
_MONEY_PATHS = ("/pay", "deposit", "topup", "refill", "recharge")


def _wire(monkeypatch, adapter, handler):
    monkeypatch.setattr(
        adapter, "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _json(payload, status=200):
    return lambda request: httpx.Response(status, json=payload)


def _readonly(handler, seen: list):
    """Оборачивает обработчик ловушкой на любую трату денег."""

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        assert request.method == "GET", (
            f"read-only адаптер сделал {request.method} на {request.url.path}")
        low = request.url.path.lower()
        assert not any(marker in low for marker in _MONEY_PATHS), (
            f"адаптер полез на денежную ручку {request.url.path}")
        return handler(request)

    return wrapped


# ── IShosting ─────────────────────────────────────────────────
_ISH_BALANCE = {"balance": "150.25", "currency": "eur"}
_ISH_INVOICES = {"invoices": [
    {"id": 11, "date": "2026-07-01", "total": "20.00", "status": "unpaid"},
]}
_ISH_SERVICES = {"services": [
    {"id": 5, "name": "vps-nl", "status": "active", "ip": "1.2.3.4",
     "location": "NL", "price": "9.90", "paid_till": "2026-08-01"},
]}


def _ishosting_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/balance"):
        return httpx.Response(200, json=_ISH_BALANCE)
    if path.endswith("/invoice"):
        return httpx.Response(200, json=_ISH_INVOICES)
    if path.endswith("/services"):
        return httpx.Response(200, json=_ISH_SERVICES)
    raise AssertionError(f"неожиданный путь {path}")


def test_ishosting_reads_everything_and_never_pays(monkeypatch):
    seen: list[str] = []
    a = IshostingAdapter()
    _wire(monkeypatch, a, _readonly(_ishosting_handler, seen))
    creds = {"api_token": "tok-ish"}

    bal = asyncio.run(a.balance(creds))
    assert bal is not None and bal.amount == pytest.approx(150.25)
    assert bal.currency == "EUR", "валюта нормализуется в верхний регистр"

    svc = asyncio.run(a.services(creds))
    assert len(svc) == 1 and svc[0].ip == "1.2.3.4" and svc[0].name == "vps-nl"
    assert svc[0].cost == pytest.approx(9.90)

    pays = asyncio.run(a.payments(creds))
    assert [p["type"] for p in pays] == ["charge"], "счёт — это начисление"
    assert pays[0]["amount"] == pytest.approx(20.0)

    assert seen and all(entry.startswith("GET ") for entry in seen)


def test_ishosting_sends_the_token_in_its_own_header(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["token"] = request.headers.get("x-api-token")
        return httpx.Response(200, json=_ISH_BALANCE)

    a = IshostingAdapter()
    _wire(monkeypatch, a, handler)
    assert asyncio.run(a.verify({"api_token": "tok-ish"})) == (True, "")
    assert seen["token"] == "tok-ish"


# ── HostKey ───────────────────────────────────────────────────
_HK_BILLING = {
    "balance": "42.50", "currency": "eur",
    "billing": [
        {"id": 3, "name": "HK-DE-1", "status": "active", "cost": "15.00",
         "ip": "5.6.7.8", "location": "DE", "paid_till": "2026-08-10"},
    ],
}
_HK_INVOICES = {"invoices": [
    {"id": 9, "invoice_date": "2026-07-05", "total": "15.00", "status": "paid"},
]}


def _hostkey_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("billing_list"):
        return httpx.Response(200, json=_HK_BILLING)
    if path.endswith("show_invoices"):
        return httpx.Response(200, json=_HK_INVOICES)
    raise AssertionError(f"неожиданный путь {path}")


def test_hostkey_reads_everything_and_never_pays(monkeypatch):
    seen: list[str] = []
    a = HostkeyAdapter()
    _wire(monkeypatch, a, _readonly(_hostkey_handler, seen))
    creds = {"token": "tok-hk"}

    bal = asyncio.run(a.balance(creds))
    assert bal is not None and bal.amount == pytest.approx(42.5)

    # Услуги берутся из ТОГО ЖЕ billing_list — отдельного пути мы не выдумываем.
    svc = asyncio.run(a.services(creds))
    assert len(svc) == 1 and svc[0].name == "HK-DE-1" and svc[0].ip == "5.6.7.8"

    pays = asyncio.run(a.payments(creds))
    assert [p["type"] for p in pays] == ["charge"]

    assert seen and all(entry.startswith("GET ") for entry in seen)


def test_hostkey_finds_the_balance_in_a_nested_account_object(monkeypatch):
    """Остаток может лежать во вложенном объекте — рядом со списком услуг."""
    a = HostkeyAdapter()
    _wire(monkeypatch, a, _json({"account": {"balance": 7, "currency": "usd"}}))
    bal = asyncio.run(a.balance({"token": "t"}))
    assert bal is not None and bal.amount == pytest.approx(7.0)
    assert bal.currency == "USD"


# ── BILLmanager ───────────────────────────────────────────────
# Числовой ПУБЛИЧНЫЙ адрес: net_guard резолвит хост через getaddrinfo, а
# числовой литерал разбирается без DNS — тесту не нужна сеть. Никто никуда не
# ходит: транспорт подменён MockTransport.
_PANEL = "https://8.8.8.8"

_BM_USRPARAM = {"doc": {"balance_str": {"$": "1 234,56 руб."}}}
_BM_PAYMENTS = {"doc": {"elem": [
    {"paydate": {"$": "2026-07-01"}, "amount": {"$": "500.00"},
     "paymethodname": {"$": "Карта"}},
]}}


@pytest.fixture(autouse=True)
def _clean_sessions(monkeypatch):
    """Кэш сессий модульного уровня не должен протекать между тестами."""
    bm._SESSIONS.clear()
    # Если в окружении выставлен ALLOW_PRIVATE_HOSTS=1, гард был бы выключен —
    # тест обязан проверять сам гард, а не окружение.
    monkeypatch.setattr(net_guard, "_ALLOW_PRIVATE", False)
    yield
    bm._SESSIONS.clear()


def _bm_form(request: httpx.Request) -> dict:
    body = urllib.parse.parse_qs(request.content.decode())
    return {k: v[0] for k, v in body.items()}


@pytest.mark.parametrize("base_url", [
    "http://127.0.0.1",
    "http://127.0.0.1:1500/billmgr",
    "http://169.254.169.254",       # IMDS облака — самая ценная цель SSRF
    "http://10.0.0.5",
    "ftp://8.8.8.8",                # не http(s)
])
def test_billmanager_refuses_a_private_panel_before_touching_the_network(
        monkeypatch, base_url):
    def trap(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"запрос ушёл на {request.url} несмотря на гард")

    a = BillmanagerAdapter()
    _wire(monkeypatch, a, trap)
    creds = {"base_url": base_url, "username": "u", "password": "p"}

    ok, err = asyncio.run(a.verify(creds))
    assert ok is False and "недопустим" in err
    # И методы данных тоже не должны ходить в сеть.
    assert asyncio.run(a.balance(creds)) is None
    assert asyncio.run(a.payments(creds)) == []


def test_billmanager_reuses_the_session(monkeypatch):
    """Авторизация логином и паролем — самое неприятное место, чтобы упереться в
    лимиты, поэтому id сессии берётся из кэша."""
    calls = {"auth": 0, "usrparam": 0, "payment": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        form = _bm_form(request)
        func = form.get("func", "")
        calls[func] = calls.get(func, 0) + 1
        assert form.get("out") == "json", "без out=json панель отдаёт HTML"
        if func == "auth":
            assert form.get("username") == "user-1"
            return httpx.Response(200, json={"doc": {"auth": {"$id": "SESS-1"}}})
        assert form.get("auth") == "SESS-1", "id сессии едет параметром auth"
        return httpx.Response(
            200, json=_BM_USRPARAM if func == "usrparam" else _BM_PAYMENTS)

    a = BillmanagerAdapter()
    _wire(monkeypatch, a, handler)
    creds = {"base_url": _PANEL, "username": "user-1", "password": "pw-1"}

    first = asyncio.run(a.balance(creds))
    second = asyncio.run(a.balance(creds))
    pays = asyncio.run(a.payments(creds))

    assert first is not None and second is not None
    # "1 234,56 руб." — неразрывный пробел в разрядах и запятая как разделитель.
    assert first.amount == pytest.approx(1234.56) and first.currency == "RUB"
    assert calls["usrparam"] == 2 and calls["payment"] == 1
    assert calls["auth"] == 1, "второй и третий вызовы должны взять сессию из кэша"
    assert [p["type"] for p in pays] == ["topup"], "func=payment — это пополнения"
    assert pays[0]["amount"] == pytest.approx(500.0)


def test_billmanager_credentials_never_ride_in_the_query(monkeypatch):
    """В query логин с паролем осели бы в access-логе панели."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = str(request.url.query.decode())
        seen["path"] = request.url.path
        return httpx.Response(200, json={"doc": {"auth": {"$id": "S"}}})

    a = BillmanagerAdapter()
    _wire(monkeypatch, a, handler)
    ok, err = asyncio.run(a.verify(
        {"base_url": _PANEL, "username": "u", "password": "pw-secret"}))

    assert (ok, err) == (True, "")
    assert "pw-secret" not in seen["query"] and "u" not in seen["query"]
    assert seen["path"] == "/billmgr", "точка входа дописывается к адресу панели"


def test_billmanager_does_not_double_the_billmgr_suffix():
    assert bm.endpoint("https://my.firstvds.ru") == "https://my.firstvds.ru/billmgr"
    assert bm.endpoint("https://my.firstvds.ru/") == "https://my.firstvds.ru/billmgr"
    assert bm.endpoint("https://my.firstvds.ru/billmgr") == "https://my.firstvds.ru/billmgr"
    assert bm.endpoint("  ") == ""


def test_billmanager_reads_an_error_that_arrives_with_http_200(monkeypatch):
    """Отказ авторизации приезжает СТАТУСОМ 200 с `doc.error` — проверять только
    код ответа значит принять «неверный пароль» за успех."""
    a = BillmanagerAdapter()
    _wire(monkeypatch, a, _json(
        {"doc": {"error": {"$type": "badauth", "msg": {"$": "wrong password"}}}}))

    ok, err = asyncio.run(a.verify(
        {"base_url": _PANEL, "username": "u", "password": "s3cret-bm"}))

    assert ok is False and err == "неверные креды"
    assert "s3cret-bm" not in err
    assert not bm._SESSIONS, "провалившаяся авторизация не должна кэшироваться"


def test_billmanager_takes_a_single_record_that_came_as_an_object(monkeypatch):
    """Одна запись приходит объектом, а не списком из одного элемента."""
    def handler(request: httpx.Request) -> httpx.Response:
        if _bm_form(request).get("func") == "auth":
            return httpx.Response(200, json={"doc": {"auth": {"$id": "S"}}})
        return httpx.Response(200, json={"doc": {"elem": {
            "paydate": {"$": "2026-07-02"}, "amount": {"$": "100"}}}})

    a = BillmanagerAdapter()
    _wire(monkeypatch, a, handler)
    pays = asyncio.run(a.payments(
        {"base_url": _PANEL, "username": "u", "password": "p"}))

    assert len(pays) == 1 and pays[0]["amount"] == pytest.approx(100.0)


# ── Servers.com ───────────────────────────────────────────────
def test_servers_com_lists_hosts_but_admits_it_has_no_billing(monkeypatch):
    a = ServersComAdapter()
    _wire(monkeypatch, a, _json([
        {"id": "abc", "title": "dedic-1", "type": "dedicated_server",
         "status": "active", "public_ipv4_address": "9.9.9.9",
         "location_code": "ams1"},
    ]))

    svc = asyncio.run(a.services({"token": "sc"}))
    assert len(svc) == 1 and svc[0].ip == "9.9.9.9" and svc[0].region == "ams1"
    assert svc[0].cost is None, "цены в /hosts нет — не выдумываем"

    # Биллинг-ручек в публичном API нет: адаптер обязан это ЗАЯВЛЯТЬ, иначе
    # пустой ответ выглядит как сломанная синхронизация.
    # Смысл теста — «счетов и баланса в публичном API нет»; заказ появился
    # позже и к этому отношения не имеет.
    assert "balance" not in a.CAPS and "payments" not in a.CAPS
    assert asyncio.run(a.balance({"token": "sc"})) is None
    assert asyncio.run(a.payments({"token": "sc"})) == []


def test_servers_com_follows_pagination(monkeypatch):
    """Аккаунт с >100 хостов не должен молча обрезаться на первой странице."""
    pages = {"1": [{"id": str(i), "title": f"h{i}"} for i in range(100)],
             "2": [{"id": "last", "title": "h-last"}]}
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page", "1")
        seen.append(page)
        return httpx.Response(200, json=pages.get(page, []))

    a = ServersComAdapter()
    _wire(monkeypatch, a, handler)
    svc = asyncio.run(a.services({"token": "sc"}))

    assert len(svc) == 101 and svc[-1].name == "h-last"
    assert seen == ["1", "2"], "неполная страница — последняя, лишнего запроса нет"


# ── Общее поведение контракта ─────────────────────────────────
_ADAPTERS = [
    (IshostingAdapter(), {"api_token": "s3cret-ish"}),
    (HostkeyAdapter(), {"token": "s3cret-hk"}),
    (ServersComAdapter(), {"token": "s3cret-sc"}),
    (BillmanagerAdapter(),
     {"base_url": _PANEL, "username": "u", "password": "s3cret-bm"}),
]


@pytest.mark.parametrize("adapter, creds", _ADAPTERS)
def test_401_is_a_readable_refusal_without_the_secret(monkeypatch, adapter, creds):
    _wire(monkeypatch, adapter, _json({"error": "unauthorized"}, status=401))

    ok, err = asyncio.run(adapter.verify(creds))

    assert ok is False and err == "неверные креды"
    for secret in creds.values():
        assert secret not in err, "секрет не должен попадать в текст ошибки"
    # И ни один метод данных не бросает наружу — контракт base.py.
    assert asyncio.run(adapter.balance(creds)) is None
    assert asyncio.run(adapter.services(creds)) == []
    assert asyncio.run(adapter.payments(creds)) == []


@pytest.mark.parametrize("adapter, creds", _ADAPTERS)
def test_a_transport_failure_quoting_the_secret_is_redacted(monkeypatch, adapter, creds):
    """Строка ошибки httpx может процитировать запрос целиком — с кредами."""
    secret = [v for v in creds.values() if v.startswith("s3cret")][0]

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"connection refused while sending {secret}")

    _wire(monkeypatch, adapter, boom)
    ok, err = asyncio.run(adapter.verify(creds))

    assert ok is False and err
    assert secret not in err
    assert "«redacted»" in err
    # Маскировка не должна изрешетить сообщение: у BILLmanager логин бывает
    # односимвольным, и если гнать через redact() ещё и его, то «refused»
    # превращается в «ref«redacted»sed» и читать ошибку становится нечем.
    assert "connection refused while sending" in err


@pytest.mark.parametrize("adapter, creds", _ADAPTERS)
def test_empty_credentials_are_refused_without_a_request(monkeypatch, adapter, creds):
    def trap(request: httpx.Request) -> httpx.Response:
        raise AssertionError("запрос без заполненных кредов")

    _wire(monkeypatch, adapter, trap)
    ok, err = asyncio.run(adapter.verify({}))

    assert ok is False and "не заполнено" in err
    assert asyncio.run(adapter.balance({})) is None
    assert asyncio.run(adapter.services({})) == []
    assert asyncio.run(adapter.payments({})) == []


@pytest.mark.parametrize("adapter, creds", _ADAPTERS)
def test_a_non_json_answer_does_not_raise(monkeypatch, adapter, creds):
    """Провайдер за капчей/страницей обслуживания отдаёт HTML с кодом 200."""
    _wire(monkeypatch, adapter, lambda r: httpx.Response(200, text="<html>oops"))

    ok, err = asyncio.run(adapter.verify(creds))
    assert ok is False and err
    assert asyncio.run(adapter.balance(creds)) is None
    assert asyncio.run(adapter.services(creds)) == []
    assert asyncio.run(adapter.payments(creds)) == []
