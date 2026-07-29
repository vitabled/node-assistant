"""Заказ у OpenStack (VK Cloud, Procloud) и Reg.ru CloudVPS.

Живых вызовов нет: `_client()` подменяется на MockTransport, поэтому здесь
ловится наша ошибка сборки тела и переименованное вендором поле, а не сетевая
погода.

Два свойства важнее маппинга:
- **создающий запрос уходит ровно один раз** — заказ тратит деньги, и «повторим
  на всякий случай» здесь означает второй оплаченный сервер;
- **отсутствие цены — это норма для OpenStack**: у flavor'а стоимости нет вовсе,
  и каталог обязан вернуться без неё, а не притвориться нулём. Маршрут покупки
  такой случай проводит через отдельное подтверждение пользователя.
"""
import asyncio

import httpx
import pytest

from app.services.hosting_providers.openstack import OpenStackAdapter
from app.services.hosting_providers.regru import RegruCloudVps


def _wire(monkeypatch, adapter, handler, *, allow_host=True):
    monkeypatch.setattr(
        adapter, "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    if allow_host:
        # Адрес Keystone в тесте вымышленный, а гард резолвит хост по-настоящему —
        # иначе тест зависел бы от DNS. Сам ОТКАЗ гарда проверяется отдельным
        # тестом, где подмены нет.
        from app.services import net_guard
        monkeypatch.setattr(net_guard, "is_safe_url", lambda *_a, **_k: True)


_OS_CREDS = {"auth_url": "https://keystone.example.com", "username": "u",
             "password": "p", "project_id": "proj", "domain": "Default"}

# Каталог Keystone: адаптер обязан брать compute-endpoint ОТСЮДА, а не хардкодить.
_CATALOG = {"token": {"catalog": [{
    "type": "compute", "name": "nova",
    "endpoints": [{"interface": "public", "url": "https://nova.example.com/v2.1"}],
}]}}

_FLAVORS = {"flavors": [
    {"id": "f1", "name": "Standard-2-4", "vcpus": 2, "ram": 4096, "disk": 40},
    {"id": "f2", "name": "Отключённый", "vcpus": 1, "ram": 1024, "disk": 10,
     "OS-FLV-DISABLED:disabled": True},
]}


def _os_handler(calls, *, create_status=202, create_body=None):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(f"{request.method} {path}")
        if path.endswith("/auth/tokens"):
            return httpx.Response(201, json=_CATALOG,
                                  headers={"X-Subject-Token": "tok-123"})
        if path.endswith("/flavors/detail"):
            return httpx.Response(200, json=_FLAVORS)
        if path.endswith("/images") or "/images" in path:
            return httpx.Response(200, json={"images": [{"id": "img-1", "name": "Ubuntu 22.04"}]})
        if request.method == "POST" and path.endswith("/servers"):
            return httpx.Response(create_status, json=create_body if create_body is not None
                                  else {"server": {"id": "srv-9", "adminPass": "S3cret!"}})
        return httpx.Response(200, json={})
    return handler


def test_openstack_catalog_has_no_price_and_that_is_fine(monkeypatch):
    a = OpenStackAdapter()
    _wire(monkeypatch, a, _os_handler([]))

    opts = asyncio.run(a.order_options(_OS_CREDS))

    assert opts is not None
    ids = [p.id for p in opts.plans]
    assert ids == ["f1"], "отключённый flavor в каталог не попадает"
    assert opts.plans[0].price is None, "у flavor'а цены нет — выдумывать её нельзя"
    assert "2 vCPU" in opts.plans[0].specs and "4 ГБ RAM" in opts.plans[0].specs


def test_openstack_creates_a_server_and_hides_the_password(monkeypatch):
    calls: list[str] = []
    a = OpenStackAdapter()
    _wire(monkeypatch, a, _os_handler(calls))

    res = asyncio.run(a.create_order(_OS_CREDS, {
        "name": "web-1", "plan_id": "f1", "image": "img-1", "region": "net-7",
    }))

    assert res["ok"] is True and res["id"] == "srv-9"
    assert res["price"] is None
    # adminPass наружу не отдаём: в контракте заказа поля для секрета нет, а
    # карточка заказа персистится на клиенте.
    assert "S3cret!" not in str(res)
    assert sum(1 for c in calls if c.startswith("POST") and c.endswith("/servers")) == 1


def test_openstack_sends_flavor_image_and_network(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/tokens"):
            return httpx.Response(201, json=_CATALOG, headers={"X-Subject-Token": "t"})
        if request.method == "POST" and request.url.path.endswith("/servers"):
            seen.update(__import__("json").loads(request.content)["server"])
            return httpx.Response(202, json={"server": {"id": "srv-1"}})
        return httpx.Response(200, json={})

    a = OpenStackAdapter()
    _wire(monkeypatch, a, handler)
    asyncio.run(a.create_order(_OS_CREDS, {
        "name": "n", "plan_id": "f1", "image": "img-1", "region": "net-7"}))

    assert seen["flavorRef"] == "f1" and seen["imageRef"] == "img-1"
    # Выбор сети форма присылает в `region` — единственном свободном селекторе.
    assert seen["networks"] == [{"uuid": "net-7"}]


def test_openstack_refuses_a_private_auth_url_without_touching_the_network(monkeypatch):
    """Адрес Keystone вводит пользователь — гард обязан сработать ДО запроса."""
    def trap(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"сетевой запрос не должен уходить: {request.url}")

    a = OpenStackAdapter()
    _wire(monkeypatch, a, trap, allow_host=False)   # настоящий гард
    for bad in ("http://127.0.0.1/v3", "http://169.254.169.254/", "http://10.1.2.3:5000"):
        res = asyncio.run(a.create_order({**_OS_CREDS, "auth_url": bad},
                                         {"name": "n", "plan_id": "f", "image": "i"}))
        assert res["ok"] is False, bad


def test_openstack_reports_the_vendor_reason_and_does_not_retry(monkeypatch):
    calls: list[str] = []
    a = OpenStackAdapter()
    _wire(monkeypatch, a, _os_handler(
        calls, create_status=403,
        create_body={"forbidden": {"message": "Quota exceeded for instances"}}))

    res = asyncio.run(a.create_order(_OS_CREDS, {
        "name": "n", "plan_id": "f1", "image": "img-1"}))

    assert res["ok"] is False
    creates = [c for c in calls if c.startswith("POST") and c.endswith("/servers")]
    assert len(creates) == 1, "повтор = второй оплаченный сервер"


# ── Reg.ru CloudVPS ───────────────────────────────────────────
def test_regru_orders_a_reglet_once(monkeypatch):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.method == "POST":
            return httpx.Response(201, json={"reglet": {"id": 42, "name": "vps-1"}})
        if request.url.path.endswith("/plans"):
            # Позиция опознаётся по `slug`, а цена берётся из `price_per_month`
            # (у часовой линейки месячной суммы нет — см. `_plan`).
            return httpx.Response(200, json={"plans": [
                {"slug": "cloud-2", "name": "Cloud-2", "price_per_month": 500,
                 "vcpus": 2, "memory": 2, "disk": 40}]})
        return httpx.Response(200, json={"images": [
            {"slug": "ubuntu-22-04", "distribution": "Ubuntu", "name": "22.04"}]})

    a = RegruCloudVps()
    _wire(monkeypatch, a, handler)
    creds = {"token": "t"}

    opts = asyncio.run(a.order_options(creds))
    assert opts is not None and opts.plans, "каталог тарифов должен читаться"

    assert opts.plans[0].price == 500 and opts.plans[0].period == "month"

    res = asyncio.run(a.create_order(creds, {
        "name": "vps-1", "plan_id": "cloud-2", "image": "ubuntu-22-04"}))
    assert res["ok"] is True
    assert sum(1 for c in calls if c.startswith("POST")) == 1


def test_regru_missing_fields_never_reach_the_network(monkeypatch):
    def trap(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"неполный заказ не должен уходить: {request.url}")

    a = RegruCloudVps()
    _wire(monkeypatch, a, trap)
    res = asyncio.run(a.create_order({"token": "t"}, {"name": "", "plan_id": ""}))
    assert res["ok"] is False and "не заполнено" in res["error"].lower()
