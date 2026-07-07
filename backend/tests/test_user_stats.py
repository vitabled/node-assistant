"""Ф3 — user-stats store + routes + collector.

Store reads are tested with directly-seeded rows (controlled ts) so migration
detection is deterministic; routes/collector are tested via TestClient + mocks.
"""
import asyncio
import time
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.services import user_stats_store as store
import app.api.user_stats as us

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"us-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _seed(acc, load_rows, top_rows):
    with store._connect(acc) as conn:
        if load_rows:
            conn.executemany(
                "INSERT INTO node_load_samples (ts,node_uuid,node_name,users_online) VALUES (?,?,?,?)",
                load_rows)
        if top_rows:
            conn.executemany(
                "INSERT INTO node_top_users (ts,node_uuid,username,total_bytes) VALUES (?,?,?,?)",
                top_rows)


# ── store: node-load ──

def test_node_load_series_and_ranking():
    acc = f"acc-{uuid.uuid4().hex[:8]}"
    now = int(time.time())
    _seed(acc, [
        (now - 600, "nodeA", "A", 5),
        (now - 300, "nodeA", "A", 7),
        (now - 600, "nodeB", "B", 1),
    ], [])
    res = asyncio.run(store.node_load(24, acc))
    nodes = {n["node_uuid"]: n for n in res["nodes"]}
    assert nodes["nodeA"]["current_online"] == 7
    assert nodes["nodeA"]["peak_online"] == 7
    assert len(nodes["nodeA"]["points"]) == 2
    assert res["nodes"][0]["node_uuid"] == "nodeA"   # busiest first (avg desc)


# ── store: best-effort migrations ──

def test_migrations_best_effort_from_to():
    acc = f"acc-{uuid.uuid4().hex[:8]}"
    now = int(time.time())
    # alice dominant on nodeA at T1 (100>50), on nodeB at T2 (200>100) → A→B once
    _seed(acc, [], [
        (now - 600, "nodeA", "alice", 100),
        (now - 600, "nodeB", "alice", 50),
        (now - 300, "nodeA", "alice", 100),
        (now - 300, "nodeB", "alice", 200),
    ])
    res = asyncio.run(store.migrations(24, acc))
    assert res["approximate"] is True
    assert {"from_node": "nodeA", "to_node": "nodeB", "count": 1} in res["migrations"]


def test_record_snapshot_roundtrip():
    acc = f"acc-{uuid.uuid4().hex[:8]}"
    asyncio.run(store.record_snapshot(
        [{"nodeUuid": "n1", "nodeName": "N1", "usersOnline": 3}],
        {"n1": [{"username": "bob", "total": 10}]}, acc))
    load = asyncio.run(store.node_load(24, acc))
    assert load["nodes"][0]["node_uuid"] == "n1" and load["nodes"][0]["current_online"] == 3
    top = asyncio.run(store.top_users(24, acc))
    assert top["users"][0]["username"] == "bob"


# ── routes: cold start (empty, not 500) + auth ──

def test_routes_require_auth():
    assert client.get("/api/stats/users/node-load").status_code == 401


def test_cold_start_returns_empty_not_500():
    h = _auth()
    for path in ("node-load", "top-users", "migrations"):
        r = client.get(f"/api/stats/users/{path}", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body.get("nodes", body.get("users", body.get("migrations"))) == []


# ── collector: edge-cases ──

def test_collector_skips_unconfigured_remnawave(monkeypatch):
    monkeypatch.setattr(us.storage, "load_settings", lambda aid=None: {})
    asyncio.run(us._collect_account(f"acc-{uuid.uuid4().hex[:8]}"))  # no-op, no raise


def test_collector_survives_remnawave_error(monkeypatch):
    monkeypatch.setattr(us.storage, "load_settings",
                        lambda aid=None: {"remnawave": {"panel_url": "http://p", "api_token": "t"}})

    class Boom:
        def __init__(self, *a):
            pass

        async def get_nodes_metrics(self):
            raise RuntimeError("remnawave down")

    monkeypatch.setattr(us, "RemnavaveClient", Boom)
    asyncio.run(us._collect_account(f"acc-{uuid.uuid4().hex[:8]}"))  # logged + skipped, no raise


def test_collector_records_snapshot(monkeypatch):
    acc = f"acc-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(us.storage, "load_settings",
                        lambda aid=None: {"remnawave": {"panel_url": "http://p", "api_token": "t"}})

    class Fake:
        def __init__(self, *a):
            pass

        async def get_nodes_metrics(self):
            return [{"nodeUuid": "n1", "nodeName": "N1", "usersOnline": 4}]

        async def get_node_users_usage(self, node_uuid):
            return {"topUsers": [{"username": "bob", "total": 99}]}

    monkeypatch.setattr(us, "RemnavaveClient", Fake)
    asyncio.run(us._collect_account(acc))
    res = asyncio.run(store.node_load(24, acc))
    assert res["nodes"] and res["nodes"][0]["node_uuid"] == "n1"
