"""Заказ у Timeweb Cloud, Latitude.sh и Aeza.

Живых вызовов нет: у каждого адаптера подменяется `_client()` на MockTransport,
поэтому здесь ловится переименованное вендором поле и наша собственная ошибка
сборки тела, а не сетевая погода.

Фикстуры сверены с публичными источниками вендоров: Timeweb — с их официальным
SDK (`timeweb-cloud/sdk-go`, модели `ServersPreset`/`ServersConfigurator`/
`CreateServer`), Latitude — с докой `POST /servers` и `/plans`, Aeza — с
собственной документацией вендора (`AezaGroup/dev-docs`, `t/service.md`).

Главное, что проверяется помимо маппинга: **создающий запрос уходит ровно один
раз**. Заказ тратит деньги, и «повторим на всякий случай» здесь означает второй
оплаченный сервер, поэтому счётчик POST-ов стоит в каждом тесте про отказ.
"""
import asyncio
import json

import httpx
import pytest

from app.services.hosting_providers.aeza import AezaAdapter
from app.services.hosting_providers.latitude import LatitudeAdapter
from app.services.hosting_providers.timeweb import TimewebAdapter


def _wire(monkeypatch, adapter, handler):
    monkeypatch.setattr(
        adapter, "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


# ── Timeweb Cloud ─────────────────────────────────────────────
# ⚠️ ram/disk у Timeweb ВЕЗДЕ в мегабайтах — и в тарифах, и в требованиях
# конструктора, и в теле создания.
_TW_PRESETS = {"server_presets": [
    {"id": 4212, "location": "ru-1", "price": 550.0, "cpu": 2,
     "cpu_frequency": "3.3", "ram": 2048, "disk": 30720, "disk_type": "nvme",
     "bandwidth": 200, "description": "Ultra тариф", "description_short": "Ultra-1",
     "is_allowed_local_network": True, "tags": []},
]}
_TW_CONFIGURATORS = {"server_configurators": [
    {"id": 11, "location": "ru-1", "disk_type": "nvme", "cpu_frequency": "3.3",
     "requirements": {
         "cpu_min": 1, "cpu_step": 1, "cpu_max": 8,
         "ram_min": 1024, "ram_step": 1024, "ram_max": 32768,
         "disk_min": 5120, "disk_step": 5120, "disk_max": 512000,
         "network_bandwidth_min": 100, "network_bandwidth_step": 100,
         "network_bandwidth_max": 1000,
         "gpu_min": None, "gpu_max": None, "gpu_step": None}},
    # Вторая локация нарочно с другими границами — проверяем правило склейки.
    {"id": 12, "location": "nl-1", "disk_type": "ssd", "cpu_frequency": "3.3",
     "requirements": {
         "cpu_min": 2, "cpu_step": 2, "cpu_max": 16,
         "ram_min": 2048, "ram_step": 2048, "ram_max": 65536,
         "disk_min": 10240, "disk_step": 10240, "disk_max": 1024000,
         "network_bandwidth_min": 200, "network_bandwidth_step": 100,
         "network_bandwidth_max": 1000}},
]}
_TW_OS = {"servers_os": [
    {"id": 79, "family": "linux", "name": "ubuntu", "version": "22.04",
     "description": "Ubuntu 22.04", "requirements": {
         "cpu_min": 1, "ram_min": 1024, "disk_min": 10240, "bandwidth_min": 100}},
]}


def _tw_catalog(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/presets/servers"):
        return httpx.Response(200, json=_TW_PRESETS)
    if path.endswith("/configurator/servers"):
        return httpx.Response(200, json=_TW_CONFIGURATORS)
    if path.endswith("/os/servers"):
        return httpx.Response(200, json=_TW_OS)
    return httpx.Response(404, json={"message": ["not found"]})


def test_timeweb_order_options_expose_presets_and_the_constructor(monkeypatch):
    a = TimewebAdapter()
    _wire(monkeypatch, a, _tw_catalog)

    opts = asyncio.run(a.order_options({"token": "t"}))

    assert opts is not None
    assert [p.id for p in opts.plans] == ["4212"]
    assert opts.plans[0].price == pytest.approx(550.0)
    # У тарифа локация своя — форме не нужно спрашивать её отдельно.
    assert opts.plans[0].region == "ru-1"
    # 2048 МБ = 2 ГБ, 30720 МБ = 30 ГБ: перепутать единицы значит показать
    # тариф в 1024 раза не того размера.
    assert "2 ГБ RAM" in opts.plans[0].specs
    assert "30 ГБ" in opts.plans[0].specs

    regions = {r["id"]: r for r in opts.regions}
    assert set(regions) == {"ru-1", "nl-1"}
    assert regions["ru-1"]["configurator_id"] == "11"
    assert regions["ru-1"]["preset_ids"] == ["4212"]

    assert opts.images[0]["id"] == "79"
    assert opts.images[0]["name"] == "ubuntu 22.04"
    # Требования образа едут в гигабайтах, как и весь наш контракт.
    assert opts.images[0]["min_ram_gb"] == pytest.approx(1.0)


def test_timeweb_constructor_bounds_are_merged_across_locations(monkeypatch):
    """Конфигуратор свой на каждую локацию, а схема `custom` одна: `min` —
    самый мягкий, `max` — самый щедрый, `step` — самый КРУПНЫЙ (значение,
    кратное большему шагу, подойдёт и там, где шаг мельче; наоборот — нет)."""
    a = TimewebAdapter()
    _wire(monkeypatch, a, _tw_catalog)

    custom = asyncio.run(a.order_options({"token": "t"})).custom

    assert custom["cpu"] == {"min": 1, "max": 16, "step": 2}
    # МБ → ГБ: 1024→1, 65536→64, 2048→2.
    assert custom["ram_gb"] == {"min": 1.0, "max": 64.0, "step": 2.0}
    # 5120→5, 1024000→1000, 10240→10.
    assert custom["disk_gb"] == {"min": 5.0, "max": 1000.0, "step": 10.0}


def test_timeweb_create_order_builds_the_configuration_from_cpu_ram_disk(monkeypatch):
    """Конструктор: тело несёт объект `configuration` с id конфигуратора,
    подобранного по локации, и размерами, переведёнными в МЕГАБАЙТЫ."""
    sent: dict = {}
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts["n"] += 1
            sent.update(json.loads(request.content))
            return httpx.Response(201, json={"server": {
                "id": 3456789, "name": "web-1", "status": "installing"}})
        return _tw_catalog(request)

    a = TimewebAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order({"token": "t"}, {
        "plan_id": "", "region": "ru-1", "image": "79", "name": "web-1",
        "cpu": 4, "ram_gb": 8, "disk_gb": 60,
    }))

    assert res["ok"] is True and res["id"] == "3456789"
    # `id` приезжает числом: без приведения к int получилась бы строка «3456789.0».
    assert "." not in res["id"]
    assert posts["n"] == 1, "создающий запрос ровно один"
    assert sent == {
        "name": "web-1",
        "os_id": 79,
        "configuration": {
            # Конфигуратор локации ru-1, а не первый попавшийся.
            "configurator_id": 11,
            "cpu": 4,
            "ram": 8192,     # 8 ГБ
            "disk": 61440,   # 60 ГБ
        },
        # Полосу пользователь не выбирал — берём минимальную из требований,
        # чтобы не потратить чужие деньги молча.
        "bandwidth": 100,
    }


def test_timeweb_create_order_uses_a_preset_when_plan_id_is_set(monkeypatch):
    """`preset_id` и `configuration` вендор запрещает слать вместе, поэтому
    непустой `plan_id` выигрывает, а значения конструктора игнорируются."""
    sent: dict = {}
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts["n"] += 1
            sent.update(json.loads(request.content))
            return httpx.Response(201, json={"server": {"id": 42, "name": "db-1"}})
        return _tw_catalog(request)

    a = TimewebAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order({"token": "t"}, {
        "plan_id": "4212", "region": "ru-1", "image": "79", "name": "db-1",
        "cpu": 4, "ram_gb": 8, "disk_gb": 60,
    }))

    assert res["ok"] is True and res["id"] == "42"
    assert posts["n"] == 1
    assert sent == {"name": "db-1", "os_id": 79, "preset_id": 4212}
    assert "configuration" not in sent


def test_timeweb_sends_a_custom_image_as_image_id_not_os_id(monkeypatch):
    """`os_id` и `image_id` вместе слать нельзя: числовой образ — вендорский,
    нечисловой — пользовательский."""
    sent: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            sent.update(json.loads(request.content))
            return httpx.Response(201, json={"server": {"id": 7, "name": "n"}})
        return _tw_catalog(request)

    a = TimewebAdapter()
    _wire(monkeypatch, a, handler)
    asyncio.run(a.create_order({"token": "t"}, {
        "plan_id": "4212", "image": "b3f1-my-own-image", "name": "n"}))

    assert sent["image_id"] == "b3f1-my-own-image"
    assert "os_id" not in sent


def test_timeweb_refuses_the_constructor_without_a_location(monkeypatch):
    """Локация задаёт и конфигуратор, и физическое размещение — выбрать её за
    пользователя нельзя, а лишний запрос к чужому API незачем."""
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts["n"] += 1
        return _tw_catalog(request)

    a = TimewebAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order({"token": "t"}, {
        "plan_id": "", "region": "", "image": "79", "name": "web-1",
        "cpu": 4, "ram_gb": 8, "disk_gb": 60,
    }))

    assert res["ok"] is False and "локация" in res["error"]
    assert posts["n"] == 0


def test_timeweb_still_offers_the_constructor_when_presets_fail(monkeypatch):
    """Тарифы и конструктор — независимые пути заказа: падение одного не должно
    прятать другой."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/presets/servers"):
            return httpx.Response(500, json={"message": ["boom"]})
        return _tw_catalog(request)

    a = TimewebAdapter()
    _wire(monkeypatch, a, handler)
    opts = asyncio.run(a.order_options({"token": "t"}))

    assert opts is not None and opts.plans == []
    assert opts.custom["cpu"]["min"] == 1, "конструктор остаётся доступен"


def test_timeweb_quotes_a_preset_but_never_invents_a_constructor_price(monkeypatch):
    a = TimewebAdapter()
    _wire(monkeypatch, a, _tw_catalog)

    quote = asyncio.run(a.quote_order({"token": "t"}, {"plan_id": "4212"}))
    assert quote == {"price": pytest.approx(550.0), "currency": "RUB"}

    # Формулу конструктора вендор не публикует и ручки расчёта у него нет:
    # выдуманная сумма обошла бы согласие пользователя с ценой.
    assert asyncio.run(a.quote_order({"token": "t"}, {
        "plan_id": "", "region": "ru-1", "cpu": 4, "ram_gb": 8, "disk_gb": 60})) is None


# ── Latitude.sh ───────────────────────────────────────────────
_LAT_PLANS = {"data": [
    {"id": "plan_xyz", "type": "plans", "attributes": {
        "name": "c2-small-x86", "slug": "c2-small-x86",
        "specs": {"cpu": {"type": "Intel Xeon E-2276G", "cores": 6, "clock": "3.8GHz"},
                  "memory": {"total": 32},
                  "drives": [{"count": 2, "size": "960GB", "type": "SSD"}]},
        "regions": [
            {"name": "Ashburn", "locations": {"available": ["ASH"], "in_stock": ["ASH"]},
             "pricing": {"USD": {"hour": 0.5, "month": 250.0, "year": 2500.0}},
             "stock_level": "high"},
            {"name": "Sao Paulo", "locations": {"available": ["SAO"], "in_stock": []},
             "pricing": {"USD": {"hour": 0.4, "month": 199.0}},
             "stock_level": "low"},
        ]}},
]}
_LAT_REGIONS = {"data": [
    {"id": "loc_1", "type": "regions", "attributes": {
        "slug": "ASH", "name": "Ashburn", "facility": "DC1",
        "country": {"name": "United States", "slug": "us"}}},
]}
_LAT_OS = {"data": [
    {"id": "os_1", "type": "operating_systems", "attributes": {
        "slug": "ubuntu_22_04_x64_lts", "name": "Ubuntu 22.04 LTS", "distro": "ubuntu"}},
]}
_LAT_PROJECTS = {"data": [
    {"id": "proj_ONE", "type": "projects",
     "attributes": {"slug": "my-project", "name": "My project"}},
]}


def _lat_catalog(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/plans/operating_systems"):
        return httpx.Response(200, json=_LAT_OS)
    if path.endswith("/plans"):
        return httpx.Response(200, json=_LAT_PLANS)
    if path.endswith("/regions"):
        return httpx.Response(200, json=_LAT_REGIONS)
    if path.endswith("/projects"):
        return httpx.Response(200, json=_LAT_PROJECTS)
    return httpx.Response(404, json={"errors": [{"detail": "not found"}]})


def test_latitude_order_options_show_the_cheapest_site_price(monkeypatch):
    a = LatitudeAdapter()
    _wire(monkeypatch, a, _lat_catalog)

    opts = asyncio.run(a.order_options({"token": "l"}))

    assert opts is not None and opts.custom is None, "bare metal: конструктора нет"
    # id плана — СЛАГ: атрибут `plan` при заказе слаг, а не opaque-id JSON:API.
    assert [p.id for p in opts.plans] == ["c2-small-x86"]
    # Цена зависит от площадки — в каталоге показываем минимальную.
    assert opts.plans[0].price == pytest.approx(199.0)
    assert opts.plans[0].currency == "USD"
    assert "32 ГБ RAM" in opts.plans[0].specs
    assert opts.regions[0]["id"] == "ASH"
    assert opts.images[0]["id"] == "ubuntu_22_04_x64_lts"


def test_latitude_quote_prices_the_chosen_site_not_the_cheapest(monkeypatch):
    """Иначе пользователь подтверждал бы самую дешёвую площадку, а платил за
    выбранную."""
    a = LatitudeAdapter()
    _wire(monkeypatch, a, _lat_catalog)

    quote = asyncio.run(a.quote_order({"token": "l"}, {
        "plan_id": "c2-small-x86", "region": "ASH", "period": "month"}))
    assert quote == {"price": pytest.approx(250.0), "currency": "USD"}

    # Почасовая оплата — другой ключ в `pricing`, а не пересчёт месячной.
    hourly = asyncio.run(a.quote_order({"token": "l"}, {
        "plan_id": "c2-small-x86", "region": "ASH", "period": "hour"}))
    assert hourly == {"price": pytest.approx(0.5), "currency": "USD"}

    # Площадки, на которой плана нет, вендор не оценивает — и мы не выдумываем.
    assert asyncio.run(a.quote_order({"token": "l"}, {
        "plan_id": "c2-small-x86", "region": "TYO"})) is None


def test_latitude_create_order_sends_plan_site_and_os_in_the_jsonapi_envelope(monkeypatch):
    sent: dict = {}
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts["n"] += 1
            sent.update(json.loads(request.content))
            return httpx.Response(201, json={"data": {
                "id": "sv_W6Q2D9xGqKLpr", "type": "servers",
                "attributes": {"hostname": "bm-1", "status": "deploying"}}})
        return _lat_catalog(request)

    a = LatitudeAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order({"token": "l"}, {
        "plan_id": "c2-small-x86", "region": "ASH",
        "image": "ubuntu_22_04_x64_lts", "name": "bm-1", "period": "month",
    }))

    assert res["ok"] is True and res["id"] == "sv_W6Q2D9xGqKLpr"
    assert res["name"] == "bm-1"
    assert posts["n"] == 1
    assert sent == {"data": {"type": "servers", "attributes": {
        # `project` в общем контракте spec нет — резолвится по аккаунту.
        "project": "proj_ONE",
        "plan": "c2-small-x86",
        "site": "ASH",
        "operating_system": "ubuntu_22_04_x64_lts",
        "hostname": "bm-1",
        "billing": "monthly",
    }}}


def test_latitude_refuses_when_the_account_has_several_projects(monkeypatch):
    """«Первый попавшийся» проект — это молча выбранная чужая корзина: сервер
    ушёл бы не туда, и заметили бы это уже после оплаты."""
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts["n"] += 1
        if request.url.path.endswith("/projects"):
            return httpx.Response(200, json={"data": [
                {"id": "proj_A", "attributes": {"slug": "alpha"}},
                {"id": "proj_B", "attributes": {"slug": "beta"}},
            ]})
        return _lat_catalog(request)

    a = LatitudeAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order({"token": "l"}, {
        "plan_id": "c2-small-x86", "region": "ASH",
        "image": "ubuntu_22_04_x64_lts", "name": "bm-1"}))

    assert res["ok"] is False
    assert "несколько проектов" in res["error"] and "alpha" in res["error"]
    assert posts["n"] == 0, "до создающего запроса дело не доходит"


# ── Aeza ──────────────────────────────────────────────────────
_AEZA_PRODUCTS = {"items": [
    {"id": 3, "name": "EPs-1", "isPrivate": False, "installPrice": 0,
     "group": {"id": 1, "name": "Нидерланды"},
     "prices": {"hour": {"value": 12, "suffix": "₽"},
                "month": {"value": 550, "suffix": "₽"}}},
    {"id": 9, "name": "Персональный тариф", "isPrivate": True,
     "prices": {"month": {"value": 1}}},
], "total": 2}
_AEZA_OS = {"items": [{"id": 25, "name": "Ubuntu 20.04"}], "total": 1}


def _aeza_catalog(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/services/products"):
        return httpx.Response(200, json=_AEZA_PRODUCTS)
    if path.endswith("/os"):
        return httpx.Response(200, json=_AEZA_OS)
    return httpx.Response(404, json={"error": {"message": "not found"}})


def test_aeza_order_options_skip_private_products(monkeypatch):
    a = AezaAdapter()
    _wire(monkeypatch, a, _aeza_catalog)

    opts = asyncio.run(a.order_options({"api_key": "k"}))

    assert opts is not None
    assert [p.id for p in opts.plans] == ["3"], "персональный тариф не предлагаем"
    assert opts.plans[0].price == pytest.approx(550.0)
    assert opts.images[0]["id"] == "25"
    # Локация зашита в продукт, отдельной ручки локаций у вендора нет.
    assert opts.regions == []
    assert opts.custom is None


def test_aeza_quote_matches_the_term_that_will_be_ordered(monkeypatch):
    """Цена читается за ТОТ срок, который уйдёт в тело заказа: подставить
    часовую цену вместо месячной значит показать не ту сумму."""
    a = AezaAdapter()
    _wire(monkeypatch, a, _aeza_catalog)

    assert asyncio.run(a.quote_order({"api_key": "k"}, {
        "plan_id": "3", "period": "month"}))["price"] == pytest.approx(550.0)
    assert asyncio.run(a.quote_order({"api_key": "k"}, {
        "plan_id": "3", "period": "hour"}))["price"] == pytest.approx(12.0)
    # Срока, которого вендор не назвал, в карте цен нет — цены тоже нет.
    assert asyncio.run(a.quote_order({"api_key": "k"}, {
        "plan_id": "3", "period": "year"})) is None


def test_aeza_create_order_sends_one_service_without_auto_renewal(monkeypatch):
    sent: dict = {}
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts["n"] += 1
            sent.update(json.loads(request.content))
            # Услуга создаётся асинхронно: сразу после заказа список пуст.
            return httpx.Response(200, json={"id": 777, "createdServiceIds": []})
        return _aeza_catalog(request)

    a = AezaAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order({"api_key": "k"}, {
        "plan_id": "3", "image": "25", "name": "i love api", "period": "month"}))

    assert res["ok"] is True
    # Идентификатора услуги ещё нет — возвращаем идентификатор ЗАКАЗА, а не пустоту.
    assert res["id"] == "777"
    assert posts["n"] == 1
    assert sent == {
        "count": 1,                 # никогда не больше одной услуги
        "term": "month",
        "name": "i love api",
        "productId": 3,
        "parameters": {"os": 25},
        # У вендора умолчание true; автопродление списывает деньги позже без
        # нового подтверждения, поэтому по умолчанию выключено.
        "autoProlong": False,
        "method": "balance",
    }


def test_aeza_prefers_the_created_service_id_when_it_is_already_there(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"id": 777, "createdServiceIds": [4242]})
        return _aeza_catalog(request)

    a = AezaAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order({"api_key": "k"}, {
        "plan_id": "3", "image": "25", "name": "srv"}))

    assert res["ok"] is True and res["id"] == "4242"


def test_aeza_reports_a_body_error_that_arrived_with_http_200(monkeypatch):
    """Aeza умеет ответить 200 с телом-ошибкой — для заказа это разница между
    «куплено» и «не куплено»."""
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts["n"] += 1
            return httpx.Response(200, json={"error": {"message": "not enough money"}})
        return _aeza_catalog(request)

    a = AezaAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order({"api_key": "k"}, {
        "plan_id": "3", "image": "25", "name": "srv"}))

    assert res["ok"] is False and "not enough money" in res["error"]
    assert posts["n"] == 1


# ── Общее: отказ вендора и молчание про секреты ───────────────
_REFUSALS = [
    (TimewebAdapter, {"token": "s3cret-tw"},
     {"plan_id": "4212", "image": "79", "name": "n"},
     _tw_catalog, {"message": ["not enough money"]}),
    (LatitudeAdapter, {"token": "s3cret-lat"},
     {"plan_id": "c2-small-x86", "region": "ASH",
      "image": "ubuntu_22_04_x64_lts", "name": "n"},
     _lat_catalog, {"errors": [{"title": "unprocessable",
                                "detail": "not enough money"}]}),
    (AezaAdapter, {"api_key": "s3cret-aeza"},
     {"plan_id": "3", "image": "25", "name": "n"},
     _aeza_catalog, {"error": {"message": "not enough money"}}),
]


@pytest.mark.parametrize("factory, creds, spec, catalog, body", _REFUSALS)
def test_a_refused_order_is_reported_once_and_without_the_secret(
        monkeypatch, factory, creds, spec, catalog, body):
    """4xx — это ответ, а не повод повторить: повтор рискует оплатить второй
    сервер, если первый всё же был создан."""
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return catalog(request)
        posts["n"] += 1
        return httpx.Response(422, json=body)

    adapter = factory()
    _wire(monkeypatch, adapter, handler)
    res = asyncio.run(adapter.create_order(creds, spec))

    assert res["ok"] is False and res["error"]
    assert posts["n"] == 1, "ретраев у создающего запроса быть не должно"
    secret = creds.get("token") or creds["api_key"]
    assert secret not in res["error"], "секрет в текст ошибки не попадает"
    # Причина словами вендора: «нет денег» и «нет ресурса» лечатся по-разному.
    assert "not enough money" in res["error"]


@pytest.mark.parametrize("factory, creds, spec", [
    (TimewebAdapter, {"token": "s3cret-tw"},
     {"plan_id": "4212", "image": "79", "name": "n"}),
    (LatitudeAdapter, {"token": "s3cret-lat"},
     {"plan_id": "p", "region": "ASH", "image": "i", "name": "n"}),
    (AezaAdapter, {"api_key": "s3cret-aeza"},
     {"plan_id": "3", "image": "25", "name": "n"}),
])
def test_a_dead_vendor_never_raises_out_of_the_ordering_methods(
        monkeypatch, factory, creds, spec):
    secret = creds.get("token") or creds["api_key"]

    def handler(request: httpx.Request) -> httpx.Response:
        # Секрет внутри текста исключения: httpx подставляет в него запрос, и
        # без redact он уехал бы пользователю в сообщение об ошибке.
        raise httpx.ConnectError(f"connection refused for {secret}")

    adapter = factory()
    _wire(monkeypatch, adapter, handler)

    assert asyncio.run(adapter.order_options(creds)) is None
    assert asyncio.run(adapter.quote_order(creds, spec)) is None
    res = asyncio.run(adapter.create_order(creds, spec))
    assert res["ok"] is False and secret not in res["error"]


@pytest.mark.parametrize("factory, creds", [
    (TimewebAdapter, {"token": "t"}),
    (LatitudeAdapter, {"token": "l"}),
    (AezaAdapter, {"api_key": "k"}),
])
def test_an_empty_spec_is_refused_before_the_network(monkeypatch, factory, creds):
    """Пустую форму отклоняет вендор и сам, но лишний запрос к чужому API — и
    тем более создающий — незачем."""
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts["n"] += 1
        return httpx.Response(404, json={})

    adapter = factory()
    _wire(monkeypatch, adapter, handler)
    res = asyncio.run(adapter.create_order(creds, {}))

    assert res["ok"] is False and "не заполнено" in res["error"]
    assert posts["n"] == 0


def test_aeza_does_not_overwrite_the_currency_the_user_confirmed(monkeypatch):
    """Ответ заказа валюту не называет, а карта `prices` бывает и по валютам.
    Маршрут предпочитает значение адаптера любому непустому, поэтому «RUB»
    здесь переписал бы валюту, в которой сумма была подтверждена."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"id": 1, "createdServiceIds": [2]})
        return _aeza_catalog(request)

    a = AezaAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order({"api_key": "k"}, {
        "plan_id": "3", "image": "25", "name": "srv"}))

    assert res["ok"] is True and res["currency"] == ""


@pytest.mark.parametrize("factory, creds, spec", [
    (TimewebAdapter, {"token": "t"},
     {"plan_id": "4212", "region": "ru-1", "image": "79", "name": "n"}),
    (LatitudeAdapter, {"token": "l"},
     {"plan_id": "p", "region": "ASH", "image": "i", "name": "n"}),
    (AezaAdapter, {"api_key": "k"},
     {"plan_id": "3", "image": "25", "name": "n"}),
])
def test_a_bare_list_instead_of_an_object_does_not_raise(
        monkeypatch, factory, creds, spec):
    """Контракт запрещает адаптеру бросать, а тело ответа — чужой JSON: голый
    список вместо объекта не должен превращаться в AttributeError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"unexpected": True}])

    adapter = factory()
    _wire(monkeypatch, adapter, handler)

    # Заказывать нечего — но это должно быть пустым каталогом либо None, а не
    # исключением: маршрут на пустом каталоге отвечает внятным отказом.
    opts = asyncio.run(adapter.order_options(creds))
    assert opts is None or (not opts.plans and not opts.custom)
    assert asyncio.run(adapter.quote_order(creds, spec)) is None
    res = asyncio.run(adapter.create_order(creds, spec))
    assert res["ok"] is False and res["error"]


def test_all_three_advertise_ordering():
    """«order» в CAPS — обещание, что создание действительно реализовано."""
    for factory in (TimewebAdapter, LatitudeAdapter, AezaAdapter):
        assert "order" in factory().CAPS
