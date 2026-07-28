"""Wave-9 Plan C Ф1/Ф2 — hosting-provider adapters (RuVDS/Beget/Veesp) + registry.

No live calls: every adapter's `_client()` is swapped for an `httpx.MockTransport`
client, so a renamed vendor field is caught here and a network outage never turns
into a red test.
"""
import asyncio
import base64
import json
import pathlib
import urllib.parse

import httpx

from app.services.hosting_providers import registry
from app.services.hosting_providers.base import redact
from app.services.hosting_providers.beget import BegetAdapter
from app.services.hosting_providers.ruvds import RuvdsAdapter
from app.services.hosting_providers.veesp import VeespAdapter

_FIX = pathlib.Path(__file__).parent / "fixtures" / "hosting"

# All eight kinds the registry is supposed to publish; a module that fails to
# import is allowed to be absent (that is the point of the guard), so the tests
# assert a subset — but the three dependency-free adapters must always be there.
# Каждый kind, который реестр вправе опубликовать. Модуль, который не
# импортировался, может отсутствовать (ради этого и гард), поэтому тесты
# проверяют ПОДмножество — но лишнего kind появиться не должно.
_ALL_KINDS = {"ruvds", "beget", "veesp", "regru_cloudvps", "regru_account",
              "yandex", "openstack", "oracle",
              # Волна биллинг-адаптеров.
              "aeza", "timeweb", "vdsina", "netangels",
              "digitalocean", "hetzner", "selectel",
              "ionos", "ovhcloud", "infomaniak", "latitude",
              "aws", "alibaba", "cloudru",
              "ishosting", "hostkey", "billmanager", "servers_com"}


def _fx(name: str):
    return json.loads((_FIX / name).read_text(encoding="utf-8"))


def _wire(monkeypatch, adapter, handler):
    """Point one adapter at a MockTransport. A NEW client per call: adapters use
    `async with self._client()`, which closes it on exit."""
    monkeypatch.setattr(
        adapter, "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return adapter


def _status(code: int, headers: dict | None = None):
    def handler(_request):
        return httpx.Response(code, headers=headers or {}, json={"error": "x"})
    return handler


# ── RuVDS ──────────────────────────────────────────────────────
RUVDS_CREDS = {"token": "rv-secret-token"}


def _ruvds_ok(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/v2/balance":
        return httpx.Response(200, json=_fx("ruvds_balance.json"))
    if path == "/v2/datacenters":
        return httpx.Response(200, json=_fx("ruvds_datacenters.json"))
    if path == "/v2/servers":
        return httpx.Response(200, json=_fx("ruvds_servers.json"))
    if path == "/v2/payments":
        return httpx.Response(200, json=_fx("ruvds_payments.json"))
    return httpx.Response(404, json={})


def test_ruvds_balance(monkeypatch):
    a = _wire(monkeypatch, RuvdsAdapter(), _ruvds_ok)
    bal = asyncio.run(a.balance(RUVDS_CREDS))
    assert bal is not None
    assert bal.amount == 1234.56
    assert bal.currency == "RUB"                 # integer enum 1 → RUB
    assert asyncio.run(a.verify(RUVDS_CREDS)) == (True, "")


def test_ruvds_services(monkeypatch):
    a = _wire(monkeypatch, RuvdsAdapter(), _ruvds_ok)
    items = asyncio.run(a.services(RUVDS_CREDS))
    assert len(items) == 2
    first, second = items
    assert first.id == "51001" and first.name == "ams-node-1"     # comment trimmed
    assert first.kind == "vps" and first.cost is None
    assert first.ip == "203.0.113.10"
    assert first.region == "Амстердам"                            # dc id → name
    assert first.period == "year"                                 # enum 5
    assert first.paid_till.startswith("2026-09-01")
    # no comment → generated name; unknown dc → numeric fallback; period 0 → month
    assert second.name == "VPS #51002"
    assert second.ip == "" and second.region == "9" and second.period == "month"


def test_ruvds_payments_direction(monkeypatch):
    a = _wire(monkeypatch, RuvdsAdapter(), _ruvds_ok)
    rows = asyncio.run(a.payments(RUVDS_CREDS))
    assert [r["type"] for r in rows] == ["topup", "charge"]        # 1 → in, 2 → out
    assert rows[0]["amount"] == 3000.0 and rows[0]["currency"] == "RUB"
    assert rows[1]["note"] == "server 51001"
    assert rows[0]["ts"].startswith("2026-07-01")


def test_ruvds_rate_limit_reports_the_wait(monkeypatch):
    a = _wire(monkeypatch, RuvdsAdapter(), _status(429, {"retry-after": "37"}))
    ok, msg = asyncio.run(a.verify(RUVDS_CREDS))
    assert ok is False
    assert "лимит" in msg and "37" in msg


def test_ruvds_bad_token(monkeypatch):
    a = _wire(monkeypatch, RuvdsAdapter(), _status(401))
    ok, msg = asyncio.run(a.verify(RUVDS_CREDS))
    assert (ok, msg) == (False, "неверные креды")
    # ...and a failed fetch degrades to «no data», not an exception
    assert asyncio.run(a.balance(RUVDS_CREDS)) is None
    assert asyncio.run(a.services(RUVDS_CREDS)) == []


def test_ruvds_missing_token_is_reported_without_a_request(monkeypatch):
    def boom(_request):
        raise AssertionError("no request must be made without a token")
    a = _wire(monkeypatch, RuvdsAdapter(), boom)
    ok, msg = asyncio.run(a.verify({}))
    assert ok is False and "не заполнено" in msg


# ── Beget ──────────────────────────────────────────────────────
BEGET_CREDS = {"login": "user1", "password": "p@ss w0rd/!"}


def test_beget_balance_through_the_double_envelope(monkeypatch):
    def handler(request):
        # the credentials really do travel in the query string
        q = dict(urllib.parse.parse_qsl(request.url.query.decode()))
        assert q["login"] == "user1" and q["passwd"] == BEGET_CREDS["password"]
        assert q["output_format"] == "json"
        return httpx.Response(200, json=_fx("beget_account.json"))

    a = _wire(monkeypatch, BegetAdapter(), handler)
    bal = asyncio.run(a.balance(BEGET_CREDS))
    assert bal is not None and bal.amount == 512.4 and bal.currency == "RUB"
    assert asyncio.run(a.verify(BEGET_CREDS)) == (True, "")


def test_beget_inner_errortext_is_surfaced(monkeypatch):
    def handler(_request):
        return httpx.Response(200, json={
            "status": "success",
            "answer": {"status": "error", "errortext": "Wrong password",
                       "errorcode": "AUTH_ERROR"},
        })

    a = _wire(monkeypatch, BegetAdapter(), handler)
    ok, msg = asyncio.run(a.verify(BEGET_CREDS))
    assert ok is False and msg == "Wrong password"
    assert asyncio.run(a.balance(BEGET_CREDS)) is None


def test_beget_outer_error_text_is_surfaced(monkeypatch):
    """The vendor spells the key differently per envelope level — the outer one
    must not read as success."""
    def handler(_request):
        return httpx.Response(200, json={"status": "error",
                                         "error_text": "Method not allowed"})

    a = _wire(monkeypatch, BegetAdapter(), handler)
    assert asyncio.run(a.verify(BEGET_CREDS)) == (False, "Method not allowed")


def test_beget_error_never_echoes_the_password(monkeypatch):
    """httpx quotes the request URL into its error strings, and Beget's URL holds
    the password percent-encoded — both forms must be masked."""
    password = BEGET_CREDS["password"]
    quoted = urllib.parse.quote(password, safe="")
    assert quoted != password                            # the fixture is meaningful

    def handler(request):
        raise httpx.ConnectError(f"connection failed for {request.url}?"
                                 f"login=user1&passwd={quoted}")

    a = _wire(monkeypatch, BegetAdapter(), handler)
    ok, msg = asyncio.run(a.verify(BEGET_CREDS))
    assert ok is False
    assert password not in msg and quoted not in msg
    assert "«redacted»" in msg
    # the masking helper itself covers both spellings
    assert password not in redact(f"{password} / {quoted}", password)


# ── Veesp ──────────────────────────────────────────────────────
VEESP_CREDS = {"email": "me@example.com", "password": "veesp-pw"}


def test_veesp_sends_basic_auth_and_reads_balance(monkeypatch):
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization", "")
        seen["path"] = request.url.path
        return httpx.Response(200, json=_fx("veesp_balance.json"))

    a = _wire(monkeypatch, VeespAdapter(), handler)
    bal = asyncio.run(a.balance(VEESP_CREDS))
    scheme, _, blob = seen["auth"].partition(" ")
    assert scheme == "Basic"
    assert base64.b64decode(blob).decode() == "me@example.com:veesp-pw"
    assert seen["path"] == "/api/balance"
    assert bal is not None and bal.amount == 42.5      # string amount coerced
    assert bal.currency == "EUR"                       # lowercase → upper


def test_veesp_unexpected_shape_yields_none(monkeypatch):
    """An undocumented response must read as «no balance», never as an exception
    or a wrong number."""
    for body in ([{"balance": 1}], {"whatever": "nope"}, {"balance": "n/a"}):
        a = _wire(monkeypatch, VeespAdapter(),
                  lambda _r, b=body: httpx.Response(200, json=b))
        assert asyncio.run(a.balance(VEESP_CREDS)) is None
    # non-JSON body → a message, not a crash
    a = _wire(monkeypatch, VeespAdapter(),
              lambda _r: httpx.Response(200, text="<html>maintenance</html>"))
    ok, msg = asyncio.run(a.verify(VEESP_CREDS))
    assert ok is False and "не-JSON" in msg


def test_veesp_invoices_are_charges(monkeypatch):
    def handler(_request):
        return httpx.Response(200, json={"invoices": [
            {"date": "2026-07-01", "total": "12.00", "currency": "eur",
             "status": "paid"},
            "junk",
        ]})

    a = _wire(monkeypatch, VeespAdapter(), handler)
    rows = asyncio.run(a.payments(VEESP_CREDS))
    assert len(rows) == 1                              # the junk row is dropped
    assert rows[0] == {"ts": "2026-07-01", "amount": 12.0, "currency": "EUR",
                       "type": "charge", "note": "paid"}


# ── registry ───────────────────────────────────────────────────
def test_registry_publishes_the_adapters():
    kinds = set(registry.ADAPTERS)
    assert kinds <= _ALL_KINDS                         # no stray kind
    assert {"ruvds", "beget", "veesp"} <= kinds        # dependency-free ones
    for kind in kinds:
        assert registry.get(kind) is registry.ADAPTERS[kind]
    assert registry.get("нет такого") is None
    assert registry.get("") is None


def test_registry_schemas_shape():
    schemas = registry.schemas()
    assert [s["kind"] for s in schemas] == registry.kinds()      # stable order
    assert {s["kind"] for s in schemas} == set(registry.ADAPTERS)
    for s in schemas:
        assert s["title"]
        assert s["caps"] == sorted(s["caps"])
        # `order` — возможность заказа ресурса (волна покупки).
        assert set(s["caps"]) <= {"balance", "services", "payments", "order"}
        assert all(set(f) == {"key", "label", "kind", "required"} for f in s["fields"])
        assert all(f["kind"] in ("text", "password", "textarea") for f in s["fields"])
    by_kind = {s["kind"]: s for s in schemas}
    assert [f["key"] for f in by_kind["beget"]["fields"]] == ["login", "password"]
    assert by_kind["beget"]["caps"] == ["balance"]


def test_registry_survives_a_broken_module(monkeypatch):
    """One unimportable vendor must not empty the whole list."""
    monkeypatch.setattr(registry, "_MODULES", ("ruvds", "no_such_vendor", "beget"))
    loaded = registry._load()
    assert set(loaded) == {"ruvds", "beget"}
