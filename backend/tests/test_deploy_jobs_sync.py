"""Remnawave → deploy-job cards sync: add / remove / skip / idempotency / tombstones.

Remnawave itself is never touched: `list_nodes` is replaced by a fake client, and
every credential value in this file is invented (`test-pass`), never a real one.
"""
import json
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.api import deploy_jobs_sync
from app.services import accounts

client = TestClient(app)


# ── helpers ───────────────────────────────────────────────────────────────────

def _auth() -> tuple[str, dict[str, str]]:
    response = client.post(
        "/api/auth/register",
        json={"login": f"sync-{uuid.uuid4().hex[:8]}", "password": "pw"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body["id"], {"Authorization": f"Bearer {body['token']}"}


def _card(task_id: str, ip: str, mode: str = "remnanode", domain: str = "") -> dict:
    return {
        "taskId": task_id,
        "domain": domain or f"{task_id}.example.com",
        "ip": ip,
        "newSshPort": 2222,
        "startedAt": 1720000000000,
        "savedForm": {"mode": mode, "ip": ip, "ssh_user": "root", "ssh_password": "old-pass"},
        "finalStatus": "success",
    }


class _FakeClient:
    def __init__(self, nodes: list[dict]) -> None:
        self._nodes = nodes
        self.calls: list[str] = []

    async def list_nodes(self) -> list[dict]:
        self.calls.append("list_nodes")
        return list(self._nodes)


def _patch_nodes(monkeypatch, nodes: list[dict]) -> _FakeClient:
    fake = _FakeClient(nodes)
    monkeypatch.setattr(deploy_jobs_sync, "_client", lambda: fake)
    return fake


def _seed(headers: dict[str, str], cards: list[dict]) -> None:
    response = client.put("/api/deploy-jobs", headers=headers, json={"jobs": cards})
    assert response.status_code == 200, response.text


def _ips(jobs: list[dict]) -> set[str]:
    return {job["ip"] for job in jobs}


# ── add ───────────────────────────────────────────────────────────────────────

def test_dry_run_reports_adds_without_writing(monkeypatch):
    _account_id, headers = _auth()
    nodes = [
        {"uuid": "u-1", "name": "n1", "address": "203.0.113.5"},
        {"uuid": "u-2", "name": "n2", "address": "198.51.100.7"},
        {"uuid": "u-lan", "name": "docker", "address": "172.30.0.1"},
    ]
    _patch_nodes(monkeypatch, nodes)

    response = client.post(
        "/api/deploy-jobs/sync-remnawave", headers=headers, json={"dry_run": True}
    )

    assert response.status_code == 200, response.text
    report = response.json()
    assert report["dry_run"] is True
    assert {a["ip"] for a in report["added"]} == {"203.0.113.5", "198.51.100.7"}
    assert {a["taskId"] for a in report["added"]} == {"rw-u-1", "rw-u-2"}
    assert report["removed"] == []
    assert report["skipped"] == [{"ip": "172.30.0.1", "reason": "private-address"}]
    assert report["nodes_total"] == 3
    assert report["cards_before"] == 0
    assert report["cards_after"] == 2
    # dry run wrote nothing
    assert client.get("/api/deploy-jobs", headers=headers).json() == {"jobs": []}


def test_apply_creates_stable_synthetic_cards(monkeypatch):
    _account_id, headers = _auth()
    _patch_nodes(monkeypatch, [{"uuid": "u-1", "name": "n1", "address": "203.0.113.5"}])

    response = client.post(
        "/api/deploy-jobs/sync-remnawave", headers=headers, json={"dry_run": False}
    )
    assert response.status_code == 200, response.text
    assert response.json()["added"] == [
        {"taskId": "rw-u-1", "ip": "203.0.113.5", "domain": ""}
    ]

    jobs = client.get("/api/deploy-jobs", headers=headers).json()["jobs"]
    assert len(jobs) == 1
    job = jobs[0]
    assert job["taskId"] == "rw-u-1"  # stable, derived from the node uuid
    assert job["ip"] == "203.0.113.5"
    assert job["finalStatus"] == "success"
    assert job["savedForm"]["mode"] == "remnanode"


def test_new_card_without_stored_creds_has_no_password(monkeypatch):
    _account_id, headers = _auth()
    _patch_nodes(monkeypatch, [{"uuid": "u-1", "name": "n1", "address": "203.0.113.5"}])

    client.post("/api/deploy-jobs/sync-remnawave", headers=headers, json={"dry_run": False})

    saved_form = client.get("/api/deploy-jobs", headers=headers).json()["jobs"][0]["savedForm"]
    # no creds store → an empty password (never invented), default SSH port/user
    assert saved_form["ssh_password"] == ""
    assert saved_form["ssh_user"] == "root"
    assert saved_form["new_ssh_port"] == 2222


def test_new_card_takes_creds_from_the_server_store(monkeypatch):
    account_id, headers = _auth()
    _patch_nodes(monkeypatch, [{"uuid": "u-1", "name": "n1", "address": "203.0.113.5"}])
    (accounts.account_dir(account_id) / "node_creds.json").write_text(
        json.dumps(
            {"creds": [
                {"ip": "203.0.113.5", "ssh_user": "deploy", "ssh_password": "test-pass",
                 "new_ssh_port": 2200},
            ]}
        ),
        encoding="utf-8",
    )

    client.post("/api/deploy-jobs/sync-remnawave", headers=headers, json={"dry_run": False})

    job = client.get("/api/deploy-jobs", headers=headers).json()["jobs"][0]
    assert job["newSshPort"] == 2200
    assert job["savedForm"]["ssh_user"] == "deploy"
    assert job["savedForm"]["ssh_password"] == "test-pass"


# ── remove ────────────────────────────────────────────────────────────────────

def test_removes_only_dead_remnanode_cards(monkeypatch):
    _account_id, headers = _auth()
    _seed(
        headers,
        [
            _card("live", "203.0.113.5"),        # in Remnawave → kept
            _card("dead", "203.0.113.9"),        # gone from Remnawave → removed
            _card("relay", "203.0.113.9", mode="haproxy"),  # HAProxy → never removed
        ],
    )
    _patch_nodes(monkeypatch, [{"uuid": "u-1", "name": "n1", "address": "203.0.113.5"}])

    report = client.post(
        "/api/deploy-jobs/sync-remnawave", headers=headers, json={"dry_run": False}
    ).json()

    assert [r["taskId"] for r in report["removed"]] == ["dead"]
    jobs = client.get("/api/deploy-jobs", headers=headers).json()["jobs"]
    assert {j["taskId"] for j in jobs} == {"live", "relay"}


def test_card_kept_when_its_node_is_still_registered(monkeypatch):
    _account_id, headers = _auth()
    _seed(headers, [_card("live", "203.0.113.5")])
    _patch_nodes(monkeypatch, [{"uuid": "u-1", "name": "n1", "address": "203.0.113.5"}])

    report = client.post(
        "/api/deploy-jobs/sync-remnawave", headers=headers, json={"dry_run": False}
    ).json()

    assert report["removed"] == []
    assert _ips(client.get("/api/deploy-jobs", headers=headers).json()["jobs"]) == {"203.0.113.5"}


# ── skip + idempotency ────────────────────────────────────────────────────────

def test_private_addresses_are_skipped_with_reason(monkeypatch):
    _account_id, headers = _auth()
    _patch_nodes(
        monkeypatch,
        [
            {"uuid": "u-1", "name": "n1", "address": "203.0.113.5"},
            {"uuid": "u-lan", "name": "docker", "address": "172.30.0.1"},
            {"uuid": "u-lo", "name": "loop", "address": "127.0.0.1"},
        ],
    )

    report = client.post(
        "/api/deploy-jobs/sync-remnawave", headers=headers, json={"dry_run": True}
    ).json()

    assert sorted(s["ip"] for s in report["skipped"]) == ["127.0.0.1", "172.30.0.1"]
    assert all(s["reason"] == "private-address" for s in report["skipped"])
    assert [a["ip"] for a in report["added"]] == ["203.0.113.5"]


def test_second_run_is_a_no_op(monkeypatch):
    _account_id, headers = _auth()
    _patch_nodes(monkeypatch, [{"uuid": "u-1", "name": "n1", "address": "203.0.113.5"}])

    first = client.post(
        "/api/deploy-jobs/sync-remnawave", headers=headers, json={"dry_run": False}
    ).json()
    assert len(first["added"]) == 1

    second = client.post(
        "/api/deploy-jobs/sync-remnawave", headers=headers, json={"dry_run": False}
    ).json()

    assert second["added"] == []
    assert second["removed"] == []
    assert second["cards_before"] == second["cards_after"] == 1


# ── tombstones ────────────────────────────────────────────────────────────────

def test_delete_is_remembered_and_upsert_cannot_resurrect():
    _account_id, headers = _auth()
    job = _card("gone", "203.0.113.9")
    _seed(headers, [job])

    assert client.delete("/api/deploy-jobs/gone", headers=headers).status_code == 200
    assert client.get("/api/deploy-jobs", headers=headers).json() == {"jobs": []}
    assert client.get("/api/deploy-jobs/tombstones", headers=headers).json() == {
        "taskIds": ["gone"]
    }

    # A stale client re-uploads the card it still has cached: refused (ignored).
    response = client.post("/api/deploy-jobs", headers=headers, json=job)
    assert response.status_code == 200, response.text
    assert response.json()["ignored"] is True
    assert client.get("/api/deploy-jobs", headers=headers).json() == {"jobs": []}


def test_sync_removal_creates_a_tombstone(monkeypatch):
    _account_id, headers = _auth()
    dead = _card("dead", "203.0.113.9")
    _seed(headers, [dead])
    _patch_nodes(monkeypatch, [{"uuid": "u-1", "name": "n1", "address": "203.0.113.5"}])

    client.post("/api/deploy-jobs/sync-remnawave", headers=headers, json={"dry_run": False})

    assert "dead" in client.get("/api/deploy-jobs/tombstones", headers=headers).json()["taskIds"]
    # the browser cache's copy cannot bring it back
    assert client.post("/api/deploy-jobs", headers=headers, json=dead).json()["ignored"] is True


def test_sync_re_add_clears_a_previous_tombstone(monkeypatch):
    _account_id, headers = _auth()
    job = _card("rw-u-1", "203.0.113.5")
    _seed(headers, [job])
    client.delete("/api/deploy-jobs/rw-u-1", headers=headers)
    assert client.get("/api/deploy-jobs", headers=headers).json() == {"jobs": []}

    _patch_nodes(monkeypatch, [{"uuid": "u-1", "name": "n1", "address": "203.0.113.5"}])
    client.post("/api/deploy-jobs/sync-remnawave", headers=headers, json={"dry_run": False})

    # the node is in Remnawave right now — the card is expected back
    assert _ips(client.get("/api/deploy-jobs", headers=headers).json()["jobs"]) == {"203.0.113.5"}
    assert client.get("/api/deploy-jobs/tombstones", headers=headers).json() == {"taskIds": []}


def test_delete_of_missing_card_is_404():
    _account_id, headers = _auth()
    assert client.delete("/api/deploy-jobs/nope", headers=headers).status_code == 404
