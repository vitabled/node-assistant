"""Заказ у VDSina / Beget / Veesp / IShosting.

Живых вызовов нет: у каждого адаптера подменяется `_client()` на MockTransport,
поэтому здесь ловится переименованное вендором поле, а не сетевая погода.

Что проверяется помимо маппинга:

- **создающий запрос уходит ровно один раз** — заказ тратит деньги, и «повторим
  на всякий случай» здесь означает второй оплаченный сервер;
- **заказ у IShosting не задевает пути оплаты** — на них стоит ловушка, потому
  что запрет «не платить по расписанию» у этого адаптера отдельный и старше, чем
  поддержка заказа;
- **там, где ручка не подтверждена (Veesp), отказ приходит словами и БЕЗ
  запроса** к чужому биллингу;
- **единицы**: у Beget память и диск в МЕГАБАЙТАХ, а контракт в гигабайтах —
  ошибка здесь означает сервер в 1024 раза не того размера.

Формы ответов сверены с публичными источниками вендоров: официальные proto
`LTD-Beget/{auth,vps}` (`POST /v1/auth`, `POST /v1/vps/server`,
`GET /v1/vps/configuration`, `GET /v1/vps/configurator/*`), клиент VDSina
(`GET /v1/server-group`, `/v1/server-plan/{группа}`, `/v1/datacenter`,
`/v1/template`, `POST /v1/server`) и официальный клиент is*hosting
(`GET /vps/plans`, `/vps/configs/{код}`, `POST /billing/order[/validate]`).
"""
import asyncio
import json

import httpx
import pytest

from app.services.hosting_providers import beget as beget_mod
from app.services.hosting_providers import veesp as veesp_mod
from app.services.hosting_providers.beget import BegetAdapter
from app.services.hosting_providers.ishosting import IshostingAdapter
from app.services.hosting_providers.vdsina import VdsinaAdapter
from app.services.hosting_providers.veesp import VeespAdapter


def _wire(monkeypatch, adapter, handler):
    monkeypatch.setattr(
        adapter, "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


# ═══════════════════════════════════════════════════════════════
# VDSina — тарифы лежат под группами
# ═══════════════════════════════════════════════════════════════
_VD_CREDS = {"token": "s3cret-vdsina"}

_VD_GROUPS = {"status": "ok", "data": [
    {"id": 1, "name": "VDS", "type": "vds", "active": True},
    {"id": 2, "name": "HighCPU", "type": "vds", "active": True},
    {"id": 9, "name": "Снятая группа", "active": False},
]}

_VD_PLANS = {
    "1": {"status": "ok", "data": [
        {"id": 11, "name": "VDS-1", "cost": 330.0, "full_cost": 400.0,
         "period": "month", "active": True, "enable": True, "has_params": False,
         "data": {"cpu": {"value": 1, "for": "core"},
                  "ram": {"value": 1, "for": "Gb"},
                  "disk": {"value": 30, "for": "Gb"},
                  "traff": {"value": 32, "for": "Tb"}}},
        # Снятый с продажи гасится любым из двух признаков вендора.
        {"id": 12, "name": "Снятый", "cost": 100.0, "active": False, "enable": True},
        {"id": 13, "name": "Выключенный", "cost": 100.0, "active": True, "enable": False},
    ]},
    "2": {"status": "ok", "data": [
        {"id": 21, "name": "Конструктор", "cost": 0, "period": "month",
         "active": True, "enable": True, "has_params": True,
         "params": {"cpu": {"min": 1, "max": 16, "step": 1, "cost": 100},
                    "ram": {"min": 1, "max": 64, "step": 1, "cost": 150},
                    "disk": {"min": 10, "max": 500, "step": 10, "cost": 5}}},
    ]},
}

_VD_DCS = {"status": "ok", "data": [
    {"id": 4, "name": "Амстердам", "country": "NL", "active": True},
    {"id": 5, "name": "Закрытый", "country": "RU", "active": False},
]}

_VD_TEMPLATES = {"status": "ok", "data": [
    {"id": 23, "name": "Ubuntu 22.04", "active": True, "server-plan": [11, 21],
     "limits": {"cpu": {"min": 1}, "ram": {"min": 1}, "disk": {"min": 10}}},
    {"id": 30, "name": "Windows Server", "active": True, "server-plan": [21],
     "limits": {"cpu": {"min": 2}, "ram": {"min": 4}, "disk": {"min": 50}}},
    {"id": 7, "name": "Снятый шаблон", "active": False},
]}


def _vd_catalog(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/v1/server-group":
        return httpx.Response(200, json=_VD_GROUPS)
    if path.startswith("/v1/server-plan/"):
        return httpx.Response(200, json=_VD_PLANS.get(path.rsplit("/", 1)[-1],
                                                      {"status": "ok", "data": []}))
    if path == "/v1/datacenter":
        return httpx.Response(200, json=_VD_DCS)
    if path == "/v1/template":
        return httpx.Response(200, json=_VD_TEMPLATES)
    return httpx.Response(404, json={"status": "error", "status_msg": "not found"})


def test_vdsina_order_options_walk_the_groups(monkeypatch):
    """Отдельной ручки «все тарифы» у VDSina нет — каталог собирается обходом
    активных групп, иначе он был бы пустым."""
    a = VdsinaAdapter()
    _wire(monkeypatch, a, _vd_catalog)
    opts = asyncio.run(a.order_options(_VD_CREDS))

    assert opts is not None
    assert [p.id for p in opts.plans] == ["11", "21"], "снятые тарифы не предлагаем"
    # У фиксированного тарифа сумма есть, у настраиваемого её нет вовсе.
    assert opts.plans[0].price == pytest.approx(330.0)
    assert opts.plans[0].name == "VDS · VDS-1", "имя группы важно: тарифы одноимённы"
    assert "RAM 1 Gb" in opts.plans[0].specs, "единицу берём из `for` вендора"
    assert opts.plans[1].price is None and "настраиваемый" in opts.plans[1].specs

    assert [r["id"] for r in opts.regions] == ["4"], "закрытый ДЦ не предлагаем"
    assert opts.regions[0]["name"] == "Амстердам NL"

    images = {i["id"]: i for i in opts.images}
    assert set(images) == {"23", "30"}, "снятый шаблон в каталог не идёт"
    # Совместимость и требования едут с образом.
    assert images["30"]["allowed_plans"] == ["21"]
    assert images["30"]["min_ram_gb"] == pytest.approx(4)

    assert opts.custom is not None
    assert opts.custom["cpu"] == {"min": 1, "max": 16, "step": 1}
    assert opts.custom["disk_gb"]["step"] == 10, "шаг вендора, а не наш"


def test_vdsina_create_order_sends_the_documented_body_once(monkeypatch):
    sent: dict = {}
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return _vd_catalog(request)
        assert request.url.path == "/v1/server"
        posts["n"] += 1
        sent.update(json.loads(request.content))
        # Токен идёт БЕЗ схемы «Bearer» — так требует VDSina.
        assert request.headers["authorization"] == _VD_CREDS["token"]
        return httpx.Response(200, json={"status": "ok", "data": {"id": 613517}})

    a = VdsinaAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_VD_CREDS, {
        "plan_id": "21", "region": "4", "image": "23", "name": "node-1",
        "cpu": 2, "ram_gb": 4, "disk_gb": 40,
    }))

    assert res["ok"] is True and res["id"] == "613517" and res["name"] == "node-1"
    assert posts["n"] == 1, "создающий запрос ровно один"
    assert sent == {"datacenter": 4, "server-plan": 21, "name": "node-1",
                    "template": 23, "cpu": 2, "ram": 4, "disk": 40}
    # Цены в ответе вендора нет — и мы её не называем.
    assert res["price"] is None


def test_vdsina_fixed_plan_sends_no_constructor_fields(monkeypatch):
    """Лишние поля у фиксированного тарифа вендор считает ошибкой."""
    sent: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return _vd_catalog(request)
        sent.update(json.loads(request.content))
        return httpx.Response(200, json={"status": "ok", "data": {"id": 1}})

    a = VdsinaAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_VD_CREDS, {
        "plan_id": "11", "region": "4", "image": "23", "name": "n",
    }))

    assert res["ok"] is True
    assert set(sent) == {"datacenter", "server-plan", "name", "template"}


def test_vdsina_refuses_an_incomplete_order_before_the_network(monkeypatch):
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts["n"] += 1
        return _vd_catalog(request)

    a = VdsinaAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_VD_CREDS, {"plan_id": "11", "name": "n"}))

    assert res["ok"] is False and "не заполнено" in res["error"]
    assert "дата-центр" in res["error"] and posts["n"] == 0


def test_vdsina_reports_a_refusal_once_and_without_the_token(monkeypatch):
    """Конверт VDSina несёт `status: error` при HTTP 200 — отказ обязан ловить
    разбор тела, а не статус. Отказ — это ответ, а не повод повторить."""
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return _vd_catalog(request)
        posts["n"] += 1
        return httpx.Response(200, json={
            "status": "error",
            "status_msg": f"not enough money for {_VD_CREDS['token']}"})

    a = VdsinaAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_VD_CREDS, {
        "plan_id": "11", "region": "4", "image": "23", "name": "n"}))

    assert res["ok"] is False and posts["n"] == 1
    assert "not enough money" in res["error"], "причина словами вендора"
    assert _VD_CREDS["token"] not in res["error"]


def test_vdsina_ordering_survives_a_dead_vendor(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"refused for {_VD_CREDS['token']}")

    a = VdsinaAdapter()
    _wire(monkeypatch, a, handler)

    assert asyncio.run(a.order_options(_VD_CREDS)) is None
    res = asyncio.run(a.create_order(_VD_CREDS, {
        "plan_id": "11", "region": "4", "image": "23", "name": "n"}))
    assert res["ok"] is False and _VD_CREDS["token"] not in res["error"]


# ═══════════════════════════════════════════════════════════════
# Beget — облачный API за JWT
# ═══════════════════════════════════════════════════════════════
_BG_CREDS = {"login": "user", "password": "s3cret-beget"}


@pytest.fixture(autouse=True)
def _clean_beget_sessions():
    """Кэш JWT модульного уровня — чистим, чтобы тесты не влияли друг на друга."""
    beget_mod._SESSIONS.clear()
    yield
    beget_mod._SESSIONS.clear()


# `memory`/`disk_size` — МЕГАБАЙТЫ, на этом легко ошибиться.
_BG_CONFIGS = {"configurations": [
    {"id": "cfg-1", "name": "Aqua", "cpu_count": 2, "memory": 2048,
     "disk_size": 30720, "bandwidth_public": 200, "price_day": 20.0,
     "price_month": 600.0, "available": True, "configurable": False,
     "region": "ru1", "group": "normal_cpu"},
    {"id": "cfg-2", "name": "Недоступный", "cpu_count": 8, "memory": 16384,
     "disk_size": 102400, "price_month": 4000.0, "available": False,
     "region": "ru2"},
], "configuration_groups": [{"name": "Обычный CPU", "group": "normal_cpu"}]}

_BG_SOFTWARE = {"software": [
    {"id": 160, "name": "ubuntu", "display_name": "Ubuntu", "version": "22.04",
     "is_available": True,
     "requirements": {"cpu_count": 1, "memory": 1024, "disk_size": 10240}},
    {"id": 7, "display_name": "Снятое ПО", "is_available": False},
]}

_BG_CONFIGURATOR = {"is_available": True, "settings": {
    "cpu_settings": {"range": {"min": 1, "max": 32},
                     "available_range": {"min": 1, "max": 16}, "step": 1},
    "memory_settings": {"range": {"min": 1024, "max": 65536},
                        "available_range": {"min": 1024, "max": 32768},
                        "step": 1024},
    "disk_settings": {"range": {"min": 10240, "max": 512000},
                      "available_range": {"min": 10240, "max": 256000},
                      "step": 5120},
}}

_BG_KEYS = {"keys": [{"id": 7, "name": "deploy", "fingerprint": "aa:bb"}]}


def _bg_handler(auths: list, posts: list, extra=None):
    """Каталог Beget + счётчики: вход и создание считаются отдельно."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/auth":
            auths.append(json.loads(request.content))
            return httpx.Response(200, json={"token": "jwt-1"})
        if request.method == "GET":
            assert request.headers["authorization"] == "Bearer jwt-1"
        if path == "/v1/vps/configuration":
            return httpx.Response(200, json=_BG_CONFIGS)
        if path == "/v1/vps/marketplace/software/list":
            return httpx.Response(200, json=_BG_SOFTWARE)
        if path == "/v1/vps/configurator/info":
            return httpx.Response(200, json=_BG_CONFIGURATOR)
        if path == "/v1/vps/configurator/calculation":
            assert request.url.params["params.cpu_count"] == "4"
            # ГБ контракта → МБ вендора.
            assert request.url.params["params.memory"] == "8192"
            assert request.url.params["params.disk_size"] == "51200"
            return httpx.Response(200, json={"success": {
                "price_day": 40.0, "price_month": 1200.0, "price_hour": 1.7}})
        if path == "/v1/vps/sshKey":
            return httpx.Response(200, json=_BG_KEYS if extra is None else extra)
        if path == "/v1/vps/server":
            posts.append(json.loads(request.content))
            return httpx.Response(200, json={"vps": {
                "id": "d5a1-4f", "display_name": "node 1", "hostname": "node-1",
                "ip_address": "1.2.3.4", "status": "RUNNING",
                "configuration": dict(_BG_CONFIGS["configurations"][0])}})
        if path == "/v1/vps/server/list":
            return httpx.Response(200, json={"vps": [{
                "id": "d5a1-4f", "display_name": "node 1", "ip_address": "1.2.3.4",
                "status": "RUNNING",
                "configuration": dict(_BG_CONFIGS["configurations"][0]),
            }], "total_count": 1})
        return httpx.Response(404, json={"error": {"message": "not found"}})

    return handler


def test_beget_order_options_convert_megabytes_to_gigabytes(monkeypatch):
    a = BegetAdapter()
    _wire(monkeypatch, a, _bg_handler([], []))
    opts = asyncio.run(a.order_options(_BG_CREDS))

    assert opts is not None
    assert [p.id for p in opts.plans] == ["cfg-1"], "недоступную конфигурацию не предлагаем"
    assert opts.plans[0].price == pytest.approx(600.0)
    assert "2 ГБ RAM" in opts.plans[0].specs, "memory у Beget в мегабайтах"
    assert "30 ГБ" in opts.plans[0].specs and "200 Мбит/с" in opts.plans[0].specs
    assert [r["id"] for r in opts.regions] == ["ru1"]
    assert opts.regions[0]["configuration_ids"] == ["cfg-1"]

    images = {i["id"]: i for i in opts.images}
    assert set(images) == {"160"}, "снятое ПО в каталог не идёт"
    assert images["160"]["name"] == "Ubuntu 22.04"
    assert images["160"]["min_ram_gb"] == pytest.approx(1)

    # Конструктор: берём ДОСТУПНЫЙ диапазон, а не общие границы.
    assert opts.custom["cpu"] == {"min": 1, "max": 16, "step": 1}
    assert opts.custom["ram_gb"]["max"] == pytest.approx(32)
    assert opts.custom["disk_gb"]["step"] == pytest.approx(5)


def test_beget_authenticates_once_and_reuses_the_token(monkeypatch):
    """Вход по логину и паролю — худшее место, чтобы ловить лимиты вендора."""
    auths: list = []
    a = BegetAdapter()
    _wire(monkeypatch, a, _bg_handler(auths, []))

    assert asyncio.run(a.order_options(_BG_CREDS)) is not None
    svc = asyncio.run(a.services(_BG_CREDS))

    assert len(auths) == 1, "второй вызов авторизовываться не должен"
    assert auths[0] == {"login": "user", "password": _BG_CREDS["password"]}
    assert len(svc) == 1 and svc[0].ip == "1.2.3.4"
    assert svc[0].cost == pytest.approx(600.0) and svc[0].region == "ru1"


def test_beget_does_not_hand_a_cached_token_to_a_wrong_password(monkeypatch):
    """Логин не секрет: если бы кэш ключевался им одним, чужой аккаунт панели
    получил бы готовый токен, введя правильный логин и любой пароль."""
    auths: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/auth":
            body = json.loads(request.content)
            auths.append(body["password"])
            if body["password"] != _BG_CREDS["password"]:
                return httpx.Response(200, json={"error": "INCORRECT_CREDENTIALS"})
            return httpx.Response(200, json={"token": "jwt-1"})
        return _bg_handler([], [])(request)

    a = BegetAdapter()
    _wire(monkeypatch, a, handler)

    assert len(asyncio.run(a.services(_BG_CREDS))) == 1
    stolen = asyncio.run(a.services({"login": "user", "password": "подбор"}))

    assert stolen == [], "чужой пароль не должен подхватывать чужую сессию"
    assert auths == [_BG_CREDS["password"], "подбор"], "вход обязан состояться заново"


def test_beget_quote_asks_the_vendor_for_the_constructor_price(monkeypatch):
    """У конструктора готовой суммы нет — её называет штатный расчёт, который
    ничего не создаёт."""
    posts: list = []
    a = BegetAdapter()
    _wire(monkeypatch, a, _bg_handler([], posts))

    quote = asyncio.run(a.quote_order(_BG_CREDS, {
        "plan_id": "", "cpu": 4, "ram_gb": 8, "disk_gb": 50}))

    assert quote == {"price": pytest.approx(1200.0), "currency": "RUB"}
    assert posts == [], "расчёт ничего не создаёт"
    # А у готового тарифа сумма перечитывается из каталога.
    assert asyncio.run(a.quote_order(_BG_CREDS, {"plan_id": "cfg-1"})) == {
        "price": pytest.approx(600.0), "currency": "RUB"}


def test_beget_create_order_sends_the_configuration_id_and_ssh_keys_once(monkeypatch):
    posts: list = []
    a = BegetAdapter()
    _wire(monkeypatch, a, _bg_handler([], posts))

    res = asyncio.run(a.create_order(_BG_CREDS, {
        "plan_id": "cfg-1", "region": "ru1", "image": "160", "name": "node 1"}))

    assert res["ok"] is True and res["id"] == "d5a1-4f" and res["name"] == "node 1"
    assert res["price"] == pytest.approx(600.0)
    assert len(posts) == 1, "создающий запрос ровно один"
    assert posts[0] == {
        "display_name": "node 1",
        # Имя хоста чистится: свободное имя формы вендор отвергает.
        "hostname": "node-1",
        "software": {"id": 160},
        "configuration_id": "cfg-1",
        "region": "ru1",
        # Доступ — ключами аккаунта: пароль мы не генерируем и вернуть его некуда.
        "ssh_keys": [7],
    }
    assert "password" not in posts[0]


def test_beget_constructor_order_sends_params_in_megabytes(monkeypatch):
    """Тариф и конструктор вместе слать нельзя — пустой `plan_id` включает второй."""
    posts: list = []
    a = BegetAdapter()
    _wire(monkeypatch, a, _bg_handler([], posts))

    res = asyncio.run(a.create_order(_BG_CREDS, {
        "plan_id": "", "region": "ru1", "image": "160", "name": "node 1",
        "cpu": 4, "ram_gb": 8, "disk_gb": 50}))

    assert res["ok"] is True and len(posts) == 1
    assert posts[0]["configuration_params"] == {
        "cpu_count": 4, "memory": 8192, "disk_size": 51200}
    assert "configuration_id" not in posts[0]


def test_beget_refuses_when_the_account_has_no_ssh_key(monkeypatch):
    """Сервер без способа доступа Beget не создаёт, а пароль вернуть некуда —
    отказ обязан случиться ДО создания."""
    posts: list = []
    a = BegetAdapter()
    _wire(monkeypatch, a, _bg_handler([], posts, extra={"keys": []}))

    res = asyncio.run(a.create_order(_BG_CREDS, {
        "plan_id": "cfg-1", "region": "ru1", "image": "160", "name": "node 1"}))

    assert res["ok"] is False and "SSH-ключ" in res["error"]
    assert posts == [], "ничего не создано"


def test_beget_two_factor_is_a_readable_refusal_without_the_password(monkeypatch):
    """`CODE_REQUIRED_*` — это не «неверный пароль», и путать их нельзя."""
    posts: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/auth":
            return httpx.Response(200, json={"error": "CODE_REQUIRED_TOTP"})
        if request.url.path == "/v1/vps/server":
            posts.append(1)
        return httpx.Response(200, json={})

    a = BegetAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_BG_CREDS, {
        "plan_id": "cfg-1", "region": "ru1", "image": "160", "name": "n"}))

    assert res["ok"] is False and "двухфакторная" in res["error"]
    assert _BG_CREDS["password"] not in res["error"]
    assert posts == [] and asyncio.run(a.order_options(_BG_CREDS)) is None


def test_beget_vendor_refusal_arrives_with_http_200(monkeypatch):
    """Облачный API кладёт отказ в тело; статус при этом бывает 200."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/auth":
            return httpx.Response(200, json={"token": "jwt-1"})
        if request.url.path == "/v1/vps/sshKey":
            return httpx.Response(200, json=_BG_KEYS)
        return httpx.Response(200, json={"error": {
            "code": "INSUFFICIENT_FUNDS", "message": "недостаточно средств"}})

    a = BegetAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_BG_CREDS, {
        "plan_id": "cfg-1", "region": "ru1", "image": "160", "name": "n"}))

    assert res["ok"] is False and "недостаточно средств" in res["error"]


# ═══════════════════════════════════════════════════════════════
# Veesp — заказ не подтверждён документацией
# ═══════════════════════════════════════════════════════════════
_VE_CREDS = {"email": "me@example.com", "password": "veesp-pw"}


def test_veesp_refuses_the_order_without_touching_the_panel(monkeypatch):
    """Публичная документация клиентского API закрыта проверкой браузера, и
    ручку заказа подтвердить не удалось: кнопка, которая молча ничего не создаёт
    (или создаёт не то), опаснее её отсутствия."""
    a = VeespAdapter()

    def trap(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"отказ обязан быть офлайновым, а ушёл запрос: {request.url}")

    _wire(monkeypatch, a, trap)
    res = asyncio.run(a.create_order(_VE_CREDS, {"plan_id": "x", "name": "n"}))

    assert res["ok"] is False and res["error"] == veesp_mod._ORDER_UNSUPPORTED
    assert "личном кабинете" in res["error"], "причина должна быть предметной"
    # Кнопки заказа быть не должно: каталог гейтится на CAPS.
    assert "order" not in a.CAPS
    assert asyncio.run(a.order_options(_VE_CREDS)) is None


def test_veesp_services_are_read_defensively(monkeypatch):
    """IP в этот ответ не входит (он в `/service/{id}/vms`), и выдумывать его
    нечем — колонка остаётся пустой."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/service"
        return httpx.Response(200, json={"services": [
            {"id": 42, "name": "vps-nl", "domain": "nl.example.com",
             "total": "12.50", "status": "Active", "billingcycle": "monthly",
             "next_due": "2026-08-01", "category": {"name": "Linux SSD VPS"}},
            "мусор",
        ]})

    a = VeespAdapter()
    _wire(monkeypatch, a, handler)
    svc = asyncio.run(a.services(_VE_CREDS))

    assert len(svc) == 1, "нестрочная запись отбрасывается"
    assert svc[0].id == "42" and svc[0].name == "vps-nl"
    assert svc[0].cost == pytest.approx(12.5) and svc[0].currency == "EUR"
    assert svc[0].kind == "Linux SSD VPS" and svc[0].period == "monthly"
    assert svc[0].paid_till == "2026-08-01" and svc[0].ip == ""


# ═══════════════════════════════════════════════════════════════
# IShosting — заказ выставляет счёт, но не платит
# ═══════════════════════════════════════════════════════════════
_ISH_CREDS = {"api_token": "s3cret-ish"}

_ISH_PLANS = {"plans": [
    {"code": "29_1m", "name": "VPS Lite", "cpu": 2, "ram": 4, "disk": 50,
     "price": "9.90", "currency": "eur",
     "location": {"code": "NL", "name": "Netherlands"}},
    {"code": "31_6m", "name": "VPS Pro", "cpu": 4, "ram": 8, "disk": 100,
     "price": "19.90", "location": {"code": "FI", "name": "Finland"}},
]}

_ISH_CONFIGS = {
    # Опции лежат под категориями — форма ответа у вендора не документирована,
    # поэтому проверяем ОБА варианта: с `category.code` и с именем контейнера.
    "29_1m": {"configs": [
        {"code": "ubuntu-22", "name": "Ubuntu 22.04", "category": {"code": "os"}},
        {"code": "debian-12", "name": "Debian 12", "category": {"code": "os"}},
        {"code": "GPU_310", "name": "Nvidia 310", "category": {"code": "PLATFORM_GPU"}},
    ]},
    "31_6m": {"os": [{"code": "ubuntu-22", "name": "Ubuntu 22.04"}]},
}


def _ish_catalog(request: httpx.Request) -> httpx.Response:
    """Каталог is*hosting + ЛОВУШКА на путях оплаты (см. запрет в шапке модуля).

    Запрет «не платить по расписанию» старше поддержки заказа, и заказ не должен
    был его ослабить: любой заход в оплату счёта или пополнение баланса роняет
    тест, а не тихо возвращает ответ."""
    path = request.url.path
    low = path.lower()
    assert "pay" not in low, f"адаптер полез в оплату: {path}"
    assert "balance/add" not in low, f"адаптер полез в пополнение: {path}"

    if path == "/vps/plans":
        return httpx.Response(200, json=_ISH_PLANS)
    if path.startswith("/vps/configs/"):
        return httpx.Response(200, json=_ISH_CONFIGS.get(path.rsplit("/", 1)[-1], {}))
    return httpx.Response(404, json={"error": "not found"})


@pytest.mark.parametrize("path", ["/billing/invoice/12/pay", "/billing/balance/add"])
def test_the_ishosting_payment_trap_itself_fires(path):
    """Ловушка обязана быть НЕ пустой: если бы она молчала, тесты заказа ничего
    не доказывали бы про запрет тратить деньги."""
    with pytest.raises(AssertionError):
        _ish_catalog(httpx.Request("POST", f"https://api.ishosting.com{path}"))


def test_ishosting_order_options_merge_the_per_plan_os_lists(monkeypatch):
    a = IshostingAdapter()
    _wire(monkeypatch, a, _ish_catalog)
    opts = asyncio.run(a.order_options(_ISH_CREDS))

    assert opts is not None and opts.custom is None, "конструктора у вендора нет"
    assert [p.id for p in opts.plans] == ["29_1m", "31_6m"]
    assert opts.plans[0].price == pytest.approx(9.90)
    assert opts.plans[0].currency == "EUR", "валюта нормализуется"
    # Срок оплаты зашит в код тарифа, а не приезжает отдельным полем.
    assert opts.plans[0].period == "month" and opts.plans[1].period == "half_year"
    assert "2 vCPU" in opts.plans[0].specs and "50 ГБ диск" in opts.plans[0].specs

    regions = {r["id"]: r for r in opts.regions}
    assert set(regions) == {"NL", "FI"} and regions["NL"]["name"] == "Netherlands"
    assert regions["FI"]["plans"] == ["31_6m"]

    images = {i["id"]: i for i in opts.images}
    assert set(images) == {"ubuntu-22", "debian-12"}, "не-ОС опции в образы не идут"
    # Совместимость едет с образом: Debian есть только у младшего тарифа.
    assert images["ubuntu-22"]["allowed_plans"] == ["29_1m", "31_6m"]
    assert images["debian-12"]["allowed_plans"] == ["29_1m"]


def test_ishosting_create_order_sends_additions_once_and_never_pays(monkeypatch):
    """Локация и ОС едут ДОБАВЛЕНИЯМИ, а не отдельными полями; полей оплаты в
    теле нет вовсе."""
    sent: dict = {}
    posts: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return _ish_catalog(request)
        _ish_catalog(request)  # ловушка на денежные пути работает и для POST
        posts.append(request.url.path)
        sent.update(json.loads(request.content))
        return httpx.Response(200, json={"id": 15718, "total": "9.90",
                                         "currency": "eur",
                                         "message": "Order created"})

    a = IshostingAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_ISH_CREDS, {
        "plan_id": "29_1m", "region": "NL", "image": "ubuntu-22", "name": "node-1"}))

    assert res["ok"] is True, res["error"]
    # Идентификатор заказа у вендора — номер счёта.
    assert res["id"] == "15718" and res["price"] == pytest.approx(9.90)
    assert res["currency"] == "EUR" and res["name"] == "node-1"
    assert posts == ["/billing/order"], "создающий запрос ровно один"

    item = sent["items"][0]
    assert item["action"] == "new" and item["type"] == "vps"
    assert item["plan"] == "29_1m" and item["quantity"] == 1
    assert item["additions"] == [{"code": "NL", "category": "country"},
                                 {"code": "ubuntu-22", "category": "os"}]
    assert len(item["identity"]) == 16
    assert not any("pay" in key or "balance" in key for key in item), \
        "оплату в тело заказа не кладём"


def test_ishosting_quote_validates_without_creating(monkeypatch):
    paths: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return _ish_catalog(request)
        paths.append(request.url.path)
        assert request.url.path.endswith("/validate"), "расчёт не создаёт заказ"
        return httpx.Response(200, json={"data": {"total": 9.9, "currency": "eur"}})

    a = IshostingAdapter()
    _wire(monkeypatch, a, handler)
    quote = asyncio.run(a.quote_order(_ISH_CREDS, {
        "plan_id": "29_1m", "region": "NL", "image": "ubuntu-22"}))

    assert quote == {"price": pytest.approx(9.9), "currency": "EUR"}
    assert paths == ["/billing/order/validate"]


def test_ishosting_refuses_an_incomplete_order_before_the_network(monkeypatch):
    posts: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts.append(request.url.path)
        return _ish_catalog(request)

    a = IshostingAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_ISH_CREDS, {"plan_id": "29_1m", "name": "n"}))

    assert res["ok"] is False and "не заполнено" in res["error"]
    assert "локация" in res["error"] and posts == []


def test_ishosting_reports_a_refusal_once_and_without_the_token(monkeypatch):
    posts: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return _ish_catalog(request)
        posts.append(request.url.path)
        return httpx.Response(422, json={
            "error": {"message": f"plan is not available for {_ISH_CREDS['api_token']}"}})

    a = IshostingAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_ISH_CREDS, {
        "plan_id": "29_1m", "region": "NL", "image": "ubuntu-22", "name": "n"}))

    assert res["ok"] is False and len(posts) == 1
    assert "plan is not available" in res["error"], "причина словами вендора"
    assert _ISH_CREDS["api_token"] not in res["error"]


def test_ishosting_answer_without_an_invoice_is_not_called_a_failure(monkeypatch):
    """Счёт мог быть выставлен — молчаливое «не получилось» тут опаснее, чем
    просьба заглянуть в панель."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return _ish_catalog(request)
        return httpx.Response(200, json={"status": "ok"})

    a = IshostingAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_ISH_CREDS, {
        "plan_id": "29_1m", "region": "NL", "image": "ubuntu-22", "name": "n"}))

    assert res["ok"] is False and "проверьте панель" in res["error"]


def test_ishosting_read_paths_fall_back_only_when_the_first_one_fails(monkeypatch):
    """Основные пути чтения не подтверждены документацией, поэтому у каждого
    есть запасной из клиента вендора — но он пробуется ТОЛЬКО при отказе."""
    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/billing/balance":
            return httpx.Response(404, json={"error": "not found"})
        if request.url.path == "/profile":
            return httpx.Response(200, json={"balance": "150.25", "currency": "usd"})
        if request.url.path == "/services":
            return httpx.Response(200, json={"services": [
                {"id": 5, "name": "vps-nl", "ip": "1.2.3.4"}]})
        raise AssertionError(f"лишний запрос: {request.url.path}")

    a = IshostingAdapter()
    _wire(monkeypatch, a, handler)

    bal = asyncio.run(a.balance(_ISH_CREDS))
    svc = asyncio.run(a.services(_ISH_CREDS))

    assert bal is not None and bal.amount == pytest.approx(150.25)
    assert bal.currency == "USD"
    assert seen == ["/billing/balance", "/profile", "/services"], \
        "запасной путь услуг не трогаем, пока основной отвечает"
    assert len(svc) == 1 and svc[0].ip == "1.2.3.4"
