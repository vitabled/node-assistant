"""Заказ у BILLmanager / HostKey / Servers.com.

Живых вызовов нет: у каждого адаптера подменяется `_client()` на MockTransport,
поэтому здесь ловится переименованное вендором поле, а не сетевая погода.

Что проверяется помимо маппинга:

- **создающий запрос уходит ровно один раз** — заказ тратит деньги, и «повторим
  на всякий случай» здесь означает второй оплаченный сервер;
- **заказ у HostKey не задевает пути оплаты счетов** — на них стоит ловушка,
  потому что запрет тратить деньги по расписанию у этого адаптера отдельный и
  старше, чем поддержка заказа;
- **там, где ручка не подтверждена, отказ приходит словами и БЕЗ запроса** к
  чужому биллингу.

Формы ответов сверены с публичной документацией вендоров: presets/os/eq у
invapi HostKey, `cloud_computing/*` у Servers.com (совпадает с их же
Go-клиентом: `region_id int64`, `flavor_id`/`image_id` — строки).
"""
import asyncio
import json
import urllib.parse

import httpx
import pytest

from app.services import net_guard
from app.services.hosting_providers import billmanager
from app.services.hosting_providers.billmanager import BillmanagerAdapter
from app.services.hosting_providers.hostkey import HostkeyAdapter
from app.services.hosting_providers.servers_com import ServersComAdapter


def _wire(monkeypatch, adapter, handler):
    monkeypatch.setattr(
        adapter, "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _form_fields(request: httpx.Request) -> dict:
    """Тело POST-формы → словарь (invapi и billmgr шлют не JSON)."""
    return dict(urllib.parse.parse_qsl(request.content.decode()))


# ═══════════════════════════════════════════════════════════════
# BILLmanager — заказ многошаговый, поэтому его нет
# ═══════════════════════════════════════════════════════════════
@pytest.fixture(autouse=True)
def _clean_sessions():
    """Кэш сессий модульного уровня — чистим, чтобы тесты не влияли друг на друга."""
    billmanager._SESSIONS.clear()
    yield
    billmanager._SESSIONS.clear()


_BM_CREDS = {"base_url": "https://my.example.com", "username": "user",
             "password": "s3cret-pass"}


def test_billmanager_refuses_the_order_without_touching_the_panel(monkeypatch):
    """Номера `addon_*` свои у каждого тарифа каждого провайдера, а `skipbasket=on`
    списывает сразу — «попробовать и посмотреть» здесь стоит чужих денег."""
    a = BillmanagerAdapter()

    def trap(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"отказ обязан быть офлайновым, а ушёл запрос: {request.url}")

    _wire(monkeypatch, a, trap)
    res = asyncio.run(a.create_order(_BM_CREDS, {"plan_id": "56324", "cpu": 4}))

    assert res["ok"] is False
    assert res["error"] == billmanager._ORDER_UNSUPPORTED
    assert "addon" in res["error"], "причина отказа должна быть предметной"
    # Кнопки заказа быть не должно: каталог всё равно гейтится на CAPS.
    assert "order" not in a.CAPS
    assert asyncio.run(a.order_options(_BM_CREDS)) is None


@pytest.mark.parametrize("base_url", [
    "http://127.0.0.1/billmgr",
    "http://10.1.2.3",
    "http://169.254.169.254/",
    "ftp://my.example.com",
])
def test_billmanager_rejects_a_private_panel_before_any_request(monkeypatch, base_url):
    """Адрес панели вводит пользователь → гард обязан сработать ДО запроса."""
    monkeypatch.setattr(net_guard, "_ALLOW_PRIVATE", False)
    a = BillmanagerAdapter()

    def trap(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"SSRF-гард пропустил запрос: {request.url}")

    _wire(monkeypatch, a, trap)
    ok, err = asyncio.run(a.verify(dict(_BM_CREDS, base_url=base_url)))

    assert ok is False and err == billmanager._UNSAFE
    assert asyncio.run(a.balance(dict(_BM_CREDS, base_url=base_url))) is None


def test_billmanager_reuses_the_session_across_calls(monkeypatch):
    """Логин с паролем — самое неприятное место, чтобы ловить лимиты: повторный
    вызов обязан идти по кэшированному id сессии, а не авторизоваться заново."""
    # Адрес панели вымышленный, а гард резолвит хост по-настоящему — без этого
    # тест зависел бы от DNS. Отказ гарда проверяется отдельным тестом выше.
    monkeypatch.setattr(net_guard, "_ALLOW_PRIVATE", True)
    funcs: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = _form_fields(request)
        funcs.append(params.get("func", ""))
        if params.get("func") == "auth":
            assert params["password"] == _BM_CREDS["password"]
            return httpx.Response(200, json={"doc": {"auth": {"$id": "sid-42"}}})
        assert params.get("auth") == "sid-42", "данные тянем по id сессии"
        # Скаляр ISPsystem — объект `{"$": …}`, разряды с НЕРАЗРЫВНЫМ пробелом.
        return httpx.Response(200, json={"doc": {"balance": {"$": "1 234,56 руб."}}})

    a = BillmanagerAdapter()
    _wire(monkeypatch, a, handler)

    first = asyncio.run(a.balance(_BM_CREDS))
    second = asyncio.run(a.balance(_BM_CREDS))

    assert first is not None and first.amount == pytest.approx(1234.56)
    assert first.currency == "RUB" and second is not None
    assert funcs.count("auth") == 1, "второй вызов авторизовываться не должен"
    assert funcs == ["auth", "usrparam", "usrparam"]


# ═══════════════════════════════════════════════════════════════
# HostKey — заказ есть, оплаты счетов адаптер не касается
# ═══════════════════════════════════════════════════════════════
_HK_CREDS = {"token": "s3cret-hostkey"}

_HK_PRESETS = {"presets": [
    # `active` — ЧИСЛО, `locations` — СТРОКА через запятую, `price` разложен по
    # локациям; всё три — реальная форма ответа presets.php.
    {"id": 108, "name": "vm.pico", "active": 1, "description": "NVMe",
     "cpu": 1, "ram": 2, "hdd": 25, "monthly_com": 3, "locations": "NL,US,FI",
     "price": {"NL": {"EUR": 3.5, "USD": 4.1}, "US": {"EUR": 2.9, "USD": 3.4}}},
    {"id": 240, "name": "vm.large", "active": 1,
     "cpu": 8, "ram": 32, "hdd": 480, "monthly_com": 20, "locations": "NL"},
    {"id": 999, "name": "снят с продажи", "active": 0, "locations": "NL"},
]}

_HK_OS = {
    "108": {"os_list": [
        {"id": 160, "name": "Ubuntu 20", "active": 1},
        {"id": 187, "name": "Debian 12", "active": 1},
        {"id": 7, "name": "снятая ОС", "active": 0},
    ]},
    "240": {"os_list": [{"id": 160, "name": "Ubuntu 20", "active": 1}]},
}


def _hk_catalog(request: httpx.Request) -> httpx.Response:
    """Каталог invapi + ЛОВУШКА на путях оплаты (см. запрет в шапке hostkey.py).

    Запрет «не трогать оплату счетов и пополнение» старше поддержки заказа, и
    заказ не должен был его ослабить: любой заход в `whmcs.php` / `*pay*` роняет
    тест, а не тихо возвращает ответ."""
    path = request.url.path
    assert "whmcs" not in path, f"адаптер полез в раздел оплаты: {path}"
    assert "pay" not in path.lower(), f"адаптер полез в оплату: {path}"

    params = _form_fields(request)
    if path == "/presets.php":
        return httpx.Response(200, json=_HK_PRESETS)
    if path == "/os.php":
        # Список ОС пер-пресетный: ключ — id пресета.
        return httpx.Response(200, json=_HK_OS.get(params.get("instance_id"), {}))
    return httpx.Response(404, json={"result": "ERROR", "message": "not found"})


@pytest.mark.parametrize("path", ["/whmcs.php", "/eq.php/payment", "/getpaymentgw"])
def test_the_payment_trap_itself_fires(path):
    """Ловушка обязана быть НЕ пустой: если бы она молчала, тесты заказа ничего
    не доказывали бы про запрет трогать оплату."""
    with pytest.raises(AssertionError):
        _hk_catalog(httpx.Request("POST", f"https://invapi.hostkey.com{path}",
                                  data={"action": "list"}))


def test_hostkey_order_options_map_presets_and_per_preset_images(monkeypatch):
    a = HostkeyAdapter()
    _wire(monkeypatch, a, _hk_catalog)
    opts = asyncio.run(a.order_options(_HK_CREDS))

    assert opts is not None and opts.custom is None, "у HostKey только готовые пресеты"
    assert [p.id for p in opts.plans] == ["108", "240"], "снятый пресет не предлагаем"
    # Цена зависит от локации — в каталоге минимальная (US дешевле NL).
    assert opts.plans[0].price == pytest.approx(2.9) and opts.plans[0].currency == "EUR"
    # Без разбивки по локациям остаётся плоский monthly_com.
    assert opts.plans[1].price == pytest.approx(20.0)
    assert "1 CPU" in opts.plans[0].specs and "25 ГБ диск" in opts.plans[0].specs

    # `locations` разобран из строки, а не принят за один код целиком.
    assert [r["id"] for r in opts.regions] == ["FI", "NL", "US"]
    assert dict(zip([r["id"] for r in opts.regions],
                    [r["presets"] for r in opts.regions]))["NL"] == ["108", "240"]

    images = {i["id"]: i for i in opts.images}
    assert set(images) == {"160", "187"}, "неактивная ОС в каталог не идёт"
    # Совместимость едет с образом: Debian есть только у младшего пресета.
    assert images["160"]["allowed_presets"] == ["108", "240"]
    assert images["187"]["allowed_presets"] == ["108"]


def test_hostkey_create_order_sends_the_documented_form_once(monkeypatch):
    """Транспорт заказа — POST-форма с полем `token` (не заголовок), ровно один
    запрос, и он не касается путей оплаты счетов."""
    sent: dict = {}
    posts = {"n": 0}
    visited: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        visited.append(request.url.path)
        if request.url.path != "/eq.php":
            return _hk_catalog(request)
        posts["n"] += 1
        sent.update(_form_fields(request))
        return httpx.Response(200, json={
            "result": "OK", "action": "order_instance",
            # Ответ без `id`: у этой сборки заказ опознаётся номером счёта.
            "invoice": 50062, "status": "Paid",
        })

    a = HostkeyAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_HK_CREDS, {
        "plan_id": "108", "region": "nl", "image": "160",
        "name": "node-1", "period": "year",
    }))

    assert res["ok"] is True and res["id"] == "50062" and res["name"] == "node-1"
    assert res["currency"] == "EUR"
    assert posts["n"] == 1, "создающий запрос ровно один"
    assert sent == {
        "action": "order_instance", "token": _HK_CREDS["token"],
        "preset": "108", "os_id": "160",
        # Локация приводится к верхнему регистру: иначе вендор отвечает ошибкой
        # совместимости ОС.
        "location_name": "NL",
        "hostname": "node-1",
        # Слова вендора, не WHMCS-форма `annually`.
        "deploy_period": "yearly",
    }
    assert "root_pass" not in sent, "пароль root не задаём — вернуть его некуда"
    # Весь набор путей, а не только те, что мы ожидали: заказ не ходит никуда,
    # кроме точки заказа — ни в счета, ни в оплату.
    assert visited == ["/eq.php"]


def test_hostkey_order_carries_the_token_in_the_body_not_the_header(monkeypatch):
    """Документированный транспорт invapi — поле формы; заголовком ходят только
    читающие методы."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/eq.php":
            return _hk_catalog(request)
        seen.append(request)
        return httpx.Response(200, json={"result": "OK", "id": 777})

    a = HostkeyAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_HK_CREDS, {
        "plan_id": "108", "region": "NL", "image": "160", "name": "n",
    }))

    assert res["ok"] is True and res["id"] == "777"
    assert "authorization" not in {k.lower() for k in seen[0].headers}
    # Незнакомый период сводится к самому короткому сроку, а не к самому дорогому.
    assert _form_fields(seen[0])["deploy_period"] == "monthly"


def test_hostkey_reports_a_refusal_once_and_without_the_token(monkeypatch):
    """Ошибка invapi приезжает с HTTP 200 — её обязан поймать разбор тела, а не
    статус. 4xx/отказ — это ответ, а не повод повторить."""
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/eq.php":
            return _hk_catalog(request)
        posts["n"] += 1
        return httpx.Response(200, json={
            "result": "ERROR", "code": -1,
            "message": f"not enough funds for {_HK_CREDS['token']}",
        })

    a = HostkeyAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_HK_CREDS, {
        "plan_id": "108", "region": "NL", "image": "160", "name": "n",
    }))

    assert res["ok"] is False and posts["n"] == 1
    assert "not enough funds" in res["error"], "причина словами вендора"
    assert _HK_CREDS["token"] not in res["error"], "секрет в текст ошибки не попадает"


def test_hostkey_refuses_an_incomplete_order_before_the_network(monkeypatch):
    """Пустая локация — отказ на нашей стороне: лишний запрос к чужому API незачем."""
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/eq.php":
            posts["n"] += 1
        return _hk_catalog(request)

    a = HostkeyAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_HK_CREDS, {"plan_id": "108", "name": "n"}))

    assert res["ok"] is False and "не заполнено" in res["error"]
    assert "локация" in res["error"] and "образ" in res["error"]
    assert posts["n"] == 0


def test_hostkey_answer_without_an_identifier_is_not_called_a_failure(monkeypatch):
    """Заказ мог пройти и уже быть оплачен — молчаливое «не получилось» тут
    опаснее, чем просьба заглянуть в панель."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/eq.php":
            return _hk_catalog(request)
        return httpx.Response(200, json={"result": "OK", "status": "Paid"})

    a = HostkeyAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_HK_CREDS, {
        "plan_id": "108", "region": "NL", "image": "160", "name": "n",
    }))

    assert res["ok"] is False and "проверьте панель" in res["error"]


def test_hostkey_does_not_mistake_a_read_response_for_an_error(monkeypatch):
    """`code` бывает и у нормального ответа (200) — ошибкой считаем только
    документированный отрицательный код."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert "whmcs" not in request.url.path
        if request.url.path == "/presets.php":
            return httpx.Response(200, json={"code": 200, **_HK_PRESETS})
        return httpx.Response(200, json=_HK_OS.get(_form_fields(request)
                                                   .get("instance_id"), {}))

    a = HostkeyAdapter()
    _wire(monkeypatch, a, handler)
    opts = asyncio.run(a.order_options(_HK_CREDS))

    assert opts is not None and len(opts.plans) == 2


def test_hostkey_ordering_survives_a_dead_vendor(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"refused for {_HK_CREDS['token']}")

    a = HostkeyAdapter()
    _wire(monkeypatch, a, handler)

    assert asyncio.run(a.order_options(_HK_CREDS)) is None
    res = asyncio.run(a.create_order(_HK_CREDS, {
        "plan_id": "108", "region": "NL", "image": "160", "name": "n",
    }))
    assert res["ok"] is False and _HK_CREDS["token"] not in res["error"]


# ═══════════════════════════════════════════════════════════════
# Servers.com — облачные инстансы
# ═══════════════════════════════════════════════════════════════
_SC_CREDS = {"token": "s3cret-servers-com"}


def _sc_catalog(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/cloud_computing/regions"):
        return httpx.Response(200, json=[
            {"id": 1, "name": "Amsterdam 1", "code": "AMS1"},
            {"id": 3, "name": "Dallas 1", "code": "DAL1"},
        ])
    if path.endswith("/regions/1/flavors"):
        # `ram` — МЕГАбайты (каталог OpenStack), на этом легко ошибиться.
        return httpx.Response(200, json=[
            {"id": "bcbf62f3", "name": "SSD.30", "vcpus": 2, "ram": 4096, "disk": 30},
        ])
    if path.endswith("/regions/3/flavors"):
        return httpx.Response(200, json=[
            {"id": "bcbf62f3", "name": "SSD.30", "vcpus": 2, "ram": 4096, "disk": 30},
            {"id": "d9f0a1", "name": "SSD.60", "vcpus": 4, "ram": 8192, "disk": 60},
        ])
    if "/images" in path:
        return httpx.Response(200, json=[
            {"id": "img-ubuntu", "name": "Ubuntu 22.04",
             "min_disk": 20, "allowed_flavors": ["bcbf62f3"]},
        ])
    return httpx.Response(404, json={"message": "not found"})


def test_servers_com_order_options_merge_the_per_region_catalog(monkeypatch):
    """Каталог у Servers.com пер-регионный, а `order_options` знает только креды —
    списки доступного едут внутри регионов, чтобы форма не предложила заведомый 4xx."""
    a = ServersComAdapter()
    _wire(monkeypatch, a, _sc_catalog)
    opts = asyncio.run(a.order_options(_SC_CREDS))

    assert opts is not None and opts.custom is None
    assert sorted(p.id for p in opts.plans) == ["bcbf62f3", "d9f0a1"]
    plan = {p.id: p for p in opts.plans}["bcbf62f3"]
    assert "4 ГБ RAM" in plan.specs, "ram у flavor'а в мегабайтах"
    # Цены в каталоге flavor'ов нет — выдумывать её нельзя.
    assert plan.price is None and plan.currency == ""

    regions = {r["id"]: r for r in opts.regions}
    assert set(regions) == {"1", "3"} and regions["1"]["code"] == "AMS1"
    assert regions["3"]["flavors"] == ["bcbf62f3", "d9f0a1"]
    assert opts.images[0]["allowed_flavors"] == ["bcbf62f3"]


def test_servers_com_create_order_sends_a_numeric_region_id_once(monkeypatch):
    """`region_id` в теле — ЧИСЛО (int64 у их же Go-клиента), а из формы приезжает
    строка; `flavor_id`/`image_id` остаются строками."""
    sent: dict = {}
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return _sc_catalog(request)
        posts["n"] += 1
        sent.update(json.loads(request.content))
        return httpx.Response(201, json={"id": "a1b2c3", "name": "web-1",
                                         "status": "CREATING"})

    a = ServersComAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_SC_CREDS, {
        "plan_id": "bcbf62f3", "region": "1", "image": "img-ubuntu", "name": "web-1",
    }))

    assert res["ok"] is True and res["id"] == "a1b2c3" and res["name"] == "web-1"
    # Вендор не называет сумму ни в каталоге, ни в ответе — и мы не называем.
    assert res["price"] is None and res["currency"] == ""
    assert posts["n"] == 1
    assert sent == {"name": "web-1", "region_id": 1,
                    "flavor_id": "bcbf62f3", "image_id": "img-ubuntu"}
    assert isinstance(sent["region_id"], int)


def test_servers_com_reports_a_refusal_once_without_the_token(monkeypatch):
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return _sc_catalog(request)
        posts["n"] += 1
        return httpx.Response(422, json={"message": "quota exceeded"})

    a = ServersComAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_SC_CREDS, {
        "plan_id": "bcbf62f3", "region": "1", "image": "img", "name": "n",
    }))

    assert res["ok"] is False and posts["n"] == 1
    assert "quota exceeded" in res["error"]
    assert _SC_CREDS["token"] not in res["error"]
