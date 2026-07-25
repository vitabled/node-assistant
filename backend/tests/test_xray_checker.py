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

    async def fake_fetch(base_url=None, cfg=None):
        return [{"stableId": "n1", "online": True}, {"stableId": "n2", "online": False}]

    async def fake_record(proxies):
        recorded["n"] = len(proxies)

    monkeypatch.setattr(xcapi.xc, "fetch_proxies", fake_fetch)
    monkeypatch.setattr(xcapi.metrics_store, "record_samples", fake_record)

    count = asyncio.run(xcapi._sample_once())
    assert count == 2
    assert recorded["n"] == 2


def test_sample_once_returns_zero_when_fetch_fails(monkeypatch):
    async def boom(base_url=None, cfg=None):
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
    assert xcapi._parse_tag("acc1:sub1|MyNode") == ("acc1", "sub1", "MyNode")
    assert xcapi._parse_tag("acc1:sub1") == ("", "", "acc1:sub1")  # no pipe → untagged
    assert xcapi._parse_tag("Plain") == ("", "", "Plain")
    px = [{"name": "acc1:s1|A"}, {"name": "acc2:s1|B"}, {"name": "acc1:s2|C"}]
    kept = xcapi._filter_by_account(px, "acc1")
    assert [p["name"] for p in kept] == ["A", "C"]
    # subId stashed for per-subscription grouping on the dashboard
    assert [p["subId"] for p in kept] == ["s1", "s2"]
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

    async def proxies(base_url=None):
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

    async def fake_summary(base_url=None):
        return {"total": 99, "online": 99, "offline": 0, "avgLatencyMs": 5}  # global — must be ignored

    async def fake_proxies(base_url=None):
        return [
            _mk_proxy(f"{_acc(a)}:s1|A", "n1", online=True),
            _mk_proxy("otheracct:s1|B", "n2", online=True),
        ]

    async def fake_info(base_url=None):
        return {}

    async def fake_uptime(_h, _cid=None):
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


# ── per-node hiding on Xray uptime (deferred backlog item) ────
def test_hidden_node_excluded_from_counts_but_shipped_with_flag(monkeypatch):
    a = _auth()

    async def running():
        return "running"

    async def proxies(base_url=None):
        return [
            _mk_proxy(f"{_acc(a)}:s1|Alpha", "n1", online=True),
            _mk_proxy(f"{_acc(a)}:s1|Beta", "n2", online=False),
        ]

    monkeypatch.setattr(xcapi.xc, "container_state", running)
    monkeypatch.setattr(xcapi.xc, "fetch_proxies", proxies)

    # Baseline: both shown, the offline one keeps the banner "partial".
    r0 = client.get("/api/checker/statuspage?ticks=30", headers=a).json()
    assert r0["global"]["total"] == 2 and r0["global"]["state"] == "partial"

    # Hide the offline node "n2".
    t = client.post("/api/stats/users/hidden/checker", headers=a,
                    json={"checker_id": "local", "stable_id": "n2",
                          "name": "Beta", "hidden": True})
    assert t.status_code == 200

    r1 = client.get("/api/checker/statuspage?ticks=30", headers=a).json()
    # counts drop the hidden node → banner goes green
    assert r1["global"]["total"] == 1 and r1["global"]["online"] == 1
    assert r1["global"]["state"] == "ok"
    # but the node is still SHIPPED, flagged, so the UI can show a "hidden" section
    by_id = {n["stableId"]: n for n in r1["nodes"]}
    assert by_id["n2"]["hidden"] is True and by_id["n1"]["hidden"] is False
    assert len(r1["nodes"]) == 2   # nothing dropped from the list, only from counts


def test_unhiding_restores_the_node_to_counts(monkeypatch):
    a = _auth()

    async def running():
        return "running"

    async def proxies(base_url=None):
        return [_mk_proxy(f"{_acc(a)}:s1|Alpha", "n1", online=True)]

    monkeypatch.setattr(xcapi.xc, "container_state", running)
    monkeypatch.setattr(xcapi.xc, "fetch_proxies", proxies)

    client.post("/api/stats/users/hidden/checker", headers=a,
                json={"checker_id": "local", "stable_id": "n1", "hidden": True})
    r = client.get("/api/checker/statuspage", headers=a).json()
    assert r["global"]["total"] == 0 and r["global"]["state"] == "unknown"

    client.post("/api/stats/users/hidden/checker", headers=a,
                json={"checker_id": "local", "stable_id": "n1", "hidden": False})
    r = client.get("/api/checker/statuspage", headers=a).json()
    assert r["global"]["total"] == 1 and r["nodes"][0]["hidden"] is False


def test_hidden_set_is_shared_with_the_stats_picker(monkeypatch):
    """Hiding on the dashboard and the stats «Серверы» picker are ONE set — both
    key on (checker_id, stableId)."""
    a = _auth()
    client.post("/api/stats/users/hidden/checker", headers=a,
                json={"checker_id": "local", "stable_id": "n9", "name": "Node9", "hidden": True})
    w = client.get("/api/stats/users/widgets", headers=a).json()
    assert w["hidden"]["checker"]["local"]["n9"] == "Node9"


def test_toggle_preserves_widget_layout(monkeypatch):
    """The dashboard toggle must not clobber the stats layout (it does a
    read-modify-write, not a full replace)."""
    a = _auth()
    r = client.put("/api/stats/users/widgets", headers=a, json={
        "layout": [{"instance_id": "w1", "kind": "node-load", "w": 2, "order": 0, "settings": {}}],
        "hidden": {"nodes": {}, "checker": {}},
    })
    assert r.status_code == 200, r.text
    client.post("/api/stats/users/hidden/checker", headers=a,
                json={"checker_id": "local", "stable_id": "n1", "hidden": True})
    w = client.get("/api/stats/users/widgets", headers=a).json()
    assert [x["instance_id"] for x in w["layout"]] == ["w1"]   # layout intact
    assert "n1" in w["hidden"]["checker"]["local"]


def test_hidden_incidents_are_dropped(monkeypatch):
    a = _auth()

    async def running():
        return "running"

    async def get_incidents(days, cid):
        return [{"stableId": "n1", "name": "Alpha"}, {"stableId": "n2", "name": "Beta"}]

    monkeypatch.setattr(xcapi.xc, "container_state", running)
    monkeypatch.setattr(xcapi.metrics_store, "get_incidents", get_incidents)

    client.post("/api/stats/users/hidden/checker", headers=a,
                json={"checker_id": "local", "stable_id": "n2", "hidden": True})
    r = client.get("/api/checker/incidents?days=7", headers=a).json()
    assert [i["stableId"] for i in r["incidents"]] == ["n1"]
