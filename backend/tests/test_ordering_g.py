"""Заказ у Oracle (OCI) / IONOS / OVHcloud / Infomaniak.

Живых вызовов нет: `_client()` каждого адаптера подменяется на MockTransport,
поэтому здесь ловится переименованное вендором поле, а не сетевая погода.

Что проверяется помимо маппинга:

- **подпись OCI пересобирается независимо** по РЕАЛЬНО ушедшему запросу и
  проверяется публичным ключом: для POST в неё входят `x-content-sha256`,
  `content-type` и `content-length`, и ошибка в их порядке — это 401,
  неотличимый от неверного ключа;
- **создающий запрос уходит ровно один раз** — заказ тратит деньги, и «повторим
  на всякий случай» здесь означает второй оплаченный сервер;
- **отказ приходит ДО создающего запроса**: нет подсети в домене доступности,
  нет LAN в датацентре, нет SSH-ключа — всё это выясняется заранее, потому что
  сервер, в который нельзя войти, оплачивается так же, как рабочий;
- **у OVHcloud и Infomaniak отказ офлайновый**: на любой запрос стоит ловушка.
"""
import asyncio
import base64
import hashlib
import json
import re

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.services.hosting_providers import infomaniak, ionos, oracle, ovhcloud
from app.services.hosting_providers.infomaniak import InfomaniakAdapter
from app.services.hosting_providers.ionos import IonosAdapter
from app.services.hosting_providers.oracle import OracleAdapter
from app.services.hosting_providers.ovhcloud import OvhcloudAdapter

# Один ключ на файл: генерация RSA-2048 стоит десятки миллисекунд, а адаптеру
# нужен просто «какой-нибудь» рабочий RSA.
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()
_PUB = _KEY.public_key()


def _wire(monkeypatch, adapter, handler):
    monkeypatch.setattr(
        adapter, "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _trap(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"отказ обязан быть офлайновым, а ушёл запрос: {request.url}")


# ═══════════════════════════════════════════════════════════════
# Oracle Cloud (OCI) — LaunchInstance
# ═══════════════════════════════════════════════════════════════
_OCI_CREDS = {
    "tenancy_ocid": "ocid1.tenancy.oc1..aaaatenancy",
    "user_ocid": "ocid1.user.oc1..aaaauser",
    "fingerprint": "aa:bb:cc:dd:ee:ff:00:11",
    "private_key": _PEM,
    "region": "eu-frankfurt-1",
    "compartment_id": "ocid1.compartment.oc1..aaaacomp",
}

_AD_1 = "feDV:EU-FRANKFURT-1-AD-1"
_AD_2 = "feDV:EU-FRANKFURT-1-AD-2"

_OCI_SHAPES = [
    # Одна и та же форма повторяется по доменам доступности — нужен дедуп.
    {"shape": "VM.Standard.E4.Flex", "ocpus": 1.0, "memoryInGBs": 16.0,
     "processorDescription": "AMD EPYC", "isFlexible": True,
     "ocpuOptions": {"min": 1, "max": 64},
     "memoryOptions": {"minInGBs": 1, "maxInGBs": 1024}},
    {"shape": "VM.Standard.E4.Flex", "ocpus": 1.0, "memoryInGBs": 16.0},
    {"shape": "VM.Standard2.1", "ocpus": 1.0, "memoryInGBs": 15.0,
     "processorDescription": "Intel Xeon"},
]

_OCI_IMAGES = [
    {"id": "ocid1.image.oc1..ubuntu", "displayName": "Canonical-Ubuntu-22.04",
     "operatingSystem": "Canonical Ubuntu", "operatingSystemVersion": "22.04",
     "lifecycleState": "AVAILABLE"},
    {"id": "ocid1.image.oc1..old", "displayName": "снятый образ",
     "lifecycleState": "DELETED"},
]

# Подсети: приватная в AD-1 идёт ПЕРВОЙ, чтобы было видно, что публичная
# выигрывает не порядком, а признаком.
_OCI_SUBNETS = [
    {"id": "ocid1.subnet.oc1..private", "displayName": "private",
     "availabilityDomain": _AD_1, "lifecycleState": "AVAILABLE",
     "prohibitPublicIpOnVnic": True},
    {"id": "ocid1.subnet.oc1..public", "displayName": "public",
     "availabilityDomain": _AD_1, "lifecycleState": "AVAILABLE",
     "prohibitPublicIpOnVnic": False},
]

_OCI_INSTANCE = {"id": "ocid1.instance.oc1..aaaanew", "displayName": "node-fra-1",
                 "lifecycleState": "PROVISIONING"}


def _assert_signed(request: httpx.Request, names: list[str]) -> None:
    """Пересобрать строку подписи по УШЕДШЕМУ запросу и проверить её ключом.

    Так ловится и переставленный заголовок, и URL, пересобранный после
    подписи, — а не только «мы что-то подписали»."""
    auth = request.headers["authorization"]
    fields = dict(re.findall(r'(\w+)="([^"]*)"', auth))
    assert fields["algorithm"] == "rsa-sha256"
    assert fields["keyId"] == "{}/{}/{}".format(
        _OCI_CREDS["tenancy_ocid"], _OCI_CREDS["user_ocid"],
        _OCI_CREDS["fingerprint"])
    assert fields["headers"].split(" ") == names, "порядок заголовков значим"

    lines = []
    for name in names:
        if name == "(request-target)":
            target = request.url.raw_path.decode()
            lines.append(f"(request-target): {request.method.lower()} {target}")
        else:
            lines.append(f"{name}: {request.headers[name]}")
    _PUB.verify(base64.b64decode(fields["signature"]),
                "\n".join(lines).encode(), padding.PKCS1v15(), hashes.SHA256())


def _oci_catalog(request: httpx.Request) -> httpx.Response:
    host, path = request.url.host, request.url.path
    if path.endswith("/availabilityDomains"):
        assert host.startswith("identity."), "домены доступности — сервис Identity"
        return httpx.Response(200, json=[{"name": _AD_1}, {"name": _AD_2}])
    if path.endswith("/shapes"):
        return httpx.Response(200, json=_OCI_SHAPES)
    if path.endswith("/images"):
        return httpx.Response(200, json=_OCI_IMAGES)
    if path.endswith("/subnets"):
        return httpx.Response(200, json=_OCI_SUBNETS)
    return httpx.Response(404, json={"code": "NotFound", "message": "not found"})


def test_oracle_launch_is_signed_with_the_body_digest(monkeypatch):
    """Создающий запрос ровно один, подпись POST несёт дайджест тела, а тело —
    ровно то, что требует LaunchInstance."""
    posts = {"n": 0}
    sent: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return _oci_catalog(request)
        posts["n"] += 1
        assert request.url.host == "iaas.eu-frankfurt-1.oraclecloud.com"
        assert request.url.path == "/20160918/instances"
        _assert_signed(request, ["(request-target)", "date", "host",
                                 "x-content-sha256", "content-type",
                                 "content-length"])
        digest = base64.b64encode(hashlib.sha256(request.content).digest()).decode()
        assert request.headers["x-content-sha256"] == digest
        assert request.headers["content-length"] == str(len(request.content))
        sent.update(json.loads(request.content))
        return httpx.Response(200, json=_OCI_INSTANCE)

    a = OracleAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_OCI_CREDS, {
        "plan_id": "VM.Standard.E4.Flex", "region": _AD_1,
        "image": "ocid1.image.oc1..ubuntu", "name": "node-fra-1",
        "cpu": 2, "ram_gb": 8, "disk_gb": 100,
    }))

    assert res["ok"] is True and res["id"] == "ocid1.instance.oc1..aaaanew"
    assert res["name"] == "node-fra-1"
    # Цены OCI не называет ни в каталоге, ни в ответе — и мы не называем.
    assert res["price"] is None and res["currency"] == ""
    assert posts["n"] == 1, "создающий запрос ровно один"

    assert sent["compartmentId"] == _OCI_CREDS["compartment_id"]
    assert sent["availabilityDomain"] == _AD_1
    assert sent["shape"] == "VM.Standard.E4.Flex"
    assert sent["displayName"] == "node-fra-1"
    assert sent["sourceDetails"] == {"sourceType": "image",
                                     "imageId": "ocid1.image.oc1..ubuntu",
                                     "bootVolumeSizeInGBs": 100}
    # Публичная подсеть выигрывает у приватной, хотя в ответе идёт второй.
    assert sent["createVnicDetails"] == {"subnetId": "ocid1.subnet.oc1..public"}
    assert "assignPublicIp" not in sent["createVnicDetails"], \
        "явный флаг на приватной подсети даёт 400 — умолчание подсети надёжнее"
    assert sent["shapeConfig"] == {"ocpus": 2.0, "memoryInGBs": 8.0}


def test_oracle_fixed_shape_does_not_get_a_shape_config(monkeypatch):
    """`shapeConfig` у формы фиксированного размера — это 400 у вендора."""
    sent: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return _oci_catalog(request)
        sent.update(json.loads(request.content))
        return httpx.Response(200, json=_OCI_INSTANCE)

    a = OracleAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_OCI_CREDS, {
        "plan_id": "VM.Standard2.1", "region": _AD_1,
        "image": "ocid1.image.oc1..ubuntu", "name": "n", "cpu": 8, "ram_gb": 64,
    }))

    assert res["ok"] is True
    assert "shapeConfig" not in sent
    # Диск не задан — размер загрузочного тома остаётся вендорским умолчанием.
    assert "bootVolumeSizeInGBs" not in sent["sourceDetails"]


def test_oracle_picks_a_public_or_regional_subnet():
    """Чистый выбор подсети: региональная подходит любому домену, публичная
    раньше приватной, чужой домен доступности не подходит вовсе."""
    regional = {"id": "s-regional", "availabilityDomain": "",
                "prohibitPublicIpOnVnic": True}
    private_ad1 = {"id": "s-private", "availabilityDomain": _AD_1,
                   "prohibitPublicIpOnVnic": True}
    public_ad1 = {"id": "s-public", "availabilityDomain": _AD_1,
                  "prohibitPublicIpOnVnic": False}

    assert oracle.pick_subnet([private_ad1, public_ad1], _AD_1)["id"] == "s-public"
    assert oracle.pick_subnet([regional], _AD_2)["id"] == "s-regional"
    assert oracle.pick_subnet([private_ad1], _AD_2) is None
    # Недоступная подсеть — не подсеть.
    assert oracle.pick_subnet(
        [{"id": "s", "availabilityDomain": _AD_1, "lifecycleState": "TERMINATED"}],
        _AD_1) is None


def test_oracle_refuses_when_the_chosen_domain_has_no_subnet(monkeypatch):
    """Домен выбран явно, подсети в нём нет — отказ БЕЗ создающего запроса."""
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts["n"] += 1
        return _oci_catalog(request)

    a = OracleAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_OCI_CREDS, {
        "plan_id": "VM.Standard2.1", "region": _AD_2,
        "image": "ocid1.image.oc1..ubuntu", "name": "n",
    }))

    assert res["ok"] is False and posts["n"] == 0
    assert _AD_2 in res["error"] and "подсети" in res["error"]


def test_oracle_refuses_when_no_domain_has_a_subnet(monkeypatch):
    """Домен не выбран: перебираем все и, не найдя подсети, отказываем."""
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts["n"] += 1
        if request.url.path.endswith("/subnets"):
            # Подсеть есть, но в домене, которого нет в списке региона.
            return httpx.Response(200, json=[{
                "id": "ocid1.subnet.oc1..elsewhere",
                "availabilityDomain": "feDV:US-ASHBURN-1-AD-1",
                "lifecycleState": "AVAILABLE"}])
        return _oci_catalog(request)

    a = OracleAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_OCI_CREDS, {
        "plan_id": "VM.Standard2.1", "image": "ocid1.image.oc1..ubuntu",
        "name": "n",
    }))

    assert res["ok"] is False and posts["n"] == 0
    assert "домен доступности" in res["error"]


def test_oracle_tries_both_identity_hostnames_then_refuses(monkeypatch):
    """OCI публикует Identity под двумя написаниями хоста. Не ответило ни одно —
    домен доступности неизвестен, а «взять первый попавшийся» тут нечего."""
    hosts: list[str] = []
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts["n"] += 1
        if request.url.path.endswith("/availabilityDomains"):
            hosts.append(request.url.host)
            return httpx.Response(500, json={"message": "unavailable"})
        return _oci_catalog(request)

    a = OracleAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_OCI_CREDS, {
        "plan_id": "VM.Standard2.1", "image": "ocid1.image.oc1..ubuntu",
        "name": "n",
    }))

    assert res["ok"] is False and posts["n"] == 0
    assert hosts == ["identity.eu-frankfurt-1.oci.oraclecloud.com",
                     "identity.eu-frankfurt-1.oraclecloud.com"]


def test_oracle_refuses_an_incomplete_or_undersized_order_offline(monkeypatch):
    """Отсутствие обязательного поля, гибкая форма без ядер и слишком маленький
    загрузочный том — всё это выясняется у нас, без запросов к вендору."""
    a = OracleAdapter()
    _wire(monkeypatch, a, _trap)

    res = asyncio.run(a.create_order(_OCI_CREDS, {"plan_id": "VM.Standard2.1"}))
    assert res["ok"] is False and "не заполнено" in res["error"]
    assert "имя сервера" in res["error"] and "образ" in res["error"]

    res = asyncio.run(a.create_order(_OCI_CREDS, {
        "plan_id": "VM.Standard.E4.Flex", "region": _AD_1, "name": "n",
        "image": "ocid1.image.oc1..ubuntu",
    }))
    assert res["ok"] is False and "гибкая" in res["error"]

    res = asyncio.run(a.create_order(_OCI_CREDS, {
        "plan_id": "VM.Standard2.1", "region": _AD_1, "name": "n",
        "image": "ocid1.image.oc1..ubuntu", "disk_gb": 20,
    }))
    assert res["ok"] is False and "50" in res["error"]


def test_oracle_reports_the_vendor_reason_once_without_the_key(monkeypatch):
    """Отказ вендора — это ответ, а не повод повторить. Причина словами OCI,
    материал ключа в текст не попадает."""
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return _oci_catalog(request)
        posts["n"] += 1
        return httpx.Response(500, json={
            "code": "InternalError",
            "message": "Out of host capacity in feDV:EU-FRANKFURT-1-AD-1",
        })

    a = OracleAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_OCI_CREDS, {
        "plan_id": "VM.Standard2.1", "region": _AD_1, "name": "n",
        "image": "ocid1.image.oc1..ubuntu",
    }))

    assert res["ok"] is False and posts["n"] == 1
    assert "Out of host capacity" in res["error"]
    assert "PRIVATE KEY" not in res["error"] and _PEM not in res["error"]


def test_oracle_answer_without_an_identifier_is_not_called_a_failure(monkeypatch):
    """Инстанс мог быть создан — молчаливое «не получилось» тут опаснее просьбы
    заглянуть в консоль."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return _oci_catalog(request)
        return httpx.Response(200, json={"lifecycleState": "PROVISIONING"})

    a = OracleAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_OCI_CREDS, {
        "plan_id": "VM.Standard2.1", "region": _AD_1, "name": "n",
        "image": "ocid1.image.oc1..ubuntu",
    }))

    assert res["ok"] is False and "консоль" in res["error"]


def test_oracle_order_options_map_shapes_domains_and_images(monkeypatch):
    a = OracleAdapter()
    _wire(monkeypatch, a, _oci_catalog)
    opts = asyncio.run(a.order_options(_OCI_CREDS))

    assert opts is not None
    assert [p.id for p in opts.plans] == ["VM.Standard.E4.Flex", "VM.Standard2.1"], \
        "форма повторяется по доменам — в каталоге она одна"
    assert all(p.price is None and p.currency == "" for p in opts.plans), \
        "цены у OCI в API нет вовсе"
    assert "гибкая: 1–64 OCPU" in opts.plans[0].specs
    assert "1 OCPU · 15 ГБ RAM" in opts.plans[1].specs

    # Свободный селектор несёт домен доступности, а не регион.
    assert [r["id"] for r in opts.regions] == [_AD_1, _AD_2]
    assert opts.regions[0]["name"].startswith("Домен доступности")

    assert [i["id"] for i in opts.images] == ["ocid1.image.oc1..ubuntu"], \
        "удалённый образ в каталог не идёт"
    assert "Ubuntu" in opts.images[0]["name"]

    # Конструктор появляется из-за гибкой формы, границы — из её же опций.
    assert opts.custom["cpu"]["max"] == 64
    assert opts.custom["ram_gb"]["max"] == 1024
    assert opts.custom["disk_gb"]["min"] == 50


def test_oracle_shape_plans_offer_no_constructor_without_a_flexible_shape():
    """Ползунки ядер там, где размер задан именем формы, — обман интерфейса."""
    plans, custom = oracle.shape_plans([
        {"shape": "VM.Standard2.1", "ocpus": 1, "memoryInGBs": 15},
    ])
    assert [p.id for p in plans] == ["VM.Standard2.1"] and custom is None

    # А непубликованные границы гибкой формы не отменяют самого конструктора.
    _plans, custom = oracle.shape_plans([{"shape": "VM.Custom.Flex"}])
    assert custom is not None and custom["cpu"]["max"] is None


def test_oracle_ordering_survives_a_dead_vendor(monkeypatch):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    a = OracleAdapter()
    _wire(monkeypatch, a, boom)

    assert asyncio.run(a.order_options(_OCI_CREDS)) is None
    res = asyncio.run(a.create_order(_OCI_CREDS, {
        "plan_id": "VM.Standard2.1", "region": _AD_1, "name": "n",
        "image": "ocid1.image.oc1..ubuntu",
    }))
    assert res["ok"] is False and _PEM not in res["error"]


# ═══════════════════════════════════════════════════════════════
# IONOS — составное создание сервера в датацентре
# ═══════════════════════════════════════════════════════════════
_ION_CREDS = {"token": "s3cret-ionos", "ssh_public_key": "ssh-ed25519 AAAAC3Nz key"}
_DC = "dc-uuid-1"

_ION_DCS = {"items": [
    {"id": _DC, "properties": {"name": "fra", "location": "de/txl"}},
    {"id": "dc-uuid-2", "properties": {"name": "par", "location": "fr/par"}},
]}

_ION_IMAGES = {"items": [
    {"id": "img-ubuntu", "properties": {"name": "Ubuntu-22.04", "public": True,
                                        "imageType": "HDD", "licenceType": "LINUX",
                                        "location": "de/txl", "size": 4}},
    {"id": "img-windows", "properties": {"name": "Windows-2022", "public": True,
                                         "imageType": "HDD",
                                         "licenceType": "WINDOWS"}},
    {"id": "img-iso", "properties": {"name": "debian-netinst.iso", "public": True,
                                     "imageType": "CDROM",
                                     "licenceType": "LINUX"}},
    {"id": "img-mine", "properties": {"name": "мой снапшот", "public": False,
                                      "imageType": "HDD", "licenceType": "LINUX"}},
]}

_ION_CONTRACTS = {"items": [{"properties": {
    "contractNumber": 31337,
    # ramPerServer у IONOS в МЕГАбайтах.
    "resourceLimits": {"coresPerServer": 24, "ramPerServer": 245760},
}}]}

# Приватный LAN идёт первым: публичный должен выигрывать признаком, не порядком.
_ION_LANS = {"items": [
    {"id": "2", "properties": {"name": "private", "public": False}},
    {"id": "1", "properties": {"name": "public", "public": True}},
]}


def _ion_catalog(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    assert "/billing/" not in path, "заказ не ходит в биллинг-API"
    if path.endswith("/datacenters"):
        return httpx.Response(200, json=_ION_DCS)
    if path.endswith("/images"):
        return httpx.Response(200, json=_ION_IMAGES)
    if path.endswith("/contracts"):
        return httpx.Response(200, json=_ION_CONTRACTS)
    if path.endswith("/lans"):
        return httpx.Response(200, json=_ION_LANS)
    return httpx.Response(404, json={"messages": [{"message": "not found"}]})


def test_ionos_order_options_build_a_constructor_from_the_contract(monkeypatch):
    """Готовых тарифов у IONOS нет: форма показывает конструктор, а его границы
    берутся из лимитов договора, а не из головы."""
    a = IonosAdapter()
    _wire(monkeypatch, a, _ion_catalog)
    opts = asyncio.run(a.order_options(_ION_CREDS))

    assert opts is not None and opts.plans == []
    assert opts.custom["cpu"]["max"] == 24
    assert opts.custom["ram_gb"]["max"] == 240, "ramPerServer приходит в МБ"

    # Свободный селектор несёт датацентр — сервер создаётся внутри него.
    assert [r["id"] for r in opts.regions] == [_DC, "dc-uuid-2"]
    assert opts.regions[0]["name"].startswith("Датацентр")
    assert opts.regions[0]["location"] == "de/txl"

    assert [i["id"] for i in opts.images] == ["img-ubuntu"], \
        "чужая лицензия, ISO и приватный снапшот в каталог заказа не идут"
    assert opts.images[0]["location"] == "de/txl"
    assert opts.images[0]["min_disk_gb"] == 4


def test_ionos_contract_limits_stay_unknown_when_the_vendor_is_silent():
    """`None` значит «не прочитали», а не «безлимит»: выдуманный потолок отрезал
    бы реальную конфигурацию."""
    assert ionos.contract_limits({"items": []}) == (None, None)
    assert ionos.contract_limits({"properties": {"resourceLimits": {
        "coresPerServer": 8, "ramPerServer": 8192}}}) == (8, 8.0)


def test_ionos_creates_the_server_volume_and_nic_in_one_request(monkeypatch):
    """Составное создание: том и NIC уезжают вместе с сервером, поэтому
    создающий запрос ровно один."""
    posts = {"n": 0}
    sent: dict = {}
    visited: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        visited.append(f"{request.method} {request.url.path}")
        if request.method != "POST":
            return _ion_catalog(request)
        posts["n"] += 1
        sent.update(json.loads(request.content))
        return httpx.Response(202, json={"id": "srv-1",
                                         "properties": {"name": "node-1"}})

    a = IonosAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_ION_CREDS, {
        "region": _DC, "image": "img-ubuntu", "name": "node-1",
        "cpu": 4, "ram_gb": 8, "disk_gb": 50,
    }))

    assert res["ok"] is True and res["id"] == "srv-1" and res["name"] == "node-1"
    assert res["price"] is None and res["currency"] == ""
    assert posts["n"] == 1, "создающий запрос ровно один"
    assert visited == [f"GET /cloudapi/v6/datacenters/{_DC}/lans",
                       f"POST /cloudapi/v6/datacenters/{_DC}/servers"]

    assert sent["properties"] == {"name": "node-1", "cores": 4, "ram": 8192}
    volume = sent["entities"]["volumes"]["items"][0]["properties"]
    assert volume["size"] == 50 and volume["image"] == "img-ubuntu"
    assert volume["sshKeys"] == [_ION_CREDS["ssh_public_key"]]
    assert "imagePassword" not in volume, "пароль вернуть некуда — задаём ключ"
    nic = sent["entities"]["nics"]["items"][0]["properties"]
    assert nic["lan"] == 1 and isinstance(nic["lan"], int), \
        "публичный LAN, и его идентификатор — число"


def test_ionos_refuses_without_an_ssh_key_before_the_network(monkeypatch):
    """Публичный образ IONOS не создаётся без пароля или ключа, а пароль наружу
    отдавать нельзя — сервер, в который не войти, стоит тех же денег."""
    a = IonosAdapter()
    _wire(monkeypatch, a, _trap)
    res = asyncio.run(a.create_order({"token": "s3cret-ionos"}, {
        "region": _DC, "image": "img-ubuntu", "name": "n",
        "cpu": 2, "ram_gb": 4, "disk_gb": 20,
    }))

    assert res["ok"] is False and "SSH-ключ" in res["error"]


def test_ionos_refuses_when_the_datacenter_has_no_lan(monkeypatch):
    """NIC ссылается на существующий LAN — выдумывать его номер нельзя."""
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts["n"] += 1
        if request.url.path.endswith("/lans"):
            return httpx.Response(200, json={"items": []})
        return _ion_catalog(request)

    a = IonosAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_ION_CREDS, {
        "region": _DC, "image": "img-ubuntu", "name": "n",
        "cpu": 2, "ram_gb": 4, "disk_gb": 20,
    }))

    assert res["ok"] is False and posts["n"] == 0
    assert "LAN" in res["error"]


def test_ionos_refuses_an_incomplete_order_before_the_network(monkeypatch):
    """Конструктор без ядер/памяти/диска — это не заказ, а заведомый 422."""
    a = IonosAdapter()
    _wire(monkeypatch, a, _trap)
    res = asyncio.run(a.create_order(_ION_CREDS, {"name": "n", "region": _DC}))

    assert res["ok"] is False and "не заполнено" in res["error"]
    for expected in ("образ", "ядра", "память (ГБ)", "диск (ГБ)"):
        assert expected in res["error"]

    # И без кредов вовсе — с тем же текстом, что у остальных ручек адаптера.
    res = asyncio.run(a.create_order({}, {"name": "n"}))
    assert res["error"] == "заполните токен или логин с паролем"


def test_ionos_reports_the_vendor_reason_once_without_the_token(monkeypatch):
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return _ion_catalog(request)
        posts["n"] += 1
        return httpx.Response(422, json={"httpStatus": 422, "messages": [
            {"errorCode": "300", "message": f"quota exceeded for {_ION_CREDS['token']}"},
        ]})

    a = IonosAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_ION_CREDS, {
        "region": _DC, "image": "img-ubuntu", "name": "n",
        "cpu": 2, "ram_gb": 4, "disk_gb": 20,
    }))

    assert res["ok"] is False and posts["n"] == 1
    assert "quota exceeded" in res["error"]
    assert _ION_CREDS["token"] not in res["error"], "секрет в текст ошибки не идёт"


def test_ionos_ordering_survives_a_dead_vendor(monkeypatch):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"no route ({_ION_CREDS['token']})")

    a = IonosAdapter()
    _wire(monkeypatch, a, boom)

    assert asyncio.run(a.order_options(_ION_CREDS)) is None
    res = asyncio.run(a.create_order(_ION_CREDS, {
        "region": _DC, "image": "img-ubuntu", "name": "n",
        "cpu": 2, "ram_gb": 4, "disk_gb": 20,
    }))
    assert res["ok"] is False and _ION_CREDS["token"] not in res["error"]


# ═══════════════════════════════════════════════════════════════
# OVHcloud — корзина многошаговая, поэтому заказа нет
# ═══════════════════════════════════════════════════════════════
_OVH_CREDS = {"application_key": "AK", "application_secret": "s3cret-AS",
              "consumer_key": "s3cret-CK"}


def test_ovh_refuses_the_order_without_touching_the_vendor(monkeypatch):
    """cart → assign → item → configuration → checkout: набор обязательных
    параметров свой у каждого предложения, а checkout сразу списывает деньги."""
    a = OvhcloudAdapter()
    _wire(monkeypatch, a, _trap)
    res = asyncio.run(a.create_order(_OVH_CREDS, {"plan_id": "vps-le-2-2-40"}))

    assert res["ok"] is False
    assert res["error"] == ovhcloud._ORDER_UNSUPPORTED
    assert "корзину" in res["error"], "причина отказа должна быть предметной"
    # Кнопки заказа быть не должно: ручка каталога и так гейтится на CAPS.
    assert "order" not in a.CAPS


def test_ovh_catalog_price_is_converted_from_micro_cents():
    """⚠️ Каталог отдаёт ЦЕЛЫЕ микро-центы: 359000000 — это 3.59, а не 359 млн."""
    price, period = ovhcloud.catalog_price([
        {"capacities": ["installation"], "price": 0, "intervalUnit": "none"},
        {"capacities": ["renew"], "price": 359000000, "interval": 1,
         "intervalUnit": "month"},
    ])
    assert price == pytest.approx(3.59) and period == "month"

    # Форма корзины (`{"value": …}`) — уже в валюте, делить её нельзя.
    price, _period = ovhcloud.catalog_price(
        [{"capacities": ["renew"], "price": {"value": 5.99}}])
    assert price == pytest.approx(5.99)

    assert ovhcloud.catalog_price([]) == (None, "")
    assert ovhcloud.catalog_price(None) == (None, "")


def test_ovh_order_options_read_the_public_catalog(monkeypatch):
    """Каталог справочный, но живой: подразделение и валюта берутся у вендора,
    а не подставляются «французскими по умолчанию»."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/1.0/auth/time":
            return httpx.Response(200, text="1900000000")
        if request.url.path == "/1.0/me":
            return httpx.Response(200, json={"ovhSubsidiary": "DE",
                                             "currency": {"code": "EUR"}})
        assert request.url.params["ovhSubsidiary"] == "DE"
        return httpx.Response(200, json={
            "locale": {"currencyCode": "EUR", "subsidiary": "DE"},
            "plans": [
                {"planCode": "vps-le-2-2-40", "invoiceName": "VPS LE 2",
                 "pricings": [{"capacities": ["renew"], "price": 359000000,
                               "interval": 1, "intervalUnit": "month"}]},
                {"invoiceName": "без planCode — заказывать нечем"},
            ],
        })

    a = OvhcloudAdapter()
    _wire(monkeypatch, a, handler)
    opts = asyncio.run(a.order_options(_OVH_CREDS))

    assert opts is not None
    assert [p.id for p in opts.plans] == ["vps-le-2-2-40"]
    assert opts.plans[0].price == pytest.approx(3.59)
    assert opts.plans[0].currency == "EUR" and opts.plans[0].period == "month"
    # Площадка и ОС — это конфигурация позиции корзины, которой у нас нет.
    assert opts.regions == [] and opts.images == [] and opts.custom is None


def test_ovh_catalog_is_dropped_when_the_subsidiary_is_unreadable(monkeypatch):
    """Подставить «FR» — значит показать чужие цены в чужой валюте."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/1.0/auth/time":
            return httpx.Response(200, text="1900000000")
        if request.url.path == "/1.0/me":
            return httpx.Response(200, json={"ovhSubsidiary": ""})
        raise AssertionError(f"каталог тянуть было нечем: {request.url}")

    a = OvhcloudAdapter()
    _wire(monkeypatch, a, handler)
    assert asyncio.run(a.order_options(_OVH_CREDS)) is None


# ═══════════════════════════════════════════════════════════════
# Infomaniak — публичной ручки заказа нет
# ═══════════════════════════════════════════════════════════════
def test_infomaniak_refuses_the_order_and_points_at_openstack(monkeypatch):
    """Угаданный POST сюда стоил бы чужих денег. Зато Public Cloud у них —
    OpenStack, и он уже покрыт своим адаптером."""
    a = InfomaniakAdapter()
    _wire(monkeypatch, a, _trap)
    res = asyncio.run(a.create_order({"token": "s3cret-ik"},
                                     {"plan_id": "x", "name": "n"}))

    assert res["ok"] is False
    assert res["error"] == infomaniak._ORDER_UNSUPPORTED
    assert "OpenStack" in res["error"], "отказ обязан предлагать замену"
    assert "order" not in a.CAPS
    # Каталога заказа тоже нет — контракт отдаёт базовый None, без запросов.
    assert asyncio.run(a.order_options({"token": "s3cret-ik"})) is None
