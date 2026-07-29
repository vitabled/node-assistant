"""Заказ у AWS (EC2 RunInstances) и Alibaba Cloud (ECS RunInstances).

Живых вызовов нет: у обоих адаптеров подменяется `_client()` на MockTransport.

Обе подписи пересобираются **независимо прямо в тесте** (stdlib, без кода
адаптеров) — иначе тест подтверждал бы сам себя и молча пережил бы, например,
подписанный, но не отправленный заголовок. Момент времени и nonce для этого
фиксируются monkeypatch-ем.

Что здесь важнее маппинга полей:

- **создающий запрос уходит РОВНО один раз** — заказ тратит деньги, и «повторим
  на всякий случай» означает второй оплаченный сервер;
- **нехватка обязательного идентификатора даёт отказ БЕЗ запроса** к чужому API:
  у AWS это тип и AMI, у Alibaba — ещё и подсеть с группой безопасности, которых
  пользователю ввести негде;
- **секрет не попадает в текст ошибки**, даже когда вендор вернул его сам.

Формы ответов взяты из документации вендоров: Query/XML `instancesSet/item/
instanceId` у EC2 и двойная вложенность `Regions.Region[]` / `InstanceIdSets.
InstanceIdSet[]` у RPC Alibaba.
"""
import asyncio
import base64
import hashlib
import hmac
import urllib.parse

import httpx
import pytest

from app.services.hosting_providers import alibaba, aws
from app.services.hosting_providers.alibaba import AlibabaAdapter
from app.services.hosting_providers.aws import AwsAdapter
from app.services.hosting_providers.base import ProviderAdapter


def _wire(monkeypatch, adapter, handler):
    monkeypatch.setattr(
        adapter, "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _form(request: httpx.Request) -> dict:
    """Тело Query-запроса EC2 → словарь."""
    return dict(urllib.parse.parse_qsl(request.content.decode()))


# ═══════════════════════════════════════════════════════════════
# AWS — EC2 Query + SigV4 со service="ec2"
# ═══════════════════════════════════════════════════════════════
_STAMP = "20260728T101112Z"
_AWS_CREDS = {"access_key_id": "AKIAEXAMPLE", "secret_access_key": "s3cret-aws"}

_XMLNS = 'xmlns="http://ec2.amazonaws.com/doc/2016-11-15/"'

_EC2_TYPES = f"""<?xml version="1.0" encoding="UTF-8"?>
<DescribeInstanceTypesResponse {_XMLNS}>
  <instanceTypeSet>
    <item>
      <instanceType>m5.large</instanceType>
      <vCpuInfo><defaultVCpus>2</defaultVCpus></vCpuInfo>
      <memoryInfo><sizeInMiB>8192</sizeInMiB></memoryInfo>
      <processorInfo><supportedArchitectures>
        <item>x86_64</item>
      </supportedArchitectures></processorInfo>
    </item>
    <item>
      <instanceType>t3.micro</instanceType>
      <vCpuInfo><defaultVCpus>2</defaultVCpus></vCpuInfo>
      <memoryInfo><sizeInMiB>1024</sizeInMiB></memoryInfo>
      <processorInfo><supportedArchitectures>
        <item>x86_64</item>
      </supportedArchitectures></processorInfo>
    </item>
  </instanceTypeSet>
</DescribeInstanceTypesResponse>"""

_EC2_REGIONS = f"""<DescribeRegionsResponse {_XMLNS}>
  <regionInfo>
    <item>
      <regionName>us-east-1</regionName>
      <regionEndpoint>ec2.us-east-1.amazonaws.com</regionEndpoint>
      <optInStatus>opt-in-not-required</optInStatus>
    </item>
  </regionInfo>
</DescribeRegionsResponse>"""

_EC2_IMAGES = f"""<DescribeImagesResponse {_XMLNS}>
  <imagesSet>
    <item>
      <imageId>ami-old</imageId>
      <name>al2023-ami-2023.1.20250101-x86_64</name>
      <creationDate>2025-01-01T00:00:00.000Z</creationDate>
    </item>
    <item>
      <imageId>ami-new</imageId>
      <name>ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-20260701</name>
      <creationDate>2026-07-01T00:00:00.000Z</creationDate>
    </item>
    <item>
      <name>без идентификатора</name>
      <creationDate>2026-07-02T00:00:00.000Z</creationDate>
    </item>
  </imagesSet>
</DescribeImagesResponse>"""


def _ec2_catalog(request: httpx.Request) -> httpx.Response:
    action = _form(request).get("Action", "")
    body = {"DescribeInstanceTypes": _EC2_TYPES,
            "DescribeRegions": _EC2_REGIONS,
            "DescribeImages": _EC2_IMAGES}.get(action)
    if body is None:
        raise AssertionError(f"неожиданная операция каталога: {action}")
    return httpx.Response(200, text=body)


def _expected_ec2_sigv4(access_key: str, secret: str, region: str, host: str,
                        body: bytes, headers: dict) -> str:
    """Независимая реализация SigV4 для EC2 (service=ec2, без x-amz-target)."""
    signed_names = ["content-type", "host", "x-amz-date"]
    canon_headers = "".join(f"{n}:{headers[n]}\n" for n in signed_names)
    canonical = "\n".join([
        "POST", "/", "",
        canon_headers,
        ";".join(signed_names),
        hashlib.sha256(body).hexdigest(),
    ])
    datestamp = _STAMP[:8]
    scope = f"{datestamp}/{region}/ec2/aws4_request"
    sts = "\n".join(["AWS4-HMAC-SHA256", _STAMP, scope,
                     hashlib.sha256(canonical.encode()).hexdigest()])

    def mac(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    key = mac(("AWS4" + secret).encode(), datestamp)
    for part in (region, "ec2", "aws4_request"):
        key = mac(key, part)
    signature = hmac.new(key, sts.encode(), hashlib.sha256).hexdigest()
    return (f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
            f"SignedHeaders={';'.join(signed_names)}, Signature={signature}")


def test_aws_order_signs_ec2_not_cost_explorer(monkeypatch):
    """Служба в scope подписи — `ec2`, а не `ce`; цели (`x-amz-target`) у Query
    нет, и подписанный, но не отправленный заголовок сорвал бы проверку."""
    monkeypatch.setattr(aws, "amz_date", lambda *a, **k: _STAMP)
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["host"] = request.url.host
        seen["body"] = request.content
        seen["headers"] = {k.lower(): v for k, v in request.headers.items()}
        return _ec2_catalog(request)

    a = AwsAdapter()
    _wire(monkeypatch, a, handler)
    asyncio.run(a.order_options(_AWS_CREDS))

    assert seen["host"] == "ec2.us-east-1.amazonaws.com", "другая служба, другой хост"
    assert "x-amz-target" not in seen["headers"], "у Query-протокола цели нет"
    assert seen["headers"]["content-type"].startswith(
        "application/x-www-form-urlencoded")
    expected = _expected_ec2_sigv4(_AWS_CREDS["access_key_id"],
                                   _AWS_CREDS["secret_access_key"], "us-east-1",
                                   seen["host"], seen["body"], seen["headers"])
    assert seen["headers"]["authorization"] == expected

    # Подпись покрывает тело: другое тело — другая подпись.
    tampered = _expected_ec2_sigv4(_AWS_CREDS["access_key_id"],
                                   _AWS_CREDS["secret_access_key"], "us-east-1",
                                   seen["host"], seen["body"] + b"&x=1",
                                   seen["headers"])
    assert seen["headers"]["authorization"] != tampered


def test_aws_order_options_read_the_namespaced_xml(monkeypatch):
    """Ответ Query — XML со СВОИМ xmlns: поиск по полному тегу не нашёл бы ничего."""
    monkeypatch.setattr(aws, "amz_date", lambda *a, **k: _STAMP)
    a = AwsAdapter()
    _wire(monkeypatch, a, _ec2_catalog)
    opts = asyncio.run(a.order_options(_AWS_CREDS))

    assert opts is not None and opts.custom is None, "у EC2 только готовые типы"
    # Порядок — по ядрам и памяти, а не как отдал вендор.
    assert [p.id for p in opts.plans] == ["t3.micro", "m5.large"]
    assert "1 ГБ RAM" in opts.plans[0].specs, "sizeInMiB — мебибайты, не гигабайты"
    assert "2 vCPU" in opts.plans[0].specs and "x86_64" in opts.plans[0].specs
    # Цены EC2 в этом API нет — выдумывать её нельзя.
    assert all(p.price is None and p.currency == "" for p in opts.plans)

    # Регион ровно один — из кредов: AMI региональны, а форма связывать селекторы
    # не умеет.
    assert [r["id"] for r in opts.regions] == ["us-east-1"]
    assert "ec2.us-east-1.amazonaws.com" in opts.regions[0]["name"]
    # Свежие сначала, запись без imageId пропущена.
    assert [i["id"] for i in opts.images] == ["ami-new", "ami-old"]


def test_aws_image_query_is_bounded_to_a_short_list(monkeypatch):
    """AMI у EC2 десятки тысяч: без владельцев и шаблонов имён форма заказа
    тянула бы мегабайты."""
    monkeypatch.setattr(aws, "amz_date", lambda *a, **k: _STAMP)
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        params = _form(request)
        if params.get("Action") == "DescribeImages":
            seen.update(params)
        return _ec2_catalog(request)

    a = AwsAdapter()
    _wire(monkeypatch, a, handler)
    asyncio.run(a.order_options(_AWS_CREDS))

    assert seen["Owner.1"] == "amazon" and seen["Owner.2"] == "099720109477"
    assert seen["Filter.4.Name"] == "name" and seen["Filter.4.Value.1"]
    assert int(seen["MaxResults"]) <= 1000
    assert seen["Version"] == "2016-11-15"


def test_aws_create_order_runs_instances_once_with_the_name_tag(monkeypatch):
    """У EC2 нет поля имени — имя это тег Name; создающий запрос ровно один."""
    monkeypatch.setattr(aws, "amz_date", lambda *a, **k: _STAMP)
    runs = {"n": 0}
    sent: dict = {}
    actions: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = _form(request)
        actions.append(params.get("Action", ""))
        if params.get("Action") != "RunInstances":
            return _ec2_catalog(request)
        runs["n"] += 1
        sent.update(params)
        return httpx.Response(200, text=f"""<RunInstancesResponse {_XMLNS}>
          <reservationId>r-123</reservationId>
          <instancesSet><item>
            <instanceId>i-0abc123</instanceId>
            <instanceType>t3.micro</instanceType>
          </item></instancesSet>
        </RunInstancesResponse>""")

    a = AwsAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_AWS_CREDS, {
        "plan_id": "t3.micro", "image": "ami-new", "name": "node-1",
        "region": "eu-west-1",
    }))

    assert res["ok"] is True and res["id"] == "i-0abc123" and res["name"] == "node-1"
    assert res["price"] is None and res["currency"] == ""
    assert runs["n"] == 1, "создающий запрос ровно один"
    assert actions == ["RunInstances"], "заказ не ходит в каталог и никуда больше"
    assert sent["ImageId"] == "ami-new" and sent["InstanceType"] == "t3.micro"
    assert sent["MinCount"] == "1" and sent["MaxCount"] == "1"
    assert sent["TagSpecification.1.ResourceType"] == "instance"
    assert sent["TagSpecification.1.Tag.1.Key"] == "Name"
    assert sent["TagSpecification.1.Tag.1.Value"] == "node-1"


def test_aws_order_honours_the_region_from_the_spec(monkeypatch):
    """Регион определяет ХОСТ запроса — он обязан приехать из выбора, а не из
    дефолта кредов."""
    monkeypatch.setattr(aws, "amz_date", lambda *a, **k: _STAMP)
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["host"] = request.url.host
        return httpx.Response(200, text=f"""<RunInstancesResponse {_XMLNS}>
          <instancesSet><item><instanceId>i-1</instanceId></item></instancesSet>
        </RunInstancesResponse>""")

    a = AwsAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_AWS_CREDS, {
        "plan_id": "t3.micro", "image": "ami-1", "name": "n", "region": "eu-central-1",
    }))

    assert res["ok"] is True
    assert seen["host"] == "ec2.eu-central-1.amazonaws.com"


@pytest.mark.parametrize("spec, expect", [
    ({"image": "ami-1", "name": "n"}, "тип инстанса"),
    ({"plan_id": "t3.micro", "name": "n"}, "образ"),
    # Регион уходит в имя хоста — мусор отсекается до сети (урок oracle.py).
    ({"plan_id": "t3.micro", "image": "ami-1", "region": "us-east-1/evil.example.com"},
     "регион"),
])
def test_aws_refuses_an_incomplete_order_before_the_network(monkeypatch, spec, expect):
    def trap(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"отказ обязан быть офлайновым, а ушёл запрос: {request.url}")

    a = AwsAdapter()
    _wire(monkeypatch, a, trap)
    res = asyncio.run(a.create_order(_AWS_CREDS, spec))

    assert res["ok"] is False and expect in res["error"]


def test_aws_reports_the_ec2_refusal_once_and_without_the_secret(monkeypatch):
    """Причина словами вендора, один запрос, ключи не в тексте ошибки."""
    monkeypatch.setattr(aws, "amz_date", lambda *a, **k: _STAMP)
    runs = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        runs["n"] += 1
        return httpx.Response(400, text=f"""<Response><Errors><Error>
          <Code>InsufficientInstanceCapacity</Code>
          <Message>not enough capacity for {_AWS_CREDS['access_key_id']}</Message>
        </Error></Errors><RequestID>r-1</RequestID></Response>""")

    a = AwsAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_AWS_CREDS, {
        "plan_id": "t3.micro", "image": "ami-1", "name": "n",
    }))

    assert res["ok"] is False and runs["n"] == 1, "отказ — это ответ, а не повод повторить"
    assert "InsufficientInstanceCapacity" in res["error"]
    assert _AWS_CREDS["access_key_id"] not in res["error"]
    assert _AWS_CREDS["secret_access_key"] not in res["error"]


def test_aws_maps_an_ec2_auth_code_to_bad_credentials(monkeypatch):
    monkeypatch.setattr(aws, "amz_date", lambda *a, **k: _STAMP)
    a = AwsAdapter()
    _wire(monkeypatch, a, lambda r: httpx.Response(401, text="""<Response><Errors>
        <Error><Code>AuthFailure</Code><Message>bad key</Message></Error>
      </Errors></Response>"""))
    res = asyncio.run(a.create_order(_AWS_CREDS, {
        "plan_id": "t3.micro", "image": "ami-1", "name": "n",
    }))

    assert res["ok"] is False and res["error"] == "неверные креды"


def test_aws_answer_without_an_instance_is_not_called_a_failure(monkeypatch):
    """Инстанс мог быть создан и уже тарифицироваться — молчаливое «не получилось»
    здесь опаснее, чем просьба заглянуть в консоль."""
    monkeypatch.setattr(aws, "amz_date", lambda *a, **k: _STAMP)
    a = AwsAdapter()
    _wire(monkeypatch, a, lambda r: httpx.Response(
        200, text=f"<RunInstancesResponse {_XMLNS}><reservationId>r-1</reservationId>"
                  f"</RunInstancesResponse>"))
    res = asyncio.run(a.create_order(_AWS_CREDS, {
        "plan_id": "t3.micro", "image": "ami-1", "name": "n",
    }))

    assert res["ok"] is False and "проверьте консоль" in res["error"]


def test_aws_ordering_survives_a_dead_vendor(monkeypatch):
    monkeypatch.setattr(aws, "amz_date", lambda *a, **k: _STAMP)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"refused for {_AWS_CREDS['secret_access_key']}")

    a = AwsAdapter()
    _wire(monkeypatch, a, handler)

    assert asyncio.run(a.order_options(_AWS_CREDS)) is None
    res = asyncio.run(a.create_order(_AWS_CREDS, {
        "plan_id": "t3.micro", "image": "ami-1", "name": "n",
    }))
    assert res["ok"] is False
    assert _AWS_CREDS["secret_access_key"] not in res["error"]


def test_aws_cost_explorer_path_is_untouched_by_the_order_support(monkeypatch):
    """Подпись стала общей на два сервиса — у Cost Explorer обязаны остаться свои
    хост, цель и scope со службой `ce`."""
    monkeypatch.setattr(aws, "amz_date", lambda *a, **k: _STAMP)
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["host"] = request.url.host
        seen["target"] = request.headers.get("x-amz-target")
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"ResultsByTime": []})

    a = AwsAdapter()
    _wire(monkeypatch, a, handler)
    asyncio.run(a.verify(_AWS_CREDS))

    assert seen["host"] == "ce.us-east-1.amazonaws.com"
    assert seen["target"] == "AWSInsightsIndexService.GetCostAndUsage"
    assert "/us-east-1/ce/aws4_request" in seen["auth"]
    assert "x-amz-target" in seen["auth"], "цель обязана быть в SignedHeaders"


# ═══════════════════════════════════════════════════════════════
# Alibaba — ECS RunInstances (RPC HMAC-SHA1)
# ═══════════════════════════════════════════════════════════════
_NONCE = "0123456789abcdef0123456789abcdef"
_TS = "2026-07-28T10:11:12Z"
_ALI_CREDS = {"access_key_id": "LTAI-example", "access_key_secret": "s3cret-ali"}


@pytest.fixture
def _freeze_alibaba(monkeypatch):
    monkeypatch.setattr(alibaba, "_nonce", lambda: _NONCE)
    monkeypatch.setattr(alibaba, "_timestamp", lambda: _TS)


def _expected_alibaba_signature(params: dict, secret: str) -> str:
    """Независимая сборка подписи по правилам Alibaba (без кода адаптера)."""
    def enc(value: str) -> str:
        out = urllib.parse.quote(str(value), safe="")
        return out.replace("+", "%20").replace("*", "%2A").replace("%7E", "~")

    canonical = "&".join(f"{enc(k)}={enc(v)}" for k, v in sorted(params.items()))
    sts = "GET&" + enc("/") + "&" + enc(canonical)
    return base64.b64encode(
        hmac.new((secret + "&").encode(), sts.encode(), hashlib.sha1).digest()
    ).decode()


_ALI_CATALOG = {
    "DescribeInstanceTypes": {"InstanceTypes": {"InstanceType": [
        {"InstanceTypeId": "ecs.g6.large", "CpuCoreCount": 2, "MemorySize": 8.0},
        {"InstanceTypeId": "ecs.t6-c1m1.large", "CpuCoreCount": 1, "MemorySize": 1.0},
    ]}},
    "DescribeRegions": {"Regions": {"Region": [
        {"RegionId": "cn-hangzhou", "LocalName": "Ханчжоу"},
        {"RegionId": "eu-central-1", "LocalName": "Франкфурт"},
    ]}},
    "DescribeImages": {"Images": {"Image": [
        {"ImageId": "ubuntu_24_04_x64", "OSNameEn": "Ubuntu 24.04 64-bit"},
        {"ImageName": "без идентификатора"},
    ]}},
    "DescribeVSwitches": {"VSwitches": {"VSwitch": [
        # Первая подсеть — в VPC, где НЕТ группы безопасности: адаптер обязан
        # дойти до второй, иначе вендор откажет «разные VPC».
        {"VSwitchId": "vsw-lonely", "VpcId": "vpc-empty", "Status": "Available"},
        {"VSwitchId": "vsw-ok", "VpcId": "vpc-main", "Status": "Available"},
    ]}},
    "DescribeSecurityGroups": {"SecurityGroups": {"SecurityGroup": [
        {"SecurityGroupId": "sg-main", "VpcId": "vpc-main"},
    ]}},
}


def _ali_catalog(request: httpx.Request) -> httpx.Response:
    action = request.url.params.get("Action", "")
    if action not in _ALI_CATALOG:
        raise AssertionError(f"неожиданная операция каталога: {action}")
    return httpx.Response(200, json=_ALI_CATALOG[action])


def test_alibaba_order_calls_ecs_with_its_own_domain_and_version(_freeze_alibaba,
                                                                monkeypatch):
    """Домен и версия у ECS свои; подпись — та же RPC-схема, пересчитанная здесь."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("Action") == "DescribeInstanceTypes":
            seen["host"] = request.url.host
            seen["params"] = dict(request.url.params)
        return _ali_catalog(request)

    a = AlibabaAdapter()
    _wire(monkeypatch, a, handler)
    asyncio.run(a.order_options(_ALI_CREDS))

    assert seen["host"] == "ecs.aliyuncs.com", "BSS-домен для ECS не годится"
    params = seen["params"]
    assert params["Version"] == "2014-05-26", "версия ECS, а не BSS 2017-12-14"
    assert params["SignatureNonce"] == _NONCE and params["Timestamp"] == _TS

    signed = {k: v for k, v in params.items() if k != "Signature"}
    assert params["Signature"] == _expected_alibaba_signature(
        signed, _ALI_CREDS["access_key_secret"])


def test_alibaba_order_options_map_the_double_nested_lists(_freeze_alibaba,
                                                           monkeypatch):
    """⚠️ Успешный ответ ECS не несёт ни `Code`, ни `Success` — строгая проверка
    конверта BSS посчитала бы весь каталог ошибкой."""
    a = AlibabaAdapter()
    _wire(monkeypatch, a, _ali_catalog)
    opts = asyncio.run(a.order_options(_ALI_CREDS))

    assert opts is not None and opts.custom is None
    # Порядок — по ядрам и памяти.
    assert [p.id for p in opts.plans] == ["ecs.t6-c1m1.large", "ecs.g6.large"]
    assert "8 ГБ RAM" in opts.plans[1].specs, "MemorySize у ECS уже в гигабайтах"
    assert all(p.price is None and p.currency == "" for p in opts.plans)

    # Регион ровно один — из кредов (образы Alibaba региональны).
    assert [r["id"] for r in opts.regions] == ["cn-hangzhou"]
    assert opts.regions[0]["name"] == "Ханчжоу (cn-hangzhou)"
    assert [i["id"] for i in opts.images] == ["ubuntu_24_04_x64"]


def test_alibaba_create_order_derives_the_network_and_runs_once(_freeze_alibaba,
                                                                monkeypatch):
    """Подсеть и группа безопасности обязаны быть в ОДНОЙ VPC, а вводить их
    негде — адаптер выводит пару из каталога."""
    runs = {"n": 0}
    sent: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if params.get("Action") != "RunInstances":
            return _ali_catalog(request)
        runs["n"] += 1
        sent.update(params)
        return httpx.Response(200, json={
            "RequestId": "req-1",
            "InstanceIdSets": {"InstanceIdSet": ["i-bp67acfmxazb4p"]},
        })

    a = AlibabaAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_ALI_CREDS, {
        "plan_id": "ecs.g6.large", "image": "ubuntu_24_04_x64", "name": "node-1",
    }))

    assert res["ok"] is True and res["id"] == "i-bp67acfmxazb4p"
    assert res["name"] == "node-1" and res["price"] is None and res["currency"] == ""
    assert runs["n"] == 1, "создающий запрос ровно один"
    assert sent["RegionId"] == "cn-hangzhou" and sent["Amount"] == "1"
    assert sent["InstanceType"] == "ecs.g6.large"
    assert sent["ImageId"] == "ubuntu_24_04_x64"
    assert sent["InstanceName"] == "node-1"
    # Пара выбрана по совпадению VpcId, а не «первая попавшаяся».
    assert sent["VSwitchId"] == "vsw-ok" and sent["SecurityGroupId"] == "sg-main"
    # Публичная полоса — отдельные деньги за трафик, по умолчанию не просим.
    assert "InternetMaxBandwidthOut" not in sent
    assert "Password" not in sent, "пароль не задаём — вернуть его некуда"


def test_alibaba_refuses_without_a_network_and_without_creating(_freeze_alibaba,
                                                                monkeypatch):
    """Нет подсети — отказ словами ДО создающего запроса, а не запрос наугад."""
    runs = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        action = request.url.params.get("Action", "")
        if action == "RunInstances":
            runs["n"] += 1
            raise AssertionError("создающий запрос при неполных данных")
        if action == "DescribeVSwitches":
            return httpx.Response(200, json={"VSwitches": {"VSwitch": []}})
        return _ali_catalog(request)

    a = AlibabaAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_ALI_CREDS, {
        "plan_id": "ecs.g6.large", "image": "img", "name": "n",
    }))

    assert res["ok"] is False and runs["n"] == 0
    assert "подсет" in res["error"] and "cn-hangzhou" in res["error"]


def test_alibaba_refuses_when_no_group_shares_the_vpc(_freeze_alibaba, monkeypatch):
    """Группа из другой VPC — это гарантированный отказ вендора; не отправляем."""
    runs = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        action = request.url.params.get("Action", "")
        if action == "RunInstances":
            runs["n"] += 1
            raise AssertionError("создающий запрос с несовместимой парой сетей")
        if action == "DescribeSecurityGroups":
            return httpx.Response(200, json={"SecurityGroups": {"SecurityGroup": [
                {"SecurityGroupId": "sg-elsewhere", "VpcId": "vpc-other"},
            ]}})
        return _ali_catalog(request)

    a = AlibabaAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_ALI_CREDS, {
        "plan_id": "ecs.g6.large", "image": "img", "name": "n",
    }))

    assert res["ok"] is False and runs["n"] == 0
    assert "группы безопасности" in res["error"] and "VPC" in res["error"]


@pytest.mark.parametrize("spec, expect", [
    ({"image": "img", "name": "n"}, "тип инстанса"),
    ({"plan_id": "ecs.g6.large", "name": "n"}, "образ"),
    ({"plan_id": "ecs.g6.large", "image": "img", "region": "cn-hangzhou/evil"},
     "регион"),
])
def test_alibaba_refuses_an_incomplete_order_before_the_network(monkeypatch, spec,
                                                                expect):
    def trap(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"отказ обязан быть офлайновым, а ушёл запрос: {request.url}")

    a = AlibabaAdapter()
    _wire(monkeypatch, a, trap)
    res = asyncio.run(a.create_order(_ALI_CREDS, spec))

    assert res["ok"] is False and expect in res["error"]


def test_alibaba_reports_the_ecs_refusal_once_and_without_the_secret(_freeze_alibaba,
                                                                     monkeypatch):
    runs = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("Action") != "RunInstances":
            return _ali_catalog(request)
        runs["n"] += 1
        # ECS отвечает на отказ 4xx с кодом и сообщением в теле.
        return httpx.Response(400, json={
            "Code": "QuotaExceed.ElasticQuota",
            "Message": f"quota exceeded for {_ALI_CREDS['access_key_secret']}",
        })

    a = AlibabaAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_ALI_CREDS, {
        "plan_id": "ecs.g6.large", "image": "img", "name": "n",
    }))

    assert res["ok"] is False and runs["n"] == 1
    assert "QuotaExceed.ElasticQuota" in res["error"], "причина словами вендора"
    assert _ALI_CREDS["access_key_secret"] not in res["error"]
    assert urllib.parse.quote(_ALI_CREDS["access_key_secret"],
                              safe="") not in res["error"]


def test_alibaba_answer_without_an_instance_is_not_called_a_failure(_freeze_alibaba,
                                                                    monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("Action") != "RunInstances":
            return _ali_catalog(request)
        return httpx.Response(200, json={"RequestId": "req-1"})

    a = AlibabaAdapter()
    _wire(monkeypatch, a, handler)
    res = asyncio.run(a.create_order(_ALI_CREDS, {
        "plan_id": "ecs.g6.large", "image": "img", "name": "n",
    }))

    assert res["ok"] is False and "проверьте консоль" in res["error"]


def test_alibaba_bss_envelope_check_stays_strict(_freeze_alibaba, monkeypatch):
    """Послабление конверта сделано ТОЛЬКО для ECS: у BSS ответ без явного успеха
    по-прежнему ошибка, иначе баланс читался бы из отказа."""
    a = AlibabaAdapter()
    _wire(monkeypatch, a, lambda r: httpx.Response(200, json={
        "Data": {"AvailableAmount": "10.00", "Currency": "CNY"},
    }))

    assert alibaba._ok({"Data": {}}, strict=True) is False
    assert alibaba._ok({"Data": {}}, strict=False) is True
    assert asyncio.run(a.balance(_ALI_CREDS)) is None


def test_alibaba_ordering_survives_a_dead_vendor(_freeze_alibaba, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"refused for {_ALI_CREDS['access_key_secret']}")

    a = AlibabaAdapter()
    _wire(monkeypatch, a, handler)

    assert asyncio.run(a.order_options(_ALI_CREDS)) is None
    res = asyncio.run(a.create_order(_ALI_CREDS, {
        "plan_id": "ecs.g6.large", "image": "img", "name": "n",
    }))
    assert res["ok"] is False
    assert _ALI_CREDS["access_key_secret"] not in res["error"]


def test_both_adapters_advertise_order_only_because_it_is_implemented():
    """CAPS — контракт для формы: «order» без реализации даёт кнопку, которая
    молча ничего не создаёт."""
    for adapter in (AwsAdapter(), AlibabaAdapter()):
        assert "order" in adapter.CAPS
        cls = type(adapter)
        assert cls.create_order is not ProviderAdapter.create_order
        assert cls.order_options is not ProviderAdapter.order_options
        # Предварительного расчёта нет ни там, ни там — цену вендор не называет.
        assert asyncio.run(adapter.quote_order({}, {})) is None
