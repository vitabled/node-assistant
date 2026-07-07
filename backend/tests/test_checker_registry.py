"""Ф1 — checker registry + checker_id in the shared metrics store.

Covers: the idempotent checker_id migration, per-checker_id filtering in the
metrics queries, the per-account registry store CRUD, and the /api/checker/*
instance routes (incl. per-checker_id statuspage + the SSH-deploy endpoint with a
mocked SSHSession). Docker/network/SSH are all mocked — no real container/box.
"""
import asyncio
import sqlite3
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import metrics_store as ms
from app.services import checker_registry as cr
import app.api.xray_checker as xcapi

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"cr-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ── migration (deleted-resource / empty: pre-existing DB w/o the column) ──

def test_migration_adds_checker_id_and_backfills_local(tmp_path, monkeypatch):
    old = tmp_path / "old_metrics.db"
    conn = sqlite3.connect(old)
    conn.executescript(
        """CREATE TABLE proxy_samples (
             id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL,
             stable_id TEXT NOT NULL, name TEXT NOT NULL, group_name TEXT DEFAULT '',
             online INTEGER NOT NULL, latency_ms INTEGER NOT NULL);"""
    )
    conn.execute(
        "INSERT INTO proxy_samples (ts, stable_id, name, group_name, online, latency_ms) "
        "VALUES (?,?,?,?,?,?)", (int(time.time()), "old1", "OldNode", "DE", 1, 10)
    )
    conn.commit(); conn.close()

    monkeypatch.setattr(ms, "_DB_PATH", old)
    ms._init()          # migration
    ms._init()          # idempotent — second run must not fail

    conn = sqlite3.connect(old); conn.row_factory = sqlite3.Row
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(proxy_samples)")}
    assert "checker_id" in cols
    row = conn.execute("SELECT checker_id FROM proxy_samples WHERE stable_id='old1'").fetchone()
    assert row["checker_id"] == "local"   # existing rows backfilled to the default
    conn.close()


# ── checker_id filtering in the metrics queries ──

def test_checker_id_scopes_metrics_queries():
    a, b = f"cidA-{uuid.uuid4().hex[:6]}", f"cidB-{uuid.uuid4().hex[:6]}"
    sa, sb = f"{a}-n1", f"{b}-n1"
    asyncio.run(ms.record_samples([{"stableId": sa, "name": "A", "online": True, "latencyMs": 5}], checker_id=a))
    asyncio.run(ms.record_samples([{"stableId": sb, "name": "B", "online": False, "latencyMs": -1}], checker_id=b))

    upA = asyncio.run(ms.get_uptime_30d(a))["per_node"]
    assert sa in upA and sb not in upA                      # scoped to checker a
    upB = asyncio.run(ms.get_uptime_30d(b))["per_node"]
    assert sb in upB and sa not in upB
    upAll = asyncio.run(ms.get_uptime_30d())["per_node"]    # None = aggregate over all
    assert sa in upAll and sb in upAll

    barsA = asyncio.run(ms.get_bars(30, a))
    assert sa in barsA and sb not in barsA                  # ring also scoped
    # unknown checker id → empty, not error (empty state)
    assert asyncio.run(ms.get_uptime_30d("nope"))["per_node"] == {}


# ── registry store CRUD + edge cases ──

def test_registry_store_crud_and_isolation():
    acc = f"acc-{uuid.uuid4().hex[:8]}"
    assert [i["id"] for i in cr.list_instances(acc)] == ["local"]     # built-in only

    inst = cr.add_instance("R1", "http://1.2.3.4:2112/", acc)
    assert inst["kind"] == "remote"
    assert inst["base_url"] == "http://1.2.3.4:2112"                  # normalized (trailing / stripped)
    assert [i["id"] for i in cr.list_instances(acc)] == ["local", inst["id"]]

    with pytest.raises(ValueError):                                   # boundary: duplicate URL
        cr.add_instance("R1b", "http://1.2.3.4:2112", acc)
    with pytest.raises(ValueError):                                   # malformed: non-http scheme
        cr.add_instance("bad", "ftp://x", acc)

    cr.update_instance(inst["id"], enabled=False, account_id=acc)
    assert cr.get_instance(inst["id"], acc)["enabled"] is False

    # other account can't see it (per-account isolation)
    other = f"acc-{uuid.uuid4().hex[:8]}"
    assert [i["id"] for i in cr.list_instances(other)] == ["local"]

    assert cr.delete_instance(inst["id"], acc) is True
    assert cr.delete_instance(inst["id"], acc) is False              # deleted-resource: gone
    assert cr.get_instance("nope", acc) is None


def test_test_connection_rejects_bad_url():
    r = asyncio.run(cr.test_connection("ftp://nope"))
    assert r["ok"] is False and "error" in r


def test_remote_deploy_script_has_essentials():
    s = cr.remote_deploy_script("http://sub?token=x", "kutovoys/xray-checker:latest", 2112)
    assert "docker run -d --name xray-checker" in s
    assert "-p 2112:2112" in s
    assert "SUBSCRIPTION_URL='http://sub?token=x'" in s
    assert "kutovoys/xray-checker:latest" in s


# ── API routes (permission / malformed / deleted-resource) ──

def test_instances_route_requires_auth():
    assert client.get("/api/checker/instances").status_code == 401


def test_instance_registry_api_flow():
    h = _auth()
    assert [i["id"] for i in client.get("/api/checker/instances", headers=h).json()["instances"]] == ["local"]

    r = client.post("/api/checker/instances", headers=h, json={"name": "Remote", "base_url": "http://5.6.7.8:2112"})
    assert r.status_code == 200
    iid = r.json()["id"]

    assert client.post("/api/checker/instances", headers=h, json={"base_url": "ftp://x"}).status_code == 422   # malformed
    assert client.post("/api/checker/instances", headers=h, json={"base_url": "http://5.6.7.8:2112"}).status_code == 409  # dup
    assert client.patch("/api/checker/instances/local", headers=h, json={"enabled": False}).status_code == 400  # local locked
    assert client.delete("/api/checker/instances/local", headers=h).status_code == 400
    assert client.patch(f"/api/checker/instances/{iid}", headers=h, json={"enabled": False}).status_code == 200
    assert client.delete("/api/checker/instances/nope", headers=h).status_code == 404  # deleted-resource
    assert client.delete(f"/api/checker/instances/{iid}", headers=h).status_code == 200


def test_ssrf_private_and_loopback_hosts_rejected():
    """SSRF: an account must not register a checker pointing at internal hosts /
    loopback / cloud metadata — those are fetched by the backend and reflected."""
    h = _auth()
    for bad in ("http://127.0.0.1:2112", "http://169.254.169.254/latest/meta-data",
                "http://10.0.0.5:2112", "http://[::1]:2112"):
        r = client.post("/api/checker/instances", headers=h, json={"base_url": bad})
        assert r.status_code == 400, f"{bad} should be blocked, got {r.status_code}"


def test_statuspage_unknown_checker_id_404():
    assert client.get("/api/checker/statuspage?checker_id=doesnotexist", headers=_auth()).status_code == 404


def test_statuspage_remote_instance_fetches_from_base_url(monkeypatch):
    h = _auth()
    iid = client.post("/api/checker/instances", headers=h,
                      json={"name": "R", "base_url": "http://9.9.9.9:2112"}).json()["id"]
    captured = {}

    async def fake_proxies(base_url=None):
        captured["base"] = base_url
        return [{"stableId": "rp1", "name": "RemoteNode", "groupName": "NL",
                 "protocol": "vless", "online": True, "latencyMs": 12}]

    monkeypatch.setattr(xcapi.xc, "fetch_proxies", fake_proxies)
    body = client.get(f"/api/checker/statuspage?checker_id={iid}", headers=h).json()
    assert captured["base"] == "http://9.9.9.9:2112"                 # fetched from the remote base_url
    assert [n["name"] for n in body["nodes"]] == ["RemoteNode"]      # untagged passthrough
    assert body["global"]["total"] == 1 and body["global"]["online"] == 1


# ── poller remote sampling (external-failure) ──

def test_sample_remote_survives_fetch_failure(monkeypatch):
    async def boom(base_url=None):
        raise RuntimeError("remote down")
    monkeypatch.setattr(xcapi.xc, "fetch_proxies", boom)
    assert asyncio.run(xcapi._sample_remote("cidX", "http://x")) == 0


def test_sample_remote_records_under_checker_id(monkeypatch):
    recorded = {}

    async def fake_proxies(base_url=None):
        return [{"stableId": "rr1", "online": True}]

    async def fake_record(proxies, checker_id="local"):
        recorded["cid"] = checker_id
        recorded["n"] = len(proxies)

    monkeypatch.setattr(xcapi.xc, "fetch_proxies", fake_proxies)
    monkeypatch.setattr(xcapi.metrics_store, "record_samples", fake_record)
    assert asyncio.run(xcapi._sample_remote("cid-remote", "http://x")) == 1
    assert recorded == {"cid": "cid-remote", "n": 1}


# ── SSH-deploy endpoint (external-failure + happy path, mocked SSH) ──

def test_deploy_requires_subscription_url():
    # a fresh account has no subscription_url → 400 (can't feed a remote checker)
    r = client.post("/api/checker/instances/deploy", headers=_auth(),
                    json={"ip": "1.1.1.1", "ssh_password": "pw"})
    assert r.status_code == 400


def test_deploy_registers_instance_with_mocked_ssh(monkeypatch):
    h = _auth()

    def fake_load_settings(account_id=None):
        return {"xray_checker": {"subscription_url": "http://sub", "image": "img:1"}}

    monkeypatch.setattr(xcapi.storage, "load_settings", fake_load_settings)

    class FakeSSH:
        def __init__(self, *a, **k):
            pass

        async def connect(self, timeout=30):
            pass

        async def get_output(self, script):
            assert "docker run" in script and "http://sub" in script
            return "xray-checker deployed"

        async def close(self):
            pass

    monkeypatch.setattr("app.services.ssh_manager.SSHSession", FakeSSH)

    r = client.post("/api/checker/instances/deploy", headers=h,
                    json={"ip": "2.2.2.2", "ssh_password": "pw", "host_port": 2112})
    assert r.status_code == 200
    assert r.json()["base_url"] == "http://2.2.2.2:2112"
    # now visible in the registry
    ids = [i["base_url"] for i in client.get("/api/checker/instances", headers=h).json()["instances"]]
    assert "http://2.2.2.2:2112" in ids
