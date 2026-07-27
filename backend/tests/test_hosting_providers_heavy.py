"""Wave-9 Plan C — тяжёлые адаптеры хостингов (yandex/openstack/oracle/regru).

Здесь живёт криптография, и опечатка в ней ломает авторизацию ТИХО: неверное
padding, перепутанный порядок заголовков подписи или чтение токена из тела
вместо заголовка дают ровно тот же 401, что и неправильный ключ. Поэтому подписи
не сравниваются с эталонной строкой, а **проверяются публичной половиной ключа**
ровно тем алгоритмом, который требует вендор — и дополнительно проверяется, что
соседний алгоритм (PKCS1v15 у Yandex, PSS у Oracle) НЕ проходит.

Сеть не трогается: клиент каждого адаптера подменяется на `httpx.MockTransport`
через `base.ProviderAdapter._client` — эта косвенность существует для тестов.
"""
import asyncio
import base64
import email.utils
import hashlib
import json
import urllib.parse

import httpx
import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.services import net_guard
from app.services.hosting_providers import openstack, oracle, regru, yandex

# Один ключ на весь файл: генерация RSA-2048 стоит десятки миллисекунд, а обоим
# адаптерам нужен просто «какой-нибудь» рабочий RSA.
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()
_PUB = _KEY.public_key()

_PSS = padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                   salt_length=hashes.SHA256.digest_size)


def _mock(adapter, handler) -> list[httpx.Request]:
    """Пустить клиент адаптера через MockTransport и запомнить все запросы."""
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    transport = httpx.MockTransport(wrapped)
    adapter._client = lambda: httpx.AsyncClient(transport=transport, timeout=5)
    return seen


def _deny(adapter) -> list[httpx.Request]:
    """Транспорт, который не должен быть вызван вообще (гарды до запроса)."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"неожиданный сетевой запрос: {request.url}")
    return _mock(adapter, handler)


def _b64u(raw: str) -> bytes:
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


def _form(request: httpx.Request) -> dict[str, str]:
    return dict(urllib.parse.parse_qsl(request.content.decode()))


# ─────────────────────────── Yandex Cloud ───────────────────────────

_YA_CREDS = {
    "service_account_id": "ajesa11111111service",
    "key_id": "ajekk22222222keyid",
    "private_key": _PEM,
    "folder_id": "b1gfolder333333",
}


@pytest.fixture(autouse=True)
def _clear_iam_cache():
    """Кэш IAM-токена — модульный (его делят поллер и роуты), между тестами чистим."""
    yandex._IAM_CACHE.clear()
    yield
    yandex._IAM_CACHE.clear()


def test_yandex_jwt_structure():
    token = yandex.build_jwt(_YA_CREDS, now=1_700_000_000)
    header_b64, payload_b64, _sig = token.split(".")

    assert json.loads(_b64u(header_b64)) == {
        "alg": "PS256", "typ": "JWT", "kid": _YA_CREDS["key_id"],
    }
    payload = json.loads(_b64u(payload_b64))
    assert payload["iss"] == _YA_CREDS["service_account_id"]
    assert payload["aud"] == "https://iam.api.cloud.yandex.net/iam/v1/tokens"
    assert payload["iat"] == 1_700_000_000
    assert payload["exp"] - payload["iat"] == 3600


def test_yandex_jwt_is_signed_with_ps256():
    """Подпись обязана проходить PSS+MGF1(SHA-256) с солью в длину дайджеста и
    НЕ проходить PKCS1v15 — иначе адаптер молча подписывает RS256 (401 от IAM)."""
    token = yandex.build_jwt(_YA_CREDS)
    header_b64, payload_b64, sig_b64 = token.split(".")
    signing_input = f"{header_b64}.{payload_b64}".encode()

    _PUB.verify(_b64u(sig_b64), signing_input, _PSS, hashes.SHA256())

    with pytest.raises(InvalidSignature):
        _PUB.verify(_b64u(sig_b64), signing_input, padding.PKCS1v15(), hashes.SHA256())


def test_yandex_accepts_a_pem_with_escaped_newlines():
    """`yc iam key create` отдаёт PEM внутри JSON, т.е. с литеральными \\n."""
    creds = dict(_YA_CREDS, private_key=_PEM.replace("\n", "\\n"))
    header_b64, payload_b64, sig_b64 = yandex.build_jwt(creds).split(".")
    _PUB.verify(_b64u(sig_b64), f"{header_b64}.{payload_b64}".encode(),
                _PSS, hashes.SHA256())


def _yandex_handler(billing: dict, token: str = "t-iam-1"):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == yandex._IAM_URL:
            body = json.loads(request.content)
            assert list(body) == ["jwt"], "IAM ждёт ровно {jwt: …}"
            header_b64, payload_b64, sig_b64 = body["jwt"].split(".")
            # Подпись проверяем и на боевом пути: так ловится расхождение между
            # тем, что подписано, и тем, что реально ушло в IAM.
            _PUB.verify(_b64u(sig_b64), f"{header_b64}.{payload_b64}".encode(),
                        _PSS, hashes.SHA256())
            return httpx.Response(200, json={"iamToken": token})
        if url.startswith(yandex._BILLING_URL):
            assert request.headers["authorization"] == f"Bearer {token}"
            return httpx.Response(200, json=billing)
        return httpx.Response(404, json={"message": "no such handler"})
    return handler


def test_yandex_balance_coerces_the_string_amount():
    """`billingAccounts[].balance` приходит СТРОКОЙ, валюта — не всегда RUB."""
    adapter = yandex.YandexAdapter()
    _mock(adapter, _yandex_handler({"billingAccounts": [
        {"id": "b1", "name": "suspended", "active": False,
         "balance": "-1.00", "currency": "rub"},
        {"id": "b2", "name": "main", "active": True,
         "balance": "1234.56", "currency": "usd"},
    ]}))

    bal = asyncio.run(adapter.balance(_YA_CREDS))
    assert bal is not None
    assert isinstance(bal.amount, float) and bal.amount == 1234.56
    assert bal.currency == "USD"


def test_yandex_caches_the_iam_token():
    """Обмен JWT→IAM — подписанный round-trip; второй вызов не должен его делать."""
    adapter = yandex.YandexAdapter()
    seen = _mock(adapter, _yandex_handler(
        {"billingAccounts": [{"id": "b1", "active": True,
                              "balance": "10", "currency": "RUB"}]}))

    assert asyncio.run(adapter.balance(_YA_CREDS)).amount == 10.0
    assert asyncio.run(adapter.balance(_YA_CREDS)).amount == 10.0

    tokens = [r for r in seen if str(r.url) == yandex._IAM_URL]
    assert len(tokens) == 1
    assert len(seen) == 3          # 1 обмен + 2 биллинга


def test_yandex_verify_reports_bad_credentials():
    adapter = yandex.YandexAdapter()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "unauthenticated"})

    _mock(adapter, handler)
    assert asyncio.run(adapter.verify(_YA_CREDS)) == (False, "неверные креды")


# ─────────────────────────── OpenStack ───────────────────────────

_OS_CREDS = {
    "auth_url": "https://keystone.example/v3",   # openrc уже содержит /v3
    "username": "svc-user",
    "password": "os-p@ss",
    "project_id": "proj-1111",
    "domain": "users",
}

_OS_CATALOG = [
    {"type": "identity", "endpoints": [
        {"interface": "public", "url": "https://keystone.example/v3",
         "region": "RegionOne"}]},
    {"type": "compute", "endpoints": [
        # admin идёт ПЕРВЫМ: адаптер обязан выбрать public, а не «первый попавшийся».
        {"interface": "admin", "url": "https://nova-admin.invalid:8774/v2.1/proj",
         "region": "RegionOne"},
        {"interface": "public", "url": "https://nova.example:8774/v2.1/proj",
         "region": "MS1"}]},
]

_OS_SERVERS = {"servers": [{
    "id": "srv-1", "name": "web-1", "status": "ACTIVE",
    "flavor": {"original_name": "Standard-2-4-50"},
    "addresses": {"ext-net": [
        {"addr": "10.0.0.5", "OS-EXT-IPS:type": "fixed"},
        {"addr": "203.0.113.9", "OS-EXT-IPS:type": "floating"}]},
    "OS-EXT-AZ:availability_zone": "MS1",
}]}

_SUBJECT_TOKEN = "gAAAAAB-subject-token"


def _allow_example_dns(monkeypatch):
    """Разрешить вымышленные *.example (DNS их не резолвит), НЕ отключая гард."""
    real = net_guard.host_is_public
    monkeypatch.setattr(net_guard, "host_is_public",
                        lambda host: host.endswith(".example") or real(host))


def _openstack_handler():
    def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if (host, path) == ("keystone.example", "/v3/auth/tokens"):
            body = json.loads(request.content)
            identity = body["auth"]["identity"]
            assert identity["methods"] == ["password"]
            assert identity["password"]["user"] == {
                "name": _OS_CREDS["username"],
                "password": _OS_CREDS["password"],
                "domain": {"name": _OS_CREDS["domain"]},
            }
            assert body["auth"]["scope"]["project"]["id"] == _OS_CREDS["project_id"]
            return httpx.Response(
                201,
                headers={"X-Subject-Token": _SUBJECT_TOKEN},
                # В теле — приманка: похожий на токен `id`, который брать нельзя.
                json={"token": {"id": "BODY-ID-IS-NOT-THE-TOKEN",
                                "catalog": _OS_CATALOG}},
            )
        if (host, path) == ("nova.example", "/v2.1/proj/servers/detail"):
            assert request.headers["x-auth-token"] == _SUBJECT_TOKEN
            return httpx.Response(200, json=_OS_SERVERS)
        return httpx.Response(404, json={"error": f"нет ручки {host}{path}"})
    return handler


def test_openstack_auth_body_and_v3_suffix(monkeypatch):
    _allow_example_dns(monkeypatch)
    adapter = openstack.OpenStackAdapter()
    seen = _mock(adapter, _openstack_handler())

    assert asyncio.run(adapter.verify(_OS_CREDS)) == (True, "")
    # /v3 из auth_url не удвоился в /v3/v3/auth/tokens
    assert str(seen[0].url) == "https://keystone.example/v3/auth/tokens"


def test_openstack_reads_the_token_from_the_header(monkeypatch):
    """Токен лежит в X-Subject-Token; body["token"] — это каталог, не токен."""
    _allow_example_dns(monkeypatch)
    adapter = openstack.OpenStackAdapter()
    _mock(adapter, _openstack_handler())

    token, nova, region, err = asyncio.run(adapter._token(_OS_CREDS))
    assert err == ""
    assert token == _SUBJECT_TOKEN
    assert nova == "https://nova.example:8774/v2.1/proj"
    assert region == "MS1"


def test_openstack_uses_the_compute_endpoint_from_the_catalog(monkeypatch):
    """Nova-хост берётся из каталога (у VK Cloud он на другом хосте и порту);
    захардкоженный или admin-эндпоинт упал бы в 404 → пустой список."""
    _allow_example_dns(monkeypatch)
    adapter = openstack.OpenStackAdapter()
    seen = _mock(adapter, _openstack_handler())

    items = asyncio.run(adapter.services(_OS_CREDS))
    assert [str(r.url) for r in seen][1] == \
        "https://nova.example:8774/v2.1/proj/servers/detail"
    assert len(items) == 1
    item = items[0]
    assert (item.id, item.name, item.status) == ("srv-1", "web-1", "ACTIVE")
    assert item.kind == "Standard-2-4-50"
    assert item.ip == "203.0.113.9"          # floating, а не fixed 10.0.0.5
    assert item.region == "MS1"
    assert item.cost is None and item.currency == ""


@pytest.mark.parametrize("auth_url", [
    "http://127.0.0.1/v3",
    "http://169.254.169.254/",
    "https://[::1]:5000/v3",
    "ftp://keystone.example/v3",
])
def test_openstack_rejects_a_non_public_auth_url(monkeypatch, auth_url):
    """SSRF-гард обязан отказать ДО запроса: auth_url задаёт пользователь."""
    monkeypatch.setattr(net_guard, "_ALLOW_PRIVATE", False)
    adapter = openstack.OpenStackAdapter()
    seen = _deny(adapter)

    ok, err = asyncio.run(adapter.verify(dict(_OS_CREDS, auth_url=auth_url)))
    assert ok is False
    assert err == openstack._UNSAFE
    assert seen == []


def test_openstack_has_no_balance():
    """Ни у VK Cloud, ни у Procloud нет биллинг-API — сумма остаётся ручной."""
    adapter = openstack.OpenStackAdapter()
    _deny(adapter)
    assert asyncio.run(adapter.balance(_OS_CREDS)) is None
    assert "balance" not in adapter.CAPS


# ─────────────────────────── Oracle Cloud (OCI) ───────────────────────────

_OCI_CREDS = {
    "tenancy_ocid": "ocid1.tenancy.oc1..aaaatenancy",
    "user_ocid": "ocid1.user.oc1..aaaauser",
    "fingerprint": "aa:bb:cc:dd:ee:ff:00:11",
    "private_key": _PEM,
    "region": "eu-frankfurt-1",
}

def _parse_auth(request: httpx.Request) -> dict[str, str]:
    """`Signature version="1",keyId="…",…` → dict."""
    raw = request.headers["authorization"]
    assert raw.startswith("Signature "), raw
    out: dict[str, str] = {}
    for part in raw[len("Signature "):].split(","):
        key, _, value = part.partition("=")
        out[key.strip()] = value.strip().strip('"')
    return out


def _rebuild_signing_string(request: httpx.Request, names: list[str]) -> bytes:
    """Собрать строку подписи НЕЗАВИСИМО от адаптера — из реально ушедшего
    запроса и ожидаемого порядка имён. Расхождение порядка → InvalidSignature."""
    parsed = urllib.parse.urlparse(str(request.url))
    target = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    values = {
        "(request-target)": f"{request.method.lower()} {target}",
        "date": request.headers["date"],
        "host": parsed.netloc,
        "x-content-sha256": request.headers.get("x-content-sha256", ""),
        "content-type": request.headers.get("content-type", ""),
        "content-length": request.headers.get("content-length", ""),
    }
    return "\n".join(f"{n}: {values[n]}" for n in names).encode()


def _assert_signed(request: httpx.Request, names: list[str]) -> None:
    auth = _parse_auth(request)
    assert auth["algorithm"] == "rsa-sha256"
    assert auth["keyId"] == "{}/{}/{}".format(
        _OCI_CREDS["tenancy_ocid"], _OCI_CREDS["user_ocid"],
        _OCI_CREDS["fingerprint"])
    assert auth["headers"] == " ".join(names)
    assert auth["version"] == "1"

    signature = base64.b64decode(auth["signature"])
    string = _rebuild_signing_string(request, names)
    _PUB.verify(signature, string, padding.PKCS1v15(), hashes.SHA256())
    # rsa-sha256 — это PKCS1v15, НЕ та PSS-набивка, что нужна Yandex.
    with pytest.raises(InvalidSignature):
        _PUB.verify(signature, string, _PSS, hashes.SHA256())


def test_oracle_signing_string_order_get():
    date = "Mon, 27 Jul 2026 10:20:30 GMT"
    url = ("https://iaas.eu-frankfurt-1.oraclecloud.com/20160918/instances"
           "?compartmentId=ocid1.tenancy")
    string, names = oracle.signing_string("GET", url, date)

    assert names == ["(request-target)", "date", "host"]
    assert string.split("\n") == [
        "(request-target): get /20160918/instances?compartmentId=ocid1.tenancy",
        f"date: {date}",
        "host: iaas.eu-frankfurt-1.oraclecloud.com",
    ]


def test_oracle_signing_string_order_post():
    date = "Mon, 27 Jul 2026 10:20:30 GMT"
    body = b'{"tenantId":"ocid1.tenancy"}'
    string, names = oracle.signing_string(
        "POST", "https://usageapi.eu-frankfurt-1.oci.oraclecloud.com/20200107/usage",
        date, body)

    assert names == ["(request-target)", "date", "host",
                     "x-content-sha256", "content-type", "content-length"]
    digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
    assert string.split("\n") == [
        "(request-target): post /20200107/usage",
        f"date: {date}",
        "host: usageapi.eu-frankfurt-1.oci.oraclecloud.com",
        f"x-content-sha256: {digest}",
        "content-type: application/json",
        f"content-length: {len(body)}",
    ]


def test_oracle_date_header_is_rfc1123_gmt():
    headers = oracle.sign_headers(_OCI_CREDS, "GET",
                                  "https://iaas.eu-frankfurt-1.oraclecloud.com/x")
    date = headers["date"]
    assert date.endswith("GMT"), date
    parsed = email.utils.parsedate_to_datetime(date)
    assert parsed.utcoffset().total_seconds() == 0
    assert headers["accept"] == "application/json"


def test_oracle_verify_signs_the_request_it_sends():
    """Подпись проверяется по РЕАЛЬНО ушедшему запросу: так ловится и неверный
    порядок заголовков, и URL, пересобранный после подписи."""
    adapter = oracle.OracleAdapter()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "iaas.eu-frankfurt-1.oraclecloud.com"
        assert request.url.path == "/20160918/instances"
        assert request.url.params["compartmentId"] == _OCI_CREDS["tenancy_ocid"]
        _assert_signed(request, ["(request-target)", "date", "host"])
        return httpx.Response(200, json={"items": [{
            "id": "ocid1.instance.oc1..aaaainst",
            "displayName": "node-fra-1",
            "shape": "VM.Standard.E4.Flex",
            "lifecycleState": "RUNNING",
            "availabilityDomain": "feDV:EU-FRANKFURT-1-AD-1",
        }]})

    seen = _mock(adapter, handler)
    assert asyncio.run(adapter.verify(_OCI_CREDS)) == (True, "")

    items = asyncio.run(adapter.services(_OCI_CREDS))
    assert len(seen) == 2
    assert len(items) == 1
    assert items[0].name == "node-fra-1"
    assert items[0].kind == "VM.Standard.E4.Flex"
    assert items[0].status == "RUNNING"


def test_oracle_rejects_a_crafted_region():
    """Регион подставляется в ИМЯ ХОСТА подписанного запроса — только slug."""
    adapter = oracle.OracleAdapter()
    _deny(adapter)
    ok, err = asyncio.run(adapter.verify(
        dict(_OCI_CREDS, region="evil.example/../x")))
    assert (ok, err) == (False, "регион указан неверно")


def test_oracle_payments_sums_computed_amount():
    adapter = oracle.OracleAdapter()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == \
            "https://usageapi.eu-frankfurt-1.oci.oraclecloud.com/20200107/usage"
        body = json.loads(request.content)
        assert body["tenantId"] == _OCI_CREDS["tenancy_ocid"]
        assert body["granularity"] == "MONTHLY"
        assert body["timeUsageStarted"].endswith("Z")
        _assert_signed(request, ["(request-target)", "date", "host",
                                 "x-content-sha256", "content-type",
                                 "content-length"])
        return httpx.Response(200, json={"items": [
            {"computedAmount": 1.5, "currency": "USD"},
            {"computedAmount": "2.25", "currency": "USD"},
            {"computedAmount": None, "currency": "USD"},
            {"computedAmount": "не число", "currency": "USD"},
        ]})

    _mock(adapter, handler)
    rows = asyncio.run(adapter.payments(_OCI_CREDS))
    assert len(rows) == 1
    assert rows[0]["amount"] == 3.75
    assert rows[0]["currency"] == "USD"
    assert rows[0]["type"] == "charge"


def test_oracle_has_no_balance():
    """OCI пост-оплатный: ручки «баланс счёта» нет, расход отдаёт payments()."""
    adapter = oracle.OracleAdapter()
    _deny(adapter)
    assert asyncio.run(adapter.balance(_OCI_CREDS)) is None
    assert "balance" not in adapter.CAPS and "payments" in adapter.CAPS


# ─────────────────────────── Reg.ru ───────────────────────────

_REG_ACCOUNT = {"username": "reg-login", "password": "reg-p@ss"}


def test_regru_account_posts_form_data():
    """Рег.API 2 запрещает query-string параметры — креды идут в ТЕЛО (и не
    попадают в логи прокси по пути)."""
    adapter = regru.RegruAccount()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == \
            "https://api.reg.ru/api/regru2/user/get_balance"
        assert request.url.query == b""
        assert request.headers["content-type"].startswith(
            "application/x-www-form-urlencoded")
        form = _form(request)
        assert form["username"] == _REG_ACCOUNT["username"]
        assert form["password"] == _REG_ACCOUNT["password"]
        return httpx.Response(200, json={
            "result": "success",
            "answer": {"prepay": "1234.50", "blocked": "0", "currency": "RUR"},
        })

    _mock(adapter, handler)
    bal = asyncio.run(adapter.balance(_REG_ACCOUNT))
    assert bal is not None
    assert bal.amount == 1234.5                 # ключ `prepay`, не `balance`
    assert bal.currency == "RUB"                # legacy RUR → RUB


def test_regru_account_maps_a_200_auth_error():
    """Рег.API 2 отвечает 200 даже на неверный логин — код в конверте."""
    adapter = regru.RegruAccount()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "result": "error",
            "error_code": "PASSWORD_AUTH_FAILED",
            "error_text": f"password auth failed for {_REG_ACCOUNT['password']}",
        })

    _mock(adapter, handler)
    ok, err = asyncio.run(adapter.verify(_REG_ACCOUNT))
    assert (ok, err) == (False, "неверные креды")
    assert _REG_ACCOUNT["password"] not in err


def test_regru_cloudvps_lists_reglets():
    adapter = regru.RegruCloudVps()

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.cloudvps.reg.ru/v1/reglets"
        assert request.headers["authorization"] == "Bearer cv-token"
        return httpx.Response(200, json={"reglets": [{
            "id": 7788,
            "name": "node-msk-1",
            "status": "active",
            "size": {"price_monthly": "550.00"},
            "region": {"name": "Москва", "slug": "msk"},
            "networks": {"v4": [
                {"ip_address": "10.20.0.2", "type": "private"},
                {"ip_address": "5.61.0.7", "type": "public"},
            ]},
        }]})

    _mock(adapter, handler)
    items = asyncio.run(adapter.services({"token": "cv-token"}))
    assert len(items) == 1
    item = items[0]
    assert (item.id, item.name, item.kind) == ("7788", "node-msk-1", "vps")
    assert item.cost == 550.0 and item.currency == "RUB" and item.period == "month"
    assert item.ip == "5.61.0.7"                # public, а не private
    assert item.region == "Москва"


def test_regru_cloudvps_401_is_bad_credentials():
    adapter = regru.RegruCloudVps()
    _mock(adapter, lambda request: httpx.Response(401, json={"id": "unauthorized"}))
    assert asyncio.run(adapter.verify({"token": "nope"})) == (False, "неверные креды")


def test_regru_cloudvps_has_no_balance():
    """У CloudVPS нет ручки баланса — он приходит из отдельного regru_account."""
    adapter = regru.RegruCloudVps()
    _deny(adapter)
    assert asyncio.run(adapter.balance({"token": "cv-token"})) is None
    assert adapter.CAPS == {"services"}
    assert regru.RegruAccount().CAPS == {"balance"}
    assert [a.KIND for a in regru.ADAPTERS] == ["regru_cloudvps", "regru_account"]
