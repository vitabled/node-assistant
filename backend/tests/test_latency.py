"""Latency Lab: настройки (маска ключа), проверка ключа, замер подсетей.

Внешний HTTP не трогаем: подменяем `httpx.AsyncClient` в `latency_lab` фейком —
тот же приём, что в `test_subnets.py::test_enrich_marks_asn`.
"""
import uuid as _uuid

from fastapi.testclient import TestClient

from app.main import app
from app.services import latency_lab

client = TestClient(app)

KEY = "ll_secret_key_4f2a"


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"ll-{_uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_rows(a, subnets):
    p = client.post("/api/subnets/providers", headers=a, json={"name": "МТС"}).json()
    l = client.post(f"/api/subnets/providers/{p['id']}/lists", headers=a,
                    json={"name": "Основной"}).json()
    client.post(f"/api/subnets/providers/{p['id']}/lists/{l['id']}/rows",
                headers=a, json={"subnets": subnets})
    data = client.get("/api/subnets", headers=a).json()
    rows = data["providers"][0]["lists"][0]["rows"]
    return p["id"], l["id"], [r["id"] for r in rows]


def _configure(a, **over):
    body = {"enabled": True, "base_url": "https://console.latencylab.ru",
            "node_id": "orel", "default_operator": "", "api_key": KEY}
    body.update(over)
    return client.post("/api/latency/config", headers=a, json=body)


class _Fake:
    """Ответ-заглушка Latency Lab + журнал запросов."""

    calls: list = []
    responses: dict = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method, url, headers=None, params=None, json=None):
        _Fake.calls.append({"method": method, "url": url, "headers": headers or {},
                            "params": params, "json": json})
        path = url.split("latencylab.ru", 1)[-1]
        body = _Fake.responses.get(path)
        if body is None:
            body = next((v for k, v in _Fake.responses.items()
                         if path.startswith(k)), {"ok": True})
        return _Resp(body)


class _Resp:
    def __init__(self, body, status=200):
        self._body = body
        self.status_code = status
        self.content = b"x"

    def json(self):
        return self._body


def _patch(monkeypatch, responses):
    _Fake.calls = []
    _Fake.responses = responses
    monkeypatch.setattr(latency_lab.httpx, "AsyncClient",
                        lambda *a, **k: _Fake())


# ── настройки ────────────────────────────────────────────────
def test_config_saves_and_never_returns_key():
    a = _auth()
    r = _configure(a)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["enabled"] is True
    assert body["has_key"] is True
    assert body["key_hint"] == "…4f2a"
    # Ключ не утекает НИ в одном поле ответа.
    assert KEY not in r.text

    got = client.get("/api/latency/config", headers=a).json()
    assert got["has_key"] is True and KEY not in str(got)
    assert got["base_url"] == "https://console.latencylab.ru"
    assert got["node_id"] == "orel"
    assert "t2" in got["operators"]


def test_config_blank_key_keeps_existing_and_stores_ciphertext():
    a = _auth()
    _configure(a)
    # Повторное сохранение без ключа его не стирает.
    r = _configure(a, api_key="", default_operator="tele2")
    assert r.status_code == 200 and r.json()["has_key"] is True
    assert r.json()["key_hint"] == "…4f2a"
    # tele2 (ключ «Подсетей») нормализуется в t2 (id Latency Lab).
    assert r.json()["default_operator"] == "t2"
    # На диске лежит ШИФРОТЕКСТ, а не сам ключ.
    import json
    import os
    import pathlib

    found = [p for p in pathlib.Path(os.environ["DATA_DIR"]).rglob("settings.json")
             if "latency" in p.read_text(encoding="utf-8")]
    assert found, "settings.json с секцией latency не найден"
    blobs = [json.loads(p.read_text(encoding="utf-8"))["latency"] for p in found]
    mine = [b for b in blobs if b.get("api_key_enc")]
    assert mine and all(KEY not in b["api_key_enc"] for b in mine)


def test_config_rejects_unknown_operator():
    a = _auth()
    r = _configure(a, default_operator="kyivstar")
    assert r.status_code == 422


# ── проверка ключа ───────────────────────────────────────────
def test_check_calls_auth_me_with_bearer(monkeypatch):
    a = _auth()
    _configure(a)
    _patch(monkeypatch, {"/api/lab/auth/me":
                         {"ok": True, "result": {"username": "alice", "role": "user"}}})
    r = client.post("/api/latency/check", headers=a)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "user": {"username": "alice", "role": "user"}}
    call = _Fake.calls[0]
    assert call["url"].endswith("/api/lab/auth/me")
    assert call["headers"]["Authorization"] == f"Bearer {KEY}"


def test_check_without_key_is_400():
    a = _auth()  # ничего не настраивали
    r = client.post("/api/latency/check", headers=a)
    assert r.status_code == 400


def test_operators_proxies_result(monkeypatch):
    a = _auth()
    _configure(a)
    _patch(monkeypatch, {"/api/lab/operators":
                         {"ok": True, "result": {"operators": [{"id": "mts"}],
                                                 "online": ["mts"]}}})
    r = client.get("/api/latency/operators", headers=a)
    assert r.status_code == 200 and r.json()["online"] == ["mts"]
    assert _Fake.calls[0]["params"] == {"node_id": "orel"}


# ── замер подсетей ───────────────────────────────────────────
def test_scan_without_key_is_400():
    a = _auth()
    pid, lid, rows = _mk_rows(a, ["203.0.113.0/24"])
    r = client.post("/api/subnets/latency-scan", headers=a,
                    json={"provider_id": pid, "list_id": lid, "row_ids": rows})
    assert r.status_code == 400
    assert "ключ" in r.json()["detail"].lower() or "выключен" in r.json()["detail"].lower()


def test_scan_disabled_is_400(monkeypatch):
    a = _auth()
    _configure(a, enabled=False)
    pid, lid, rows = _mk_rows(a, ["203.0.113.0/24"])
    r = client.post("/api/subnets/latency-scan", headers=a,
                    json={"provider_id": pid, "list_id": lid, "row_ids": rows})
    assert r.status_code == 400 and "выключен" in r.json()["detail"]


def test_scan_collects_subnets_into_one_multiscan(monkeypatch):
    a = _auth()
    _configure(a)
    pid, lid, rows = _mk_rows(a, ["203.0.113.0/24", "198.51.100.0/24"])
    _patch(monkeypatch, {"/api/lab/multiscan":
                         {"ok": True, "req_id": "req-1", "status": "pending"}})
    r = client.post("/api/subnets/latency-scan", headers=a,
                    json={"provider_id": pid, "list_id": lid,
                          "row_ids": rows, "async_": True})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "multiscan" and body["jobs"][0]["req_id"] == "req-1"
    assert body["jobs"][0]["targets"] == ["203.0.113.0/24", "198.51.100.0/24"]
    # Мультискан — РОВНО один внешний запрос на всю пачку (1 запрос лимита).
    assert len(_Fake.calls) == 1
    sent = _Fake.calls[0]["json"]
    assert sent["text"] == "203.0.113.0/24\n198.51.100.0/24"
    assert sent["async"] is True and sent["node_id"] == "orel"


def test_scan_with_operator_does_per_subnet_calls(monkeypatch):
    a = _auth()
    _configure(a)
    pid, lid, rows = _mk_rows(a, ["203.0.113.0/24", "198.51.100.0/24"])
    _patch(monkeypatch, {"/api/lab/subnet-scan":
                         {"ok": True, "req_id": "req-2", "status": "pending"}})
    r = client.post("/api/subnets/latency-scan", headers=a,
                    json={"provider_id": pid, "list_id": lid, "all": True,
                          "operator": "tele2", "async_": True})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "subnet-scan" and body["operator"] == "t2"
    assert len(body["jobs"]) == 2 and len(_Fake.calls) == 2
    assert _Fake.calls[0]["json"]["operator"] == "t2"


def test_scan_limit_750(monkeypatch):
    """Потолок пачки: 751 подсеть → 400 ещё до внешнего запроса, 750 → ок.

    Фронт сам режет большие выборки на порции по 750, поэтому за один
    POST /latency-scan можно слать максимум 750 подсетей."""
    a = _auth()
    _configure(a)
    _patch(monkeypatch, {"/api/lab/multiscan":
                         {"ok": True, "req_id": "req-big", "status": "pending"}})
    subnets = [f"10.{i // 256}.{i % 256}.0/24" for i in range(751)]
    pid, lid, rows = _mk_rows(a, subnets)
    r = client.post("/api/subnets/latency-scan", headers=a,
                    json={"provider_id": pid, "list_id": lid, "row_ids": rows})
    assert r.status_code == 400
    assert "750" in r.json()["detail"]
    assert _Fake.calls == []  # до внешнего сервиса дело не дошло

    # Свежий аккаунт, чтобы `_mk_rows` не зацепил провайдера из первой части.
    a = _auth()
    _configure(a)
    pid, lid, rows = _mk_rows(a, subnets[:750])
    r = client.post("/api/subnets/latency-scan", headers=a,
                    json={"provider_id": pid, "list_id": lid, "row_ids": rows})
    assert r.status_code == 200
    assert r.json()["jobs"][0]["req_id"] == "req-big"
    assert len(_Fake.calls) == 1  # ровно один мультискан на всю пачку
    assert len(_Fake.calls[0]["json"]["text"].split("\n")) == 750


def test_scan_unknown_rows_is_404():
    a = _auth()
    _configure(a)
    pid, lid, _ = _mk_rows(a, ["203.0.113.0/24"])
    r = client.post("/api/subnets/latency-scan", headers=a,
                    json={"provider_id": pid, "list_id": lid, "row_ids": ["nope"]})
    assert r.status_code == 404


def test_job_status_and_cancel(monkeypatch):
    a = _auth()
    _configure(a)
    _patch(monkeypatch, {"/api/lab/job/": {"ok": True, "status": "done",
                                           "result": {"alive_count": 12}},
                         "/api/lab/cancel": {"ok": True,
                                             "result": {"cancelled": True}}})
    r = client.get("/api/subnets/latency-scan/req-1", headers=a)
    assert r.status_code == 200
    assert r.json()["status"] == "done"
    assert r.json()["result"]["alive_count"] == 12

    r = client.post("/api/subnets/latency-scan/cancel", headers=a,
                    json={"req_id": "req-1"})
    assert r.status_code == 200 and r.json()["result"]["cancelled"] is True
    assert _Fake.calls[-1]["json"] == {"req_id": "req-1"}


def test_service_error_surfaces_as_502(monkeypatch):
    a = _auth()
    _configure(a)
    pid, lid, rows = _mk_rows(a, ["203.0.113.0/24"])

    class _Err(_Fake):
        async def request(self, method, url, headers=None, params=None, json=None):
            return _Resp({"ok": False,
                          "error": "API-ключ: подсеть только /23…/32"}, 403)

    monkeypatch.setattr(latency_lab.httpx, "AsyncClient", lambda *a, **k: _Err())
    r = client.post("/api/subnets/latency-scan", headers=a,
                    json={"provider_id": pid, "list_id": lid, "row_ids": rows})
    assert r.status_code == 502 and "/23" in r.json()["detail"]


# ── привилегия ───────────────────────────────────────────────
def test_latency_scan_permission_is_separate():
    from app.services import permissions

    assert "latency.scan" in permissions.ALL_PERMISSIONS
    assert permissions.required("/api/subnets/latency-scan", "POST") == ("latency.scan",)
    # Общий префикс подсетей не перехватывает замер.
    assert permissions.required("/api/subnets", "POST") == ("hostings.create",)


def test_export_without_secrets_strips_latency_key():
    """Ключ — Fernet-поле `*_enc`, и выгрузка без секретов обязана его вычистить."""
    from app.services import export_service

    stripped = export_service._strip_secrets(
        "settings.json", {"latency": {"enabled": True, "api_key_enc": "ciphertext",
                                      "node_id": "orel"}})
    assert stripped["latency"]["api_key_enc"] == ""
    assert stripped["latency"]["node_id"] == "orel"
    assert "latency" in export_service.available_sections()
