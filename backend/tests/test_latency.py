"""Latency Lab: настройки (маска ключа), проверка ключа, замер подсетей.

Внешний HTTP не трогаем: подменяем `httpx.AsyncClient` в `latency_lab` фейком —
тот же приём, что в `test_subnets.py::test_enrich_marks_asn`.
"""
import time
import uuid as _uuid
from datetime import datetime

from fastapi.testclient import TestClient

from app.main import app
from app.services import latency_lab

client = TestClient(app)

KEY = "ll_secret_key_4f2a"


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"ll-{_uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_account():
    """Регистрация → (headers, account_id) — id нужен для правки settings.json."""
    r = client.post("/api/auth/register",
                    json={"login": f"ll-{_uuid.uuid4().hex[:8]}", "password": "pw"})
    body = r.json()
    return {"Authorization": f"Bearer {body['token']}"}, body["id"]


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


def test_scan_excludes_host_32_from_multiscan(monkeypatch):
    """Host-адреса /32 мультискан не распознаёт («не удалось распознать
    сеть») — они не идут в text мультискана и помечаются понятной ошибкой,
    обычные сети сканируются как раньше."""
    a = _auth()
    _configure(a)
    pid, lid, rows = _mk_rows(a, ["203.0.113.0/24", "195.239.193.161/32"])
    _patch(monkeypatch, {"/api/lab/multiscan":
                         {"ok": True, "req_id": "req-net", "status": "pending"}})
    r = client.post("/api/subnets/latency-scan", headers=a,
                    json={"provider_id": pid, "list_id": lid,
                          "row_ids": rows, "async_": True})
    assert r.status_code == 200
    body = r.json()
    assert len(_Fake.calls) == 1  # только мультискан сетей
    assert _Fake.calls[0]["json"]["text"] == "203.0.113.0/24"
    assert body["jobs"][0]["targets"] == ["203.0.113.0/24"]
    # /32 — отдельная понятная ошибка, а не «не удалось распознать сеть»
    assert body["errors"] and any(
        "195.239.193.161/32" in e and "оператора" in e for e in body["errors"])


def test_scan_only_hosts_without_operator_is_400(monkeypatch):
    """Только host-адреса и нет оператора — поштучный скан невозможен:
    400 с понятным текстом вместо 502 «не удалось распознать сеть»."""
    a = _auth()
    _configure(a)
    pid, lid, rows = _mk_rows(a, ["195.239.193.161/32"])
    _patch(monkeypatch, {"/api/lab/multiscan":
                         {"ok": True, "req_id": "req", "status": "pending"}})
    r = client.post("/api/subnets/latency-scan", headers=a,
                    json={"provider_id": pid, "list_id": lid, "row_ids": rows})
    assert r.status_code == 400
    assert "оператора" in r.json()["detail"]
    assert _Fake.calls == []  # внешний сервис не дёргали


def test_scan_host_32_with_operator_goes_subnet_scan(monkeypatch):
    """С оператором /32 уходит поштучным subnet-scan'ом (он принимает
    /23…/32) — скан не падает на host-адресах."""
    a = _auth()
    _configure(a)
    pid, lid, rows = _mk_rows(a, ["203.0.113.0/24", "195.239.193.161/32"])
    _patch(monkeypatch, {"/api/lab/subnet-scan":
                         {"ok": True, "req_id": "req-s", "status": "pending"}})
    r = client.post("/api/subnets/latency-scan", headers=a,
                    json={"provider_id": pid, "list_id": lid,
                          "row_ids": rows, "operator": "tele2", "async_": True})
    assert r.status_code == 200
    assert r.json()["mode"] == "subnet-scan"
    assert len(_Fake.calls) == 2
    targets = [c["json"]["target"] for c in _Fake.calls]
    assert "195.239.193.161/32" in targets and "203.0.113.0/24" in targets


def test_split_host_subnets():
    """Разбиение на сети/host-адреса: /32 (IPv4) и /128 (IPv6) — hosts,
    остальное — сети; strip и мусор не ломают."""
    from app.api.subnets import _split_host_subnets
    nets, hosts = _split_host_subnets(
        ["203.0.113.0/24", "195.239.193.161/32", " 2001:db8::/32 ",
         "2001:db8::1/128", "10.0.0.0/8"])
    assert nets == ["203.0.113.0/24", "2001:db8::/32", "10.0.0.0/8"]
    assert hosts == ["195.239.193.161/32", "2001:db8::1/128"]


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


# ── лимит «N сканов за M часов» ──────────────────────────────
def _scan(a, pid, lid, rows):
    return client.post("/api/subnets/latency-scan", headers=a,
                       json={"provider_id": pid, "list_id": lid,
                             "row_ids": rows})


def _set_history(account_id, marks):
    """Прямая правка scan_history в settings.json аккаунта (API её не отдаёт)."""
    from app.services import storage

    data = storage.load_settings(account_id)
    data.setdefault("latency", {})["scan_history"] = marks
    storage.save_settings(data, account_id)


def test_scan_rate_limit_2_per_24h_third_is_429(monkeypatch):
    """Лимит 2 скана за 24 ч: первый и второй проходят, третий — 429."""
    a = _auth()
    _configure(a, scan_limit=2, scan_window_hours=24)
    pid, lid, rows = _mk_rows(a, ["203.0.113.0/24"])
    _patch(monkeypatch, {"/api/lab/multiscan":
                         {"ok": True, "req_id": "req-1", "status": "pending"}})

    for i in range(2):
        r = _scan(a, pid, lid, rows)
        assert r.status_code == 200, f"скан {i + 1} должен пройти"
    got = client.get("/api/latency/config", headers=a).json()
    assert got["scan_count"] == 2  # метки записались и видны в _public

    r = _scan(a, pid, lid, rows)
    assert r.status_code == 429
    detail = r.json()["detail"]
    assert "лимит" in detail.lower() and "2" in detail and "24" in detail


def test_scan_rate_limit_ignores_old_marks(monkeypatch):
    """Метки старше окна не считаются: 2 метки 48 ч назад не блокируют."""
    a, aid = _mk_account()
    _configure(a, scan_limit=2, scan_window_hours=24)
    _set_history(aid, [time.time() - 48 * 3600, time.time() - 47 * 3600])
    pid, lid, rows = _mk_rows(a, ["203.0.113.0/24"])
    _patch(monkeypatch, {"/api/lab/multiscan":
                         {"ok": True, "req_id": "req-1", "status": "pending"}})

    # Две старые метки + два свежих скана → ок; третий свежий → 429.
    for i in range(2):
        r = _scan(a, pid, lid, rows)
        assert r.status_code == 200, f"скан {i + 1} должен пройти"
    got = client.get("/api/latency/config", headers=a).json()
    assert got["scan_count"] == 2  # только свежие
    assert _scan(a, pid, lid, rows).status_code == 429


def test_scan_rate_limit_zero_means_unlimited(monkeypatch):
    """scan_limit=0 — лимит выключен, сколько угодно запусков."""
    a = _auth()
    _configure(a, scan_limit=0, scan_window_hours=24)
    pid, lid, rows = _mk_rows(a, ["203.0.113.0/24"])
    _patch(monkeypatch, {"/api/lab/multiscan":
                         {"ok": True, "req_id": "req-1", "status": "pending"}})
    for _ in range(4):
        assert _scan(a, pid, lid, rows).status_code == 200


def test_scan_rate_limit_marks_only_on_accepted(monkeypatch):
    """Ошибка Latency Lab лимит не тратит: метка пишется при приёме скана."""
    a = _auth()
    _configure(a, scan_limit=1, scan_window_hours=24)
    pid, lid, rows = _mk_rows(a, ["203.0.113.0/24"])

    class _Err(_Fake):
        async def request(self, method, url, headers=None, params=None, json=None):
            return _Resp({"ok": False, "error": "boom"}, 500)

    monkeypatch.setattr(latency_lab.httpx, "AsyncClient", lambda *a, **k: _Err())
    assert _scan(a, pid, lid, rows).status_code == 502
    got = client.get("/api/latency/config", headers=a).json()
    assert got["scan_count"] == 0  # неуспешный запуск не записан

    # А вот принятый скан метку пишет — и следующий уже 429.
    _patch(monkeypatch, {"/api/lab/multiscan":
                         {"ok": True, "req_id": "req-1", "status": "pending"}})
    assert _scan(a, pid, lid, rows).status_code == 200
    assert _scan(a, pid, lid, rows).status_code == 429


def test_save_config_persists_scan_limit_and_public():
    """save_config сохраняет лимит; _public отдаёт его + scan_count без меток."""
    a = _auth()
    r = _configure(a, scan_limit=5, scan_window_hours=12)
    assert r.status_code == 200
    body = r.json()
    assert body["scan_limit"] == 5 and body["scan_window_hours"] == 12
    assert body["scan_count"] == 0

    got = client.get("/api/latency/config", headers=a).json()
    assert got["scan_limit"] == 5 and got["scan_window_hours"] == 12
    assert got["scan_count"] == 0
    assert "scan_history" not in got  # сами метки наружу не уходят


def test_scan_rate_limit_rejects_bad_window(monkeypatch):
    """Окно лимита ограничено 1…720 ч (и отрицательный лимит не пройдёт)."""
    a = _auth()
    assert _configure(a, scan_window_hours=0).status_code == 422
    assert _configure(a, scan_window_hours=721).status_code == 422
    assert _configure(a, scan_limit=-1).status_code == 422
    assert _configure(a, scan_window_hours=720).status_code == 200


# ── время сброса лимита (reset_at / reset_in_seconds) ────────
def _public_cfg(a):
    return client.get("/api/latency/config", headers=a).json()


def _reset_ts(got):
    """reset_at (ISO-8601 UTC) → epoch; сам `got` не трогаем."""
    return datetime.fromisoformat(got["reset_at"].replace("Z", "+00:00")).timestamp()


def test_scan_reset_empty_history():
    """Пустая история: reset_at ≈ now + окно (24 ч), секунд ≈ 86400."""
    a, aid = _mk_account()
    _configure(a, scan_limit=2, scan_window_hours=24)
    got = _public_cfg(a)
    assert got["scan_count"] == 0
    assert "reset_at" in got and "reset_in_seconds" in got
    now = time.time()
    assert now + 23.5 * 3600 <= _reset_ts(got) <= now + 24.5 * 3600
    assert abs(got["reset_in_seconds"] - 86400) <= 60


def test_scan_reset_mark_1h_ago():
    """Метка 1 ч назад → старейшая в окне выпадет через window - 1 ч (23 ч)."""
    a, aid = _mk_account()
    _configure(a, scan_limit=2, scan_window_hours=24)
    _set_history(aid, [time.time() - 3600])
    got = _public_cfg(a)
    now = time.time()
    assert now + 22.5 * 3600 <= _reset_ts(got) <= now + 23.5 * 3600
    assert abs(got["reset_in_seconds"] - 23 * 3600) <= 60


def test_scan_reset_oldest_mark_wins():
    """Старейшая метка в окне определяет reset: более свежая — не влияет."""
    a, aid = _mk_account()
    _configure(a, scan_limit=2, scan_window_hours=24)
    _set_history(aid, [time.time() - 3600, time.time() - 1800])
    got = _public_cfg(a)
    assert abs(got["reset_in_seconds"] - 23 * 3600) <= 60


def test_scan_reset_exhausted_limit():
    """Лимит исчерпан (2 из 2) — reset в момент выпадения старейшей метки."""
    a, aid = _mk_account()
    _configure(a, scan_limit=2, scan_window_hours=24)
    now = time.time()
    _set_history(aid, [now - 3 * 3600, now - 1 * 3600])
    got = _public_cfg(a)
    assert got["scan_count"] == 2
    assert now + 20.5 * 3600 <= _reset_ts(got) <= now + 21.5 * 3600  # через 21 ч
    assert abs(got["reset_in_seconds"] - 21 * 3600) <= 60


def test_scan_reset_zero_limit():
    """scan_limit=0 — сбрасывать нечего: reset_at пуст, секунды 0."""
    a = _auth()
    _configure(a, scan_limit=0, scan_window_hours=24)
    got = _public_cfg(a)
    assert got["scan_limit"] == 0
    assert got["reset_at"] == ""
    assert got["reset_in_seconds"] == 0


def test_scan_reset_public_contains_fields():
    """_public отдаёт reset_at/reset_in_seconds и в POST-ответе, и в GET."""
    a = _auth()
    r = _configure(a, scan_limit=5, scan_window_hours=12)
    assert r.status_code == 200
    assert "reset_at" in r.json() and "reset_in_seconds" in r.json()
    got = _public_cfg(a)
    assert "reset_at" in got and "reset_in_seconds" in got
    assert "scan_history" not in got  # сами метки наружу не уходят


def test_scan_reset_helper_formula():
    """Хелпер напрямую (фиксированный now): reset_ts = старейшая В окне + окно."""
    from app.models.settings import LatencyLabConfig

    now = 1_700_000_000.0 + 7200
    cfg = LatencyLabConfig(scan_limit=2, scan_window_hours=24,
                           scan_history=[1_700_000_000.0, 1_700_000_000.0 + 3600])
    reset_ts, reset_in = latency_lab.scan_reset(cfg, now=now)
    assert reset_ts == 1_700_000_000.0 + 24 * 3600  # старейшая (0 ч) + окно
    assert reset_in == 24 * 3600 - 7200             # через 22 ч

    # Пустое окно → полное окно от now.
    reset_ts, reset_in = latency_lab.scan_reset(
        LatencyLabConfig(scan_limit=2, scan_window_hours=24), now=now)
    assert reset_ts == now + 24 * 3600 and reset_in == 24 * 3600

    # Лимит 0 → нечего сбрасывать.
    reset_ts, reset_in = latency_lab.scan_reset(
        LatencyLabConfig(scan_limit=0, scan_window_hours=24), now=now)
    assert reset_ts == 0.0 and reset_in == 0


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


def test_normalize_job_status_maps_synonyms():
    """Синонимы Latency Lab → канонические статусы поллинга панели."""
    assert latency_lab.normalize_job_status("success") == "done"
    assert latency_lab.normalize_job_status("completed") == "done"
    assert latency_lab.normalize_job_status("finished") == "done"
    assert latency_lab.normalize_job_status("ok") == "done"
    assert latency_lab.normalize_job_status("failed") == "error"
    assert latency_lab.normalize_job_status("cancelled") == "cancelled"
    assert latency_lab.normalize_job_status("running") == "pending"
    assert latency_lab.normalize_job_status("") == "pending"


def test_job_status_normalizes_synonyms(monkeypatch):
    """GET /latency-scan/{id} отдаёт канонический статус: «success» → done,
    «failed» → error. Иначе поллинг с одной подсетью ждал бы «done» вечно."""
    a = _auth()
    _configure(a)
    for raw, want in (("success", "done"), ("completed", "done"),
                      ("finished", "done"), ("ok", "done"),
                      ("failed", "error"), ("cancelled", "cancelled"),
                      ("running", "pending")):
        _patch(monkeypatch, {"/api/lab/job/": {"ok": True, "status": raw,
                                               "result": {"alive_count": 1}}})
        r = client.get("/api/subnets/latency-scan/req-1", headers=a)
        assert r.status_code == 200, raw
        assert r.json()["status"] == want, raw


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
