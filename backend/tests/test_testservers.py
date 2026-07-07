"""Ф1 (wave1) — test-server registry: store CRUD, /api/testservers routes,
deploy_script safety (ufw rules only for valid IPs, everything quoted) and the
SSH-deploy endpoint with a mocked SSHSession (creds transient, streamed Task).
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import testserver_registry as tsr
from app.services.task_store import TaskStatus, task_store
import app.api.testservers as tsapi

client = TestClient(app)


def _auth():
    r = client.post(
        "/api/auth/register",
        json={"login": f"ts-{uuid.uuid4().hex[:8]}", "password": "pw"},
    )
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ── registry store ────────────────────────────────────────────


def test_store_crud_and_isolation():
    acc = f"acc-{uuid.uuid4().hex[:8]}"
    assert tsr.list_servers(acc) == []  # empty registry

    srv = tsr.add_server("T1", "1.2.3.4", 5201, acc)
    assert srv["ip"] == "1.2.3.4" and srv["iperf_port"] == 5201
    assert len(srv["id"]) == 12
    assert [s["id"] for s in tsr.list_servers(acc)] == [srv["id"]]

    with pytest.raises(ValueError):  # duplicate ip+port
        tsr.add_server("T1b", "1.2.3.4", 5201, acc)
    # same ip on ANOTHER port is allowed
    srv2 = tsr.add_server("T1c", "1.2.3.4", 5202, acc)
    with pytest.raises(ValueError):  # invalid IP
        tsr.add_server("bad", "999.1.1.1", 5201, acc)
    with pytest.raises(ValueError):  # garbage IP
        tsr.add_server("bad", "evil; rm -rf /", 5201, acc)

    other = f"acc-{uuid.uuid4().hex[:8]}"  # per-account isolation
    assert tsr.list_servers(other) == []

    assert tsr.remove_server(srv["id"], acc) is True
    assert tsr.remove_server(srv["id"], acc) is False  # already gone
    assert [s["id"] for s in tsr.list_servers(acc)] == [srv2["id"]]


def test_deploy_script_ufw_only_valid_ips_and_quoted():
    s = tsr.deploy_script(5201, ["1.2.3.4", "evil; rm -rf /", "8.8.8.8", "999.1.1.1"])
    assert "ufw allow from 1.2.3.4 to any port 5201 proto tcp" in s
    assert "ufw allow from 8.8.8.8 to any port 5201 proto tcp" in s
    assert "evil" not in s and "rm -rf /" not in s  # invalid entries dropped
    assert "999.1.1.1" not in s
    # composed of the shared installer + the iperf3 systemd unit
    assert "iperf3-server.service" in s
    assert "Xray-linux-64.zip" in s
    # UFW is never force-enabled by the deploy
    assert "ufw enable" not in s and "ufw --force enable" not in s


def test_deploy_script_empty_allowlist():
    s = tsr.deploy_script(5201, [])
    assert "ufw allow from" not in s
    assert "iperf3-server.service" in s


# ── API routes ────────────────────────────────────────────────


def test_routes_require_auth():
    assert client.get("/api/testservers").status_code == 401


def test_api_crud_flow():
    h = _auth()
    assert client.get("/api/testservers", headers=h).json() == {"servers": []}

    r = client.post("/api/testservers", headers=h, json={"name": "T", "ip": "5.6.7.8"})
    assert r.status_code == 200
    sid = r.json()["id"]
    assert r.json()["iperf_port"] == 5201  # default

    # duplicate → 409 (эталон: checker_registry)
    assert (
        client.post("/api/testservers", headers=h, json={"ip": "5.6.7.8"}).status_code
        == 409
    )
    # invalid ip → 422 (model validator)
    assert (
        client.post("/api/testservers", headers=h, json={"ip": "not-an-ip"}).status_code
        == 422
    )
    # invalid port → 422
    assert (
        client.post(
            "/api/testservers", headers=h, json={"ip": "9.9.9.9", "iperf_port": 70000}
        ).status_code
        == 422
    )

    assert [
        s["id"] for s in client.get("/api/testservers", headers=h).json()["servers"]
    ] == [sid]

    # other account sees nothing (isolation)
    h2 = _auth()
    assert client.get("/api/testservers", headers=h2).json() == {"servers": []}

    assert client.delete("/api/testservers/nope", headers=h).status_code == 404
    assert client.delete(f"/api/testservers/{sid}", headers=h).status_code == 200
    assert client.get("/api/testservers", headers=h).json() == {"servers": []}


# ── SSH deploy endpoint (mocked SSH) ──────────────────────────


def test_deploy_endpoint_mocked_ssh(monkeypatch):
    h = _auth()
    captured = {}

    class FakeSSH:
        def __init__(self, *a, **k):
            pass

        async def connect(self, timeout=30):
            pass

        async def run_script(self, script, task, check=True, timeout=None):
            captured["script"] = script
            return 0

        async def close(self):
            pass

    async def fake_backend_ip():
        return "9.9.9.9"

    monkeypatch.setattr(tsapi, "SSHSession", FakeSSH)
    monkeypatch.setattr(tsapi, "get_backend_ip", fake_backend_ip)

    r = client.post(
        "/api/testservers/deploy",
        headers=h,
        json={
            "name": "D1",
            "ip": "3.3.3.3",
            "ssh_password": "pw",
            "allow_ips": ["7.7.7.7", "not-an-ip"],
        },
    )
    assert r.status_code == 200
    task_id = r.json()["task_id"]
    task = task_store.get(task_id)
    assert task is not None and task.status == TaskStatus.SUCCESS

    # deploy script got the node allowlist + the backend IP; junk dropped
    assert "ufw allow from 7.7.7.7 to any port 5201 proto tcp" in captured["script"]
    assert "ufw allow from 9.9.9.9 to any port 5201 proto tcp" in captured["script"]
    assert "not-an-ip" not in captured["script"]
    # SSH creds are transient — never in the stored record
    servers = client.get("/api/testservers", headers=h).json()["servers"]
    assert [s["ip"] for s in servers] == ["3.3.3.3"]
    assert "ssh_password" not in servers[0] and "pw" not in str(servers[0])


def test_deploy_endpoint_ssh_failure_fails_task(monkeypatch):
    h = _auth()

    class BoomSSH:
        def __init__(self, *a, **k):
            pass

        async def connect(self, timeout=30):
            raise RuntimeError("connection refused")

        async def close(self):
            pass

    monkeypatch.setattr(tsapi, "SSHSession", BoomSSH)

    r = client.post(
        "/api/testservers/deploy",
        headers=h,
        json={"ip": "4.4.4.4", "ssh_password": "pw"},
    )
    assert r.status_code == 200  # task created; failure surfaces in the stream
    task = task_store.get(r.json()["task_id"])
    assert task.status == TaskStatus.FAILED
    # nothing registered on failure
    assert client.get("/api/testservers", headers=h).json() == {"servers": []}


def test_deploy_endpoint_invalid_ip_422():
    h = _auth()
    r = client.post(
        "/api/testservers/deploy",
        headers=h,
        json={"ip": "evil; rm", "ssh_password": "pw"},
    )
    assert r.status_code == 422
