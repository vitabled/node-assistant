"""Tests for api/xray_checker.py — the sampling helper and the account gating on
the checker status route. Docker/network calls are mocked (no real container)."""
import asyncio
import uuid

from fastapi.testclient import TestClient

from app.main import app
import app.api.xray_checker as xcapi

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"xc-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_sample_once_records_scraped_proxies(monkeypatch):
    recorded = {}

    async def fake_fetch():
        return [{"stableId": "n1", "online": True}, {"stableId": "n2", "online": False}]

    async def fake_record(proxies):
        recorded["n"] = len(proxies)

    monkeypatch.setattr(xcapi.xc, "fetch_proxies", fake_fetch)
    monkeypatch.setattr(xcapi.metrics_store, "record_samples", fake_record)

    count = asyncio.run(xcapi._sample_once())
    assert count == 2
    assert recorded["n"] == 2


def test_sample_once_returns_zero_when_fetch_fails(monkeypatch):
    async def boom():
        raise RuntimeError("checker unreachable")

    monkeypatch.setattr(xcapi.xc, "fetch_proxies", boom)
    assert asyncio.run(xcapi._sample_once()) == 0


def test_checker_status_requires_auth():
    assert client.get("/api/checker/status").status_code == 401


def test_checker_status_reports_container_state(monkeypatch):
    async def stopped():
        return "stopped"

    monkeypatch.setattr(xcapi.xc, "container_state", stopped)
    r = client.get("/api/checker/status", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body["container"] == "stopped"
    assert body["reachable"] is False


# ── per-account tag filtering (Ф9) ────────────────────────────

def test_parse_tag_and_filter_helpers():
    assert xcapi._parse_tag("acc1:sub1|MyNode") == ("acc1", "MyNode")
    assert xcapi._parse_tag("acc1:sub1") == ("", "acc1:sub1")  # no pipe → untagged
    assert xcapi._parse_tag("Plain") == ("", "Plain")
    px = [{"name": "acc1:s1|A"}, {"name": "acc2:s1|B"}, {"name": "acc1:s2|C"}]
    assert [p["name"] for p in xcapi._filter_by_account(px, "acc1")] == ["A", "C"]
    # fallback: nothing tagged → passthrough unchanged (single-subscription mode)
    plain = [{"name": "X"}, {"name": "Y"}]
    assert xcapi._filter_by_account(plain, "acc1") == plain


def _mk_proxy(name, sid, online=True, protocol="vless"):
    return {"stableId": sid, "name": name, "groupName": "DE",
            "protocol": protocol, "online": online, "latencyMs": 10}


def test_statuspage_filters_and_strips_tag_per_account(monkeypatch):
    # two accounts, proxies tagged for each; each account sees only its own,
    # with the tag stripped from the name and global counts scoped to it.
    a = _auth(); b = _auth()

    async def running():
        return "running"

    async def proxies():
        return [
            _mk_proxy(f"{_acc(a)}:s1|Alpha", "n1", online=True),
            _mk_proxy(f"{_acc(a)}:s1|Beta", "n2", online=False),
            _mk_proxy(f"{_acc(b)}:s1|Gamma", "n3", online=True),
        ]

    monkeypatch.setattr(xcapi.xc, "container_state", running)
    monkeypatch.setattr(xcapi.xc, "fetch_proxies", proxies)

    ra = client.get("/api/checker/statuspage?ticks=30", headers=a).json()
    names_a = sorted(n["name"] for n in ra["nodes"])
    assert names_a == ["Alpha", "Beta"]           # only account a, tag stripped
    assert ra["global"]["total"] == 2 and ra["global"]["online"] == 1
    # global uptime is scoped to this account's nodes (no shared-DB aggregate)
    assert "uptime30d" in ra["global"]

    rb = client.get("/api/checker/statuspage?ticks=30", headers=b).json()
    assert [n["name"] for n in rb["nodes"]] == ["Gamma"]
    assert rb["global"]["total"] == 1


def _acc(hdr):
    # extract the account id from a bearer token via /api/auth/me
    return client.get("/api/auth/me", headers=hdr).json()["id"]


def test_status_summary_recomputed_per_account(monkeypatch):
    # /status must recompute summary from the FILTERED proxies, not surface the
    # checker's cross-account aggregate.
    a = _auth()

    async def running():
        return "running"

    async def fake_summary():
        return {"total": 99, "online": 99, "offline": 0, "avgLatencyMs": 5}  # global — must be ignored

    async def fake_proxies():
        return [
            _mk_proxy(f"{_acc(a)}:s1|A", "n1", online=True),
            _mk_proxy("otheracct:s1|B", "n2", online=True),
        ]

    async def fake_info():
        return {}

    async def fake_uptime(_h):
        return {}

    monkeypatch.setattr(xcapi.xc, "container_state", running)
    monkeypatch.setattr(xcapi.xc, "fetch_status", fake_summary)
    monkeypatch.setattr(xcapi.xc, "fetch_proxies", fake_proxies)
    monkeypatch.setattr(xcapi.xc, "fetch_system_info", fake_info)
    monkeypatch.setattr(xcapi.metrics_store, "get_node_uptime", fake_uptime)

    body = client.get("/api/checker/status", headers=a).json()
    assert body["summary"]["total"] == 1        # only account a's node, not 99
    assert body["summary"]["online"] == 1
    assert [p["name"] for p in body["proxies"]] == ["A"]
