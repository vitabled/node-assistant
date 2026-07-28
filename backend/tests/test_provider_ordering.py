"""Контракт заказа: `order_options` / `create_order`.

Живых вызовов нет — у каждого адаптера подменяется `_client()` на MockTransport,
поэтому здесь ловится переименованное вендором поле, а не сетевая погода.

Фикстуры RuVDS сверены с `ruvds-api-v2.yaml` (схемы `server_create`,
`server_create_response`, `vps_tariff`, `drive_tariff`, `datacenter`, `os`), DO и
Hetzner — с их публичными API v2/v1.

Главное, что проверяется помимо маппинга: **создающий запрос уходит ровно один
раз**. Заказ тратит деньги, и «повторим на всякий случай» здесь означает второй
оплаченный сервер, поэтому счётчик POST-ов стоит в каждом тесте про ошибку.
"""
import asyncio
import json

import httpx
import pytest

from app.services.hosting_providers.beget import BegetAdapter
from app.services.hosting_providers.digitalocean import DigitalOceanAdapter
from app.services.hosting_providers.hetzner import HetznerAdapter
from app.services.hosting_providers.ruvds import RuvdsAdapter
from app.services.hosting_providers.selectel import SelectelAdapter


def _wire(monkeypatch, adapter, handler):
    monkeypatch.setattr(
        adapter, "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


# ── Фикстуры ──────────────────────────────────────────────────
_RUVDS_TARIFFS = {
    # У RuVDS «тариф» — ПРАЙС-ЛИСТ: cpu/ram/ip это цены за единицу, не размеры.
    "vps": [
        {"id": 14, "name": "Regular", "cpu": 79, "ram": 195, "ip": 100, "is_active": True},
        {"id": 99, "name": "Снятый с продажи", "cpu": 1, "ram": 1, "is_active": False},
    ],
    "drive": [
        {"id": 1, "name": "HDD", "price": 5.5, "is_active": True},
        {"id": 2, "name": "SSD", "price": 12.0, "is_active": True},
        {"id": 3, "name": "NVMe (нет в ДЦ 1)", "price": 0.1, "is_active": True},
        {"id": 4, "name": "Отключённый", "price": 0.01, "is_active": False},
    ],
}
_RUVDS_DCS = {"datacenters": [
    {"id": 1, "name": "Rucloud: Россия, Королёв",
     "vps_tariffs": [14], "drive_tariffs": [1, 2]},
    {"id": 2, "name": "Equinix: Нидерланды", "vps_tariffs": [14], "drive_tariffs": [3]},
]}
_RUVDS_OS = {"os": [
    {"id": 52, "name": "Ubuntu 22.04", "is_active": True, "type": "linux",
     "os_requirements": {"cpu": 1, "ram": 1, "drive": 10}},
    {"id": 14, "name": "Windows Server 2016", "is_active": True, "type": "windows",
     "os_requirements": {"cpu": 2, "ram": 4, "drive": 50}},
    {"id": 7, "name": "Снятая ОС", "is_active": False},
]}


def _ruvds_catalog(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/tariffs"):
        return httpx.Response(200, json=_RUVDS_TARIFFS)
    if path.endswith("/datacenters"):
        return httpx.Response(200, json=_RUVDS_DCS)
    if path.endswith("/os"):
        return httpx.Response(200, json=_RUVDS_OS)
    return httpx.Response(404, json={"message": "not found"})


# ── Дефолты базы: адаптер без заказа ──────────────────────────
def test_an_adapter_without_ordering_gets_the_base_defaults():
    """Beget ничего не переопределяет — контракт обязан отвечать за него сам,
    иначе добавление заказа сломало бы все прежние адаптеры."""
    a = BegetAdapter()

    assert "order" not in a.CAPS, "заказ не заявляем там, где его нет"
    assert asyncio.run(a.order_options({"login": "l", "password": "p"})) is None

    res = asyncio.run(a.create_order({"login": "l", "password": "p"}, {"plan_id": "x"}))
    assert res["ok"] is False and res["error"] == "Провайдер не поддерживает заказ"


# ── RuVDS: конструктор ────────────────────────────────────────
def test_ruvds_order_options_map_the_price_list_and_the_constructor(monkeypatch):
    a = RuvdsAdapter()
    _wire(monkeypatch, a, _ruvds_catalog)

    opts = asyncio.run(a.order_options({"token": "t"}))

    assert opts is not None
    assert [p.id for p in opts.plans] == ["14"], "неактивный тариф в каталог не идёт"
    assert opts.plans[0].price is None, "у прайс-листа нет итоговой суммы"

    assert [r["id"] for r in opts.regions] == ["1", "2"]
    # Списки доступного в ДЦ едут с регионом: не каждый тариф есть везде.
    assert opts.regions[0]["drive_tariffs"] == ["1", "2"]

    images = {i["id"]: i for i in opts.images}
    assert set(images) == {"52", "14"}, "неактивная ОС в каталог не идёт"
    assert images["14"]["min_ram_gb"] == 4, "требования Windows едут с образом"

    # Пол конструктора — самое мягкое требование среди активных ОС; конкретная
    # ОС всё равно несёт свой минимум отдельно.
    assert opts.custom["cpu"]["min"] == 1
    assert opts.custom["disk_gb"]["min"] == 10
    # Потолка вендор не публикует — выдуманный отрезал бы реальную конфигурацию.
    assert opts.custom["ram_gb"]["max"] is None


def test_ruvds_create_order_sends_the_documented_body_and_picks_a_drive_tariff(monkeypatch):
    """Тело сверено со схемой `server_create`, а `drive_tariff_id` (обязательное
    поле, которого нет в общем контракте spec) подбирается сам."""
    sent: dict = {}
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts["n"] += 1
            sent.update(json.loads(request.content))
            return httpx.Response(200, json={
                "virtual_server_id": 6732, "cost_rub": 320.0,
                # Пароль в ответе есть — наружу он попасть не должен.
                "password": "sup3r-s3cret",
            })
        return _ruvds_catalog(request)

    a = RuvdsAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order({"token": "t"}, {
        "plan_id": "14", "region": "1", "image": "52", "name": "SQLSRV-01",
        "cpu": 2, "ram_gb": 4, "disk_gb": 40, "period": "year",
    }))

    assert res["ok"] is True and res["id"] == "6732"
    assert res["price"] == pytest.approx(320.0) and res["currency"] == "RUB"
    assert "sup3r-s3cret" not in str(res), "пароль сервера наружу не отдаём"

    assert posts["n"] == 1, "создающий запрос ровно один"
    assert sent == {
        "datacenter": 1, "tariff_id": 14, "os_id": 52, "payment_period": 5,
        "cpu": 2, "ram": 4.0, "drive": 40,
        # Самый дешёвый АКТИВНЫЙ из доступных в ДЦ 1: NVMe дешевле, но его в
        # этом ДЦ нет, а отключённый не берём вовсе.
        "drive_tariff_id": 1,
        "ip": 1, "computer_name": "SQLSRV-01",
    }


def test_ruvds_refuses_before_the_network_when_the_constructor_is_empty(monkeypatch):
    """Незаполненный конструктор — отказ на нашей стороне: пустой POST у RuVDS
    всё равно 400, но лишний запрос к чужому API незачем."""
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts["n"] += 1
        return _ruvds_catalog(request)

    a = RuvdsAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order({"token": "t"}, {"plan_id": "14", "region": "1"}))

    assert res["ok"] is False and "не заполнено" in res["error"]
    assert posts["n"] == 0


# ── DigitalOcean: фиксированные размеры ───────────────────────
def test_digitalocean_order_options_have_no_constructor(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/sizes"):
            return httpx.Response(200, json={"sizes": [
                {"slug": "s-1vcpu-1gb", "description": "Basic", "vcpus": 1,
                 "memory": 1024, "disk": 25, "price_monthly": 6.0,
                 "available": True, "regions": ["ams3", "nyc1"]},
                {"slug": "gone", "available": False},
            ]})
        if p.endswith("/regions"):
            return httpx.Response(200, json={"regions": [
                {"slug": "ams3", "name": "Amsterdam 3", "available": True},
                {"slug": "old", "name": "Retired", "available": False},
            ]})
        return httpx.Response(200, json={"images": [
            {"slug": "ubuntu-22-04-x64", "distribution": "Ubuntu", "name": "22.04 x64"},
        ]})

    a = DigitalOceanAdapter()
    _wire(monkeypatch, a, handler)
    opts = asyncio.run(a.order_options({"token": "do"}))

    assert opts is not None and opts.custom is None, "у DO только готовые size"
    assert [p.id for p in opts.plans] == ["s-1vcpu-1gb"], "недоступный size отсеян"
    assert opts.plans[0].price == pytest.approx(6.0)
    # `memory` у DO в МЕГАбайтах — на этом легко ошибиться.
    assert "1 ГБ RAM" in opts.plans[0].specs
    assert [r["id"] for r in opts.regions] == ["ams3"]
    assert opts.images[0]["id"] == "ubuntu-22-04-x64"


def test_digitalocean_create_order_sends_size_and_returns_the_droplet(monkeypatch):
    sent: dict = {}
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        posts["n"] += 1
        sent.update(json.loads(request.content))
        # Реальный ответ — 202 Accepted: дроплет ещё в очереди.
        return httpx.Response(202, json={"droplet": {
            "id": 4242, "name": "web-1", "status": "new",
            "size": {"price_monthly": 6.0},
        }})

    a = DigitalOceanAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order({"token": "do"}, {
        "plan_id": "s-1vcpu-1gb", "region": "ams3",
        "image": "ubuntu-22-04-x64", "name": "web-1",
    }))

    assert res["ok"] is True and res["id"] == "4242" and res["name"] == "web-1"
    assert res["price"] == pytest.approx(6.0) and res["currency"] == "USD"
    assert posts["n"] == 1
    # У DO поле называется `size`, а не `plan_id`/`server_type`.
    assert sent == {"name": "web-1", "region": "ams3",
                    "size": "s-1vcpu-1gb", "image": "ubuntu-22-04-x64"}


# ── Hetzner: фиксированные типы ───────────────────────────────
def test_hetzner_order_options_take_the_cheapest_location_price(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/server_types"):
            return httpx.Response(200, json={"server_types": [
                {"name": "cx22", "description": "CX22", "cores": 2, "memory": 4,
                 "disk": 40, "architecture": "x86", "deprecated": False,
                 "prices": [{"price_monthly": {"gross": "4.51"}},
                            {"price_monthly": {"gross": "3.79"}}]},
                {"name": "cx11", "deprecated": True},
            ]})
        if p.endswith("/locations"):
            return httpx.Response(200, json={"locations": [
                {"name": "fsn1", "city": "Falkenstein", "country": "DE"}]})
        return httpx.Response(200, json={"images": [
            {"id": 67794396, "name": "ubuntu-22.04", "description": "Ubuntu 22.04"}]})

    a = HetznerAdapter()
    _wire(monkeypatch, a, handler)
    opts = asyncio.run(a.order_options({"token": "h"}))

    assert opts is not None and opts.custom is None
    assert [p.id for p in opts.plans] == ["cx22"], "deprecated тип не предлагаем"
    # Цена зависит от локации — в каталоге показываем минимальную.
    assert opts.plans[0].price == pytest.approx(3.79)
    assert opts.regions[0]["id"] == "fsn1"
    # id образа — имя: оно переживает пересборку образа вендором.
    assert opts.images[0]["id"] == "ubuntu-22.04"


def test_hetzner_create_order_sends_server_type_and_hides_the_root_password(monkeypatch):
    sent: dict = {}
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        posts["n"] += 1
        sent.update(json.loads(request.content))
        return httpx.Response(201, json={
            "server": {"id": 77, "name": "node-1",
                       "server_type": {"prices": [{"price_monthly": {"gross": "3.79"}}]}},
            "root_password": "r00t-p4ss",
        })

    a = HetznerAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order({"token": "h"}, {
        "plan_id": "cx22", "region": "fsn1", "image": "ubuntu-22.04", "name": "node-1",
    }))

    assert res["ok"] is True and res["id"] == "77"
    assert res["price"] == pytest.approx(3.79) and res["currency"] == "EUR"
    assert "r00t-p4ss" not in str(res), "root-пароль наружу не отдаём"
    assert posts["n"] == 1
    assert sent == {"name": "node-1", "server_type": "cx22",
                    "location": "fsn1", "image": "ubuntu-22.04"}


# ── Отказ провайдера ──────────────────────────────────────────
@pytest.mark.parametrize("adapter, creds, spec", [
    (RuvdsAdapter(), {"token": "s3cret-ruvds"},
     {"plan_id": "14", "region": "1", "image": "52", "name": "n",
      "cpu": 2, "ram_gb": 4, "disk_gb": 40, "drive_tariff_id": 1}),
    (DigitalOceanAdapter(), {"token": "s3cret-do"},
     {"plan_id": "s-1vcpu-1gb", "region": "ams3", "image": "img", "name": "n"}),
    (HetznerAdapter(), {"token": "s3cret-hz"},
     {"plan_id": "cx22", "region": "fsn1", "image": "img", "name": "n"}),
])
def test_a_refused_order_is_reported_once_and_without_the_secret(
        monkeypatch, adapter, creds, spec):
    """4xx — это ответ, а не повод повторить: повтор рискует оплатить второй
    сервер, если первый всё же был создан."""
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return _ruvds_catalog(request)
        posts["n"] += 1
        return httpx.Response(422, json={
            "message": "not enough money",
            "error": {"message": "resource_unavailable"},
        })

    _wire(monkeypatch, adapter, handler)
    res = asyncio.run(adapter.create_order(creds, spec))

    assert res["ok"] is False and res["error"]
    assert posts["n"] == 1, "ретраев у создающего запроса быть не должно"
    assert creds["token"] not in res["error"], "секрет в текст ошибки не попадает"
    # Причина словами вендора — «нет денег» и «нет ресурса» лечатся по-разному.
    assert "money" in res["error"] or "resource_unavailable" in res["error"]


@pytest.mark.parametrize("adapter, creds", [
    (RuvdsAdapter(), {"token": "s3cret-ruvds"}),
    (DigitalOceanAdapter(), {"token": "s3cret-do"}),
    (HetznerAdapter(), {"token": "s3cret-hz"}),
])
def test_a_dead_vendor_never_raises_out_of_the_ordering_methods(monkeypatch, adapter, creds):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"connection refused for {creds['token']}")

    _wire(monkeypatch, adapter, handler)

    assert asyncio.run(adapter.order_options(creds)) is None
    res = asyncio.run(adapter.create_order(creds, {
        "plan_id": "p", "region": "r", "image": "i", "name": "n",
        "cpu": 1, "ram_gb": 1, "disk_gb": 10, "drive_tariff_id": 1,
    }))
    assert res["ok"] is False and creds["token"] not in res["error"]


# ── Selectel: честный отказ вместо кнопки-пустышки ────────────
def test_selectel_refuses_the_order_instead_of_pretending():
    """Конструктор у Selectel есть, но создаются серверы через OpenStack с
    ПРОЕКТНЫМИ кредами (адаптер `openstack`), а не по токену аккаунта."""
    a = SelectelAdapter()

    assert "order" not in a.CAPS, "кнопка, которая ничего не создаёт, хуже её отсутствия"
    res = asyncio.run(a.create_order({"token": "s"}, {"cpu": 4, "ram_gb": 8}))
    assert res["ok"] is False
    assert res["error"] == "Selectel не отдаёт заказ через публичный API — оформите в панели"

    # Границы конструктора остаются справочными: потолок вендор не публикует.
    opts = asyncio.run(a.order_options({"token": "s"}))
    assert opts is not None and opts.plans == []
    assert opts.custom["cpu"]["min"] == 1 and opts.custom["cpu"]["max"] is None
