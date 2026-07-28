"""Адаптеры больших облаков: AWS (SigV4), Alibaba (RPC HMAC-SHA1), Cloud.ru (IAM).

Живых вызовов нет — у каждого адаптера подменяется `_client()` на MockTransport.

Подписи проверяются **независимым пересчётом прямо в тесте**: если бы ожидание
считалось теми же функциями адаптера, тест подтверждал бы сам себя и молча
пережил бы, например, потерянный заголовок в canonical request. Момент времени и
nonce для этого фиксируются monkeypatch-ем — иначе подпись недетерминирована.
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
from app.services.hosting_providers.cloudru import CloudruAdapter


def _wire(monkeypatch, adapter, handler):
    monkeypatch.setattr(
        adapter, "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _json(payload, status=200):
    return lambda request: httpx.Response(status, json=payload)


# ── AWS: SigV4 ────────────────────────────────────────────────
_STAMP = "20260728T101112Z"


def _expected_sigv4(access_key: str, secret: str, region: str, host: str,
                    body: bytes, headers: dict[str, str]) -> str:
    """Независимая реализация SigV4 (stdlib, без кода адаптера)."""
    signed_names = ["content-type", "host", "x-amz-date", "x-amz-target"]
    canon_headers = "".join(f"{n}:{headers[n]}\n" for n in signed_names)
    canonical = "\n".join([
        "POST", "/", "",
        canon_headers,
        ";".join(signed_names),
        hashlib.sha256(body).hexdigest(),
    ])
    datestamp = _STAMP[:8]
    scope = f"{datestamp}/{region}/ce/aws4_request"
    sts = "\n".join([
        "AWS4-HMAC-SHA256", _STAMP, scope,
        hashlib.sha256(canonical.encode()).hexdigest(),
    ])

    def mac(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    key = mac(("AWS4" + secret).encode(), datestamp)
    key = mac(key, region)
    key = mac(key, "ce")
    key = mac(key, "aws4_request")
    signature = hmac.new(key, sts.encode(), hashlib.sha256).hexdigest()
    return (f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
            f"SignedHeaders={';'.join(signed_names)}, Signature={signature}")


def test_aws_authorization_matches_an_independent_sigv4(monkeypatch):
    monkeypatch.setattr(aws, "amz_date", lambda *a, **k: _STAMP)
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.content
        seen["headers"] = {k.lower(): v for k, v in request.headers.items()}
        seen["host"] = request.url.host
        seen["target"] = request.headers.get("x-amz-target")
        return httpx.Response(200, json={"ResultsByTime": []})

    a = AwsAdapter()
    _wire(monkeypatch, a, handler)
    creds = {"access_key_id": "AKIAEXAMPLE", "secret_access_key": "s3cret-aws"}
    asyncio.run(a.verify(creds))

    assert seen["host"] == "ce.us-east-1.amazonaws.com", "регион по умолчанию"
    assert seen["target"] == "AWSInsightsIndexService.GetCostAndUsage"
    assert seen["headers"]["content-type"] == "application/x-amz-json-1.1"
    assert seen["headers"]["x-amz-date"] == _STAMP

    expected = _expected_sigv4("AKIAEXAMPLE", "s3cret-aws", "us-east-1",
                               seen["host"], seen["body"], seen["headers"])
    assert seen["auth"] == expected


def test_aws_signature_covers_the_body(monkeypatch):
    """Тело входит в canonical request хешем — подпись под ДРУГОЕ тело не сходится."""
    monkeypatch.setattr(aws, "amz_date", lambda *a, **k: _STAMP)
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.content
        seen["headers"] = {k.lower(): v for k, v in request.headers.items()}
        return httpx.Response(200, json={"ResultsByTime": []})

    a = AwsAdapter()
    _wire(monkeypatch, a, handler)
    asyncio.run(a.verify({"access_key_id": "AKIA", "secret_access_key": "sec"}))

    tampered = _expected_sigv4("AKIA", "sec", "us-east-1", "ce.us-east-1.amazonaws.com",
                               seen["body"] + b" ", seen["headers"])
    assert seen["auth"] != tampered


def test_aws_body_asks_for_the_current_month(monkeypatch):
    monkeypatch.setattr(aws, "amz_date", lambda *a, **k: _STAMP)
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"ResultsByTime": []})

    a = AwsAdapter()
    _wire(monkeypatch, a, handler)
    asyncio.run(a.verify({"access_key_id": "AKIA", "secret_access_key": "sec"}))

    import json as _json_mod
    body = _json_mod.loads(seen["body"])
    assert body["Granularity"] == "MONTHLY" and body["Metrics"] == ["UnblendedCost"]
    start, end = body["TimePeriod"]["Start"], body["TimePeriod"]["End"]
    assert start.endswith("-01") and end.endswith("-01"), "границы месяца"
    assert end > start, "End у Cost Explorer — исключающая граница"


def test_aws_payments_sum_the_month_and_there_is_no_balance(monkeypatch):
    a = AwsAdapter()
    _wire(monkeypatch, a, _json({"ResultsByTime": [
        # Суммы приходят СТРОКАМИ.
        {"TimePeriod": {"Start": "2026-07-01", "End": "2026-08-01"},
         "Total": {"UnblendedCost": {"Amount": "12.345", "Unit": "USD"}}},
        {"TimePeriod": {"Start": "2026-07-01", "End": "2026-08-01"},
         "Total": {"UnblendedCost": {"Amount": "0.655", "Unit": "USD"}}},
    ]}))
    creds = {"access_key_id": "AKIA", "secret_access_key": "sec"}

    pays = asyncio.run(a.payments(creds))
    assert len(pays) == 1, "расход за месяц — одной записью"
    assert pays[0]["amount"] == pytest.approx(13.0)
    assert pays[0]["currency"] == "USD" and pays[0]["type"] == "charge"
    assert pays[0]["ts"] == "2026-07-01"

    # Баланса у AWS нет, а EC2 — другая служба: адаптер это не заявляет и не врёт.
    assert a.CAPS == {"payments"}
    assert asyncio.run(a.balance(creds)) is None
    assert asyncio.run(a.services(creds)) == []


def test_aws_reports_nothing_rather_than_a_fake_zero(monkeypatch):
    """Если вендор переименует поле суммы, запись «расход 0» соврала бы про деньги."""
    a = AwsAdapter()
    _wire(monkeypatch, a, _json({"ResultsByTime": [
        {"TimePeriod": {"Start": "2026-07-01"}, "Total": {"AmortizedCost": {"Amount": "9"}}},
    ]}))
    assert asyncio.run(a.payments({"access_key_id": "AKIA",
                                   "secret_access_key": "sec"})) == []


def test_aws_bad_region_never_reaches_the_network(monkeypatch):
    """Регион уходит в имя хоста — мусор должен отсекаться до запроса."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("запрос не должен уйти")

    a = AwsAdapter()
    _wire(monkeypatch, a, handler)
    ok, err = asyncio.run(a.verify({"access_key_id": "AKIA",
                                    "secret_access_key": "sec",
                                    "region": "us-east-1/evil.example.com"}))
    assert ok is False and "регион" in err


def test_aws_reports_the_error_type_from_the_json11_body(monkeypatch):
    a = AwsAdapter()
    _wire(monkeypatch, a, _json({"__type": "com.amazon.coral#DataUnavailableException",
                                 "message": "нет данных"}, status=400))
    ok, err = asyncio.run(a.verify({"access_key_id": "AKIA", "secret_access_key": "sec"}))
    assert ok is False and "DataUnavailableException" in err


# ── Alibaba: RPC HMAC-SHA1 ────────────────────────────────────
_NONCE = "0123456789abcdef0123456789abcdef"
_TS = "2026-07-28T10:11:12Z"


def _expected_alibaba_sts(params: dict) -> str:
    """Независимая сборка StringToSign по правилам Alibaba."""
    def enc(value: str) -> str:
        # RFC-3986 + три отличия: %20 вместо +, «~» не кодируется, «*» → %2A.
        out = urllib.parse.quote(str(value), safe="")
        return out.replace("+", "%20").replace("*", "%2A").replace("%7E", "~")

    canonical = "&".join(f"{enc(k)}={enc(v)}" for k, v in sorted(params.items()))
    return "GET&" + enc("/") + "&" + enc(canonical)


def _freeze_alibaba(monkeypatch):
    monkeypatch.setattr(alibaba, "_nonce", lambda: _NONCE)
    monkeypatch.setattr(alibaba, "_timestamp", lambda: _TS)


def test_alibaba_signature_matches_an_independent_computation(monkeypatch):
    _freeze_alibaba(monkeypatch)
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"Code": "200", "Success": True,
                                         "Data": {"AvailableAmount": "1.00",
                                                  "Currency": "CNY"}})

    a = AlibabaAdapter()
    _wire(monkeypatch, a, handler)
    secret = "s3cret-ali"
    asyncio.run(a.verify({"access_key_id": "LTAI-example", "access_key_secret": secret}))

    params = seen["params"]
    assert params["SignatureNonce"] == _NONCE and params["Timestamp"] == _TS
    assert params["SignatureMethod"] == "HMAC-SHA1" and params["Version"] == "2017-12-14"
    assert params["Action"] == "QueryAccountBalance" and params["Format"] == "JSON"

    signed = {k: v for k, v in params.items() if k != "Signature"}
    sts = _expected_alibaba_sts(signed)
    assert sts.startswith("GET&%2F&"), "StringToSign начинается с метода и кодированного пути"
    # canonical query кодируется ЦЕЛИКОМ ещё раз, поэтому «:» из Timestamp
    # доходит до StringToSign как %253A (%3A, у которого закодировали «%»).
    assert "%253A" in sts and "%26" in sts
    expected = base64.b64encode(
        hmac.new((secret + "&").encode(), sts.encode(), hashlib.sha1).digest()
    ).decode()
    assert params["Signature"] == expected


def test_alibaba_percent_encoding_follows_the_vendor_rules():
    assert alibaba.percent_encode("a b") == "a%20b", "пробел даёт %20, не +"
    assert alibaba.percent_encode("~") == "~", "«~» не кодируется"
    assert alibaba.percent_encode("*") == "%2A"
    assert alibaba.percent_encode("/") == "%2F"


def test_alibaba_balance_strips_thousand_separators(monkeypatch):
    """«1,234.56» — запятая РАЗДЕЛИТЕЛЬ ТЫСЯЧ; принять её за десятичную значит
    показать баланс 1.23 вместо 1234.56."""
    _freeze_alibaba(monkeypatch)
    a = AlibabaAdapter()
    _wire(monkeypatch, a, _json({"Code": "200", "Message": "Successful!",
                                 "Data": {"AvailableAmount": "1,234.56",
                                          "Currency": "cny"}}))
    bal = asyncio.run(a.balance({"access_key_id": "id", "access_key_secret": "s"}))
    assert bal is not None and bal.amount == pytest.approx(1234.56)
    assert bal.currency == "CNY"


def test_alibaba_payments_read_the_double_nested_items(monkeypatch):
    _freeze_alibaba(monkeypatch)
    a = AlibabaAdapter()
    _wire(monkeypatch, a, _json({"Success": True, "Data": {
        "BillingCycle": "2026-07",
        "Items": {"Item": [
            {"ProductName": "ECS", "PretaxAmount": 120.5, "Currency": "CNY"},
            {"ProductName": "OSS", "PretaxAmount": 3, "Currency": "CNY"},
        ]},
    }}))
    pays = asyncio.run(a.payments({"access_key_id": "id", "access_key_secret": "s"}))
    assert [p["note"] for p in pays] == ["ECS", "OSS"]
    assert pays[0]["amount"] == pytest.approx(120.5) and pays[0]["ts"] == "2026-07"
    assert all(p["type"] == "charge" for p in pays)


def test_alibaba_maps_the_404_auth_code_to_bad_credentials(monkeypatch):
    """Неверный AccessKeyId Alibaba отдаёт как 404 — без разбора Code пользователь
    увидел бы «ручка API не найдена»."""
    _freeze_alibaba(monkeypatch)
    a = AlibabaAdapter()
    _wire(monkeypatch, a, _json({"Code": "InvalidAccessKeyId.NotFound",
                                 "Message": "Specified access key is not found."},
                                status=404))
    ok, err = asyncio.run(a.verify({"access_key_id": "id", "access_key_secret": "s"}))
    assert ok is False and err == "неверные креды"


def test_alibaba_rejects_a_200_with_a_failure_code(monkeypatch):
    """RPC умеет отвечать 200 с ошибкой в теле — успех определяется Code/Success."""
    _freeze_alibaba(monkeypatch)
    a = AlibabaAdapter()
    _wire(monkeypatch, a, _json({"Code": "InternalError", "Success": False}))
    ok, err = asyncio.run(a.verify({"access_key_id": "id", "access_key_secret": "s"}))
    assert ok is False and err
    assert asyncio.run(a.balance({"access_key_id": "id", "access_key_secret": "s"})) is None


# ── Cloud.ru: обмен ключа на токен ────────────────────────────
def test_cloudru_exchanges_the_key_for_a_token(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"access_token": "T", "expires_in": 3600})

    a = CloudruAdapter()
    _wire(monkeypatch, a, handler)
    ok, err = asyncio.run(a.verify({"key_id": "k", "key_secret": "s"}))

    assert ok is True and err == ""
    assert seen["url"] == "https://iam.api.cloud.ru/api/v1/auth/token"
    assert '"keyId"' in seen["body"] and '"secret"' in seen["body"]


def test_cloudru_admits_it_has_no_billing_caps(monkeypatch):
    """Формы биллинговых ручек не зафиксированы публично — адаптер не заявляет
    того, чего не умеет, и не ходит по выдуманным URL."""
    a = CloudruAdapter()
    assert a.CAPS == set()
    _wire(monkeypatch, a, _json({"access_token": "T"}))
    creds = {"key_id": "k", "key_secret": "s"}
    assert asyncio.run(a.balance(creds)) is None
    assert asyncio.run(a.services(creds)) == []
    assert asyncio.run(a.payments(creds)) == []


def test_cloudru_reports_a_token_less_answer(monkeypatch):
    a = CloudruAdapter()
    _wire(monkeypatch, a, _json({"expires_in": 3600}))
    ok, err = asyncio.run(a.verify({"key_id": "k", "key_secret": "s"}))
    assert ok is False and "токен" in err


# ── Общее поведение контракта ─────────────────────────────────
@pytest.mark.parametrize("adapter, creds", [
    (AwsAdapter(), {"access_key_id": "AKIA", "secret_access_key": "s3cret-aws"}),
    (AlibabaAdapter(), {"access_key_id": "LTAI", "access_key_secret": "s3cret-ali"}),
    (CloudruAdapter(), {"key_id": "k", "key_secret": "s3cret-cloudru"}),
])
def test_401_is_a_readable_refusal_without_the_secret(monkeypatch, adapter, creds):
    _freeze_alibaba(monkeypatch)
    _wire(monkeypatch, adapter, _json({"message": "unauthorized"}, status=401))

    ok, err = asyncio.run(adapter.verify(creds))

    assert ok is False and err == "неверные креды"
    for secret in creds.values():
        assert secret not in err, "секрет не должен попадать в текст ошибки"
    # Ни один метод данных не бросает наружу — контракт base.py.
    assert asyncio.run(adapter.balance(creds)) is None
    assert asyncio.run(adapter.services(creds)) == []
    assert asyncio.run(adapter.payments(creds)) == []


@pytest.mark.parametrize("adapter, creds", [
    (AwsAdapter(), {"access_key_id": "AKIA", "secret_access_key": "s3cret-aws"}),
    (AlibabaAdapter(), {"access_key_id": "LTAI", "access_key_secret": "s3cret-ali"}),
    (CloudruAdapter(), {"key_id": "k", "key_secret": "s3cret-cloudru"}),
])
def test_a_transport_failure_never_leaks_the_secret(monkeypatch, adapter, creds):
    """httpx кладёт URL в текст ошибки — у RPC-провайдера там же и креды."""
    _freeze_alibaba(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"boom {request.url}")

    _wire(monkeypatch, adapter, handler)
    ok, err = asyncio.run(adapter.verify(creds))

    assert ok is False and err
    for secret in creds.values():
        assert secret not in err
        assert urllib.parse.quote(secret, safe="") not in err


@pytest.mark.parametrize("adapter", [AwsAdapter(), AlibabaAdapter(), CloudruAdapter()])
def test_empty_credentials_are_refused_before_any_request(adapter):
    ok, err = asyncio.run(adapter.verify({}))
    assert ok is False and err.startswith("не заполнено")
