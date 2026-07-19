"""Plan A Ф2 — «Server uptime» monitor: store CRUD/analytics + probe + routes."""
import asyncio
import time
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.services import server_monitor_store as store
import app.api.server_monitor as sm

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"sm-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _seed_samples(acc, rows):
    """rows: [(ts, server_id, online, latency_ms)]."""
    with store._connect(acc) as conn:
        conn.executemany(
            "INSERT INTO server_samples (ts, server_id, online, latency_ms) VALUES (?,?,?,?)",
            rows)


# ── store: CRUD + isolation ──

def test_server_crud_and_isolation():
    a = f"acc-{uuid.uuid4().hex[:8]}"
    b = f"acc-{uuid.uuid4().hex[:8]}"
    s = asyncio.run(store.add_server("Node A", "DE", "1.2.3.4", 443, "note", "manual", a))
    assert s["id"] and s["source"] == "manual"
    assert [x["ip"] for x in asyncio.run(store.list_servers(a))] == ["1.2.3.4"]
    assert asyncio.run(store.list_servers(b)) == []  # per-account isolation

    upd = asyncio.run(store.update_server(s["id"], {"name": "Renamed", "port": 8443}, a))
    assert upd["name"] == "Renamed" and upd["port"] == 8443
    assert asyncio.run(store.delete_server(s["id"], a)) is True
    assert asyncio.run(store.list_servers(a)) == []


def test_sync_deployed_upsert_and_prune():
    acc = f"acc-{uuid.uuid4().hex[:8]}"
    asyncio.run(store.sync_deployed(
        [{"name": "N1", "country": "NL", "ip": "5.5.5.5", "port": 443}], acc))
    rows = asyncio.run(store.list_servers(acc))
    assert len(rows) == 1 and rows[0]["source"] == "deployed"
    # re-sync without 5.5.5.5 → the deployed row is pruned
    asyncio.run(store.sync_deployed([{"name": "N2", "country": "US", "ip": "6.6.6.6"}], acc))
    ips = {r["ip"] for r in asyncio.run(store.list_servers(acc))}
    assert ips == {"6.6.6.6"}
    # a manual server survives a deployed sync
    asyncio.run(store.add_server("M", "DE", "9.9.9.9", 443, "", "manual", acc))
    asyncio.run(store.sync_deployed([], acc))
    ips = {r["ip"] for r in asyncio.run(store.list_servers(acc))}
    assert ips == {"9.9.9.9"}  # manual kept, deployed all pruned


# ── store: analytics on seeded samples ──

def test_analytics_bars_uptime_incidents():
    acc = f"acc-{uuid.uuid4().hex[:8]}"
    s = asyncio.run(store.add_server("Srv", "DE", "1.1.1.1", 443, "", "manual", acc))
    sid = s["id"]
    now = int(time.time())
    # up, up(slow), down, up  → one recovered incident
    _seed_samples(acc, [
        (now - 400, sid, 1, 20),
        (now - 300, sid, 1, 900),   # slow
        (now - 200, sid, 0, -1),    # down
        (now - 100, sid, 1, 15),    # recovered
    ])
    bars = asyncio.run(store.get_bars(30, acc))
    assert [b["status"] for b in bars[sid]] == ["up", "slow", "down", "up"]

    up30 = asyncio.run(store.get_uptime_30d(acc))
    assert up30["per_node"][sid] == 75.0  # 3/4 online

    latest = asyncio.run(store.get_latest(acc))
    assert latest[sid]["online"] is True and latest[sid]["latency_ms"] == 15

    inc = asyncio.run(store.get_incidents(7, acc))
    assert len(inc) == 1 and inc[0]["ongoing"] is False and inc[0]["durationSec"] == 100
    assert inc[0]["name"] == "Srv" and inc[0]["group"] == "DE"


# ── probe ──

def test_probe_tcp_up():
    async def run():
        server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            online, rtt = await sm._probe("127.0.0.1", port)
        finally:
            server.close()
            await server.wait_closed()
        return online, rtt
    online, rtt = asyncio.run(run())
    assert online is True and rtt >= 0


def test_probe_down(monkeypatch):
    async def _no_icmp(ip):
        return False, -1
    monkeypatch.setattr(sm, "_icmp", _no_icmp)
    # 127.0.0.1 with a very-unlikely-open port → TCP refused fast, ICMP stubbed down
    online, rtt = asyncio.run(sm._probe("127.0.0.1", 65533))
    assert online is False and rtt == -1


# ── routes ──

def test_routes_require_auth():
    assert client.get("/api/server-monitor/servers").status_code == 401
    assert client.get("/api/server-monitor/statuspage").status_code == 401


def test_route_crud_and_statuspage():
    h = _auth()
    # cold statuspage → empty, not 500
    r = client.get("/api/server-monitor/statuspage", headers=h)
    assert r.status_code == 200 and r.json()["nodes"] == []
    assert r.json()["global"]["state"] == "unknown"

    # create + list
    r = client.post("/api/server-monitor/servers", headers=h,
                    json={"name": "S1", "country": "DE", "ip": "1.2.3.4", "port": 443, "note": "x"})
    assert r.status_code == 201
    sid = r.json()["id"]
    assert len(client.get("/api/server-monitor/servers", headers=h).json()) == 1

    # bad IP → 422
    assert client.post("/api/server-monitor/servers", headers=h,
                       json={"ip": "not-an-ip"}).status_code == 422

    # patch + delete
    r = client.patch(f"/api/server-monitor/servers/{sid}", headers=h, json={"name": "S1b"})
    assert r.status_code == 200 and r.json()["name"] == "S1b"
    assert client.delete(f"/api/server-monitor/servers/{sid}", headers=h).status_code == 204
    assert client.delete(f"/api/server-monitor/servers/{sid}", headers=h).status_code == 404


def test_sync_deployed_route():
    h = _auth()
    r = client.post("/api/server-monitor/servers/sync-deployed", headers=h,
                    json=[{"name": "N", "country": "US", "ip": "7.7.7.7", "port": 443}])
    assert r.status_code == 200 and r.json()["synced"] == 1
    servers = client.get("/api/server-monitor/servers", headers=h).json()
    assert servers[0]["source"] == "deployed" and servers[0]["ip"] == "7.7.7.7"
