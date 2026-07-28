"""Покупка сервера через адаптер провайдера: /order-options и /order.

Адаптер подменяется в реестре — живых вызовов нет. Главное, что здесь
проверяется: заказ НЕ уходит в адаптер, пока не пройдены гейты (подтверждение,
сверка цены, известная цена), потому что каждый такой вызов тратит деньги.
"""
import asyncio
import uuid as uuidlib

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import infra_billing_store as store
from app.services.hosting_providers import registry
from app.services.hosting_providers.base import ProviderAdapter

client = TestClient(app)


class FakeOrderAdapter(ProviderAdapter):
    """Минимальный адаптер, умеющий заказ. Считает вызовы create_order — тест
    падает, если гейт пропустил покупку."""

    KIND = "fake-order"
    TITLE = "Тестовый провайдер"
    CAPS = {"order"}

    def __init__(self, price=9.99):
        self.price = price
        self.orders: list[dict] = []

    async def verify(self, creds):
        return True, ""

    async def order_options(self, creds):
        return {
            "plans": [{"id": "p1", "name": "CX11", "specs": {"cpu": 1},
                       "price": self.price, "currency": "EUR", "period": "month",
                       "region": "nbg1"}],
            "regions": [{"id": "nbg1", "name": "Нюрнберг"}],
            "images": [{"id": "debian-12", "name": "Debian 12"}],
            "custom": None,
        }

    async def create_order(self, creds, spec):
        self.orders.append(spec)
        return {"ok": True, "id": "srv-1", "name": spec["name"],
                "price": self.price, "currency": "EUR", "error": ""}


def _account():
    login = f"ord-{uuidlib.uuid4().hex[:8]}"
    r = client.post("/api/auth/register", json={"login": login, "password": "pw-1"})
    body = r.json()
    return {"Authorization": f"Bearer {body['token']}"}, body["id"]


def _provider(headers, account_id, *, kind="fake-order", with_creds=True):
    """Локальная запись провайдера с адаптером и ссылкой на Хранилище.

    Провайдера заводим прямо в сторе: POST /providers ходит в Remnawave, а к
    заказу это отношения не имеет."""
    ref = ""
    if with_creds:
        created = client.post("/api/vault", headers=headers, json={
            "name": "creds", "kind": "provider_creds", "resource": kind,
            "fields": {"token": "s3cret"}})
        ref = created.json()["id"]
    puuid = uuidlib.uuid4().hex
    asyncio.run(store.upsert_provider_meta(
        puuid, account_id, adapter_kind=kind, vault_entry_id=ref, currency="EUR"))
    return puuid


@pytest.fixture()
def adapter(monkeypatch):
    a = FakeOrderAdapter()
    monkeypatch.setitem(registry.ADAPTERS, a.KIND, a)
    return a


def _order_body(**over):
    body = {"plan_id": "p1", "region": "nbg1", "image": "debian-12", "name": "web-1",
            "confirm": True, "expected_price": 9.99, "expected_currency": "EUR"}
    body.update(over)
    return body


def test_order_options_lists_plans(adapter):
    h, aid = _account()
    p = _provider(h, aid)
    r = client.get(f"/api/infra-billing/providers/{p}/order-options", headers=h)
    assert r.status_code == 200
    assert [x["id"] for x in r.json()["plans"]] == ["p1"]


def test_order_options_refuses_a_provider_without_an_adapter(adapter):
    h, aid = _account()
    p = _provider(h, aid, kind="")
    r = client.get(f"/api/infra-billing/providers/{p}/order-options", headers=h)
    assert r.status_code == 400
    assert "адаптер" in r.json()["detail"].lower()


def test_order_without_confirm_is_refused(adapter):
    h, aid = _account()
    p = _provider(h, aid)
    r = client.post(f"/api/infra-billing/providers/{p}/order", headers=h,
                    json=_order_body(confirm=False))
    assert r.status_code == 400
    assert adapter.orders == [], "без подтверждения заказ не должен уходить провайдеру"


def test_price_drift_is_a_409_and_nothing_is_ordered(adapter):
    h, aid = _account()
    p = _provider(h, aid)
    r = client.post(f"/api/infra-billing/providers/{p}/order", headers=h,
                    json=_order_body(expected_price=5.0))
    assert r.status_code == 409
    assert adapter.orders == []


def test_unknown_price_fails_closed(adapter):
    """Цены нет — доказать, что пользователь согласился на списание, нечем."""
    h, aid = _account()
    p = _provider(h, aid)
    adapter.price = None
    r = client.post(f"/api/infra-billing/providers/{p}/order", headers=h,
                    json=_order_body(expected_price=None))
    assert r.status_code == 400
    assert "цену" in r.json()["detail"].lower()
    assert adapter.orders == []


def test_missing_vault_creds_stop_the_order(adapter):
    h, aid = _account()
    p = _provider(h, aid, with_creds=False)
    r = client.post(f"/api/infra-billing/providers/{p}/order", headers=h, json=_order_body())
    assert r.status_code == 400
    assert adapter.orders == []


def test_successful_order_creates_a_local_service(adapter):
    h, aid = _account()
    p = _provider(h, aid)
    r = client.post(f"/api/infra-billing/providers/{p}/order", headers=h, json=_order_body())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] and body["id"] == "srv-1" and body["service_id"]
    assert adapter.orders and adapter.orders[0]["name"] == "web-1"

    services = client.get("/api/infra-billing/services", headers=h).json()
    row = next(s for s in services if s["id"] == body["service_id"])
    assert row["name"] == "web-1" and row["provider_uuid"] == p
    assert row["cost"] == pytest.approx(9.99) and row["billing_type"] == "fixed"


# ── Конструктор: цену даёт расчёт, а не поле плана ────────────
def test_price_comes_from_a_quote_when_the_plan_has_none(monkeypatch):
    """RuVDS-подобный случай: «тариф» — прайс-лист, готовой цены у плана нет.
    Без расчёта маршрут обязан отказать, с расчётом — сверить и пропустить."""
    from app.services.hosting_providers.base import OrderOptions, OrderPlan

    created: dict = {}

    class Quoting:
        KIND, TITLE, FIELDS = "quoting-order", "Q", []
        CAPS = {"order"}

        async def order_options(self, creds):
            # price=None — именно это раньше блокировало заказ у конструкторов.
            return OrderOptions(
                plans=[OrderPlan(id="p1", name="P1", specs="", price=None,
                                 currency="RUB", period="month", region="")],
                regions=[], images=[], custom={"cpu": {"min": 1, "max": 8, "step": 1}})

        async def quote_order(self, creds, spec):
            return {"price": 777.0, "currency": "RUB"}

        async def create_order(self, creds, spec):
            created.update(spec)
            return {"ok": True, "id": "42", "name": spec.get("name") or "",
                    "price": 777.0, "currency": "RUB", "error": ""}

    a = Quoting()
    monkeypatch.setitem(registry.ADAPTERS, a.KIND, a)
    h, aid = _account()
    puuid = _provider(h, aid, kind=a.KIND)

    # Расчёт доступен отдельной ручкой — форма показывает сумму до подтверждения.
    q = client.post(f"/api/infra-billing/providers/{puuid}/order-quote", headers=h,
                    json=_order_body(expected_price=None, expected_currency="", confirm=False))
    assert q.status_code == 200 and q.json()["price"] == 777.0

    stale = client.post(f"/api/infra-billing/providers/{puuid}/order", headers=h,
                        json=_order_body(expected_price=700.0, expected_currency="RUB"))
    assert stale.status_code == 409 and not created, "устаревшая цена не пропускается"

    ok = client.post(f"/api/infra-billing/providers/{puuid}/order", headers=h,
                     json=_order_body(expected_price=777.0, expected_currency="RUB"))
    assert ok.status_code == 200, ok.text
    assert created.get("name") == "web-1", "заказ ушёл в адаптер только после сверки цены"
