"""Tests for panel sync (sync_store + panel_sync + api/panel_sync).

The destructive SSH backup/restore is mocked; the priority resolution, role
guard, group CRUD/isolation and confirm gating are covered directly.
"""

import asyncio
import uuid

from fastapi.testclient import TestClient

from app.services import panel_sync, sync_store
from app.services.task_store import task_store, TaskStatus
from app.main import app

client = TestClient(app)


def _auth():
    login = f"sy-{uuid.uuid4().hex[:8]}"
    r = client.post("/api/auth/register", json={"login": login, "password": "pw-1"})
    body = r.json()
    return {"Authorization": f"Bearer {body['token']}"}, body["id"]


_MEMBERS = [
    {"panel_key": "p-top", "priority": 30, "role": "primary"},
    {"panel_key": "p-mid", "priority": 20, "role": "primary"},
    {"panel_key": "s-low", "priority": 10, "role": "standby"},
    {"panel_key": "s-high", "priority": 25, "role": "standby"},
]


# ── nearest_higher_primary ────────────────────────────────────
def test_nearest_higher_primary_picks_just_above():
    # s-low (10): higher primaries are p-mid(20) & p-top(30) → nearest = p-mid.
    m = sync_store.nearest_higher_primary(_MEMBERS, "s-low")
    assert m and m["panel_key"] == "p-mid"


def test_nearest_higher_primary_skips_lower():
    # s-high (25): only p-top(30) is strictly higher → p-top.
    m = sync_store.nearest_higher_primary(_MEMBERS, "s-high")
    assert m and m["panel_key"] == "p-top"


def test_nearest_higher_primary_none_for_primary_key():
    assert sync_store.nearest_higher_primary(_MEMBERS, "p-top") is None


def test_nearest_higher_primary_none_when_no_higher():
    members = [
        {"panel_key": "p1", "priority": 5, "role": "primary"},
        {"panel_key": "s1", "priority": 9, "role": "standby"},  # above the only primary
    ]
    assert sync_store.nearest_higher_primary(members, "s1") is None


# ── plan_sync guards ──────────────────────────────────────────
def test_plan_sync_rejects_primary_target():
    group = {"id": "g", "members": _MEMBERS}
    try:
        panel_sync.plan_sync(group, "p-top")
        assert False, "expected SyncError"
    except panel_sync.SyncError as e:
        assert "primary" in str(e).lower()


def test_plan_sync_ok_for_standby():
    group = {"id": "g", "members": _MEMBERS}
    primary, standby = panel_sync.plan_sync(group, "s-low")
    assert primary["panel_key"] == "p-mid" and standby["panel_key"] == "s-low"


# ── API CRUD + isolation ──────────────────────────────────────
def test_groups_require_account():
    assert client.get("/api/sync/groups").status_code == 401


def test_group_crud_and_isolation():
    a, _ = _auth()
    b, _ = _auth()
    r = client.post(
        "/api/sync/groups", headers=a, json={"name": "G1", "members": _MEMBERS}
    )
    assert r.status_code == 201
    gid = r.json()["id"]
    assert any(g["id"] == gid for g in client.get("/api/sync/groups", headers=a).json())
    assert client.get("/api/sync/groups", headers=b).json() == []  # isolated
    # patch
    p = client.patch(f"/api/sync/groups/{gid}", headers=a, json={"auto_sync": True})
    assert p.json()["auto_sync"] is True
    # delete
    assert client.delete(f"/api/sync/groups/{gid}", headers=a).status_code == 204
    assert (
        client.patch(
            f"/api/sync/groups/{gid}", headers=a, json={"name": "x"}
        ).status_code
        == 404
    )


def test_duplicate_priority_rejected():
    a, _ = _auth()
    r = client.post(
        "/api/sync/groups",
        headers=a,
        json={
            "name": "dup",
            "members": [
                {"panel_key": "x", "priority": 5, "role": "primary"},
                {"panel_key": "y", "priority": 5, "role": "standby"},
            ],
        },
    )
    assert r.status_code == 422


# ── run gating ────────────────────────────────────────────────
def _make_group(h):
    return client.post(
        "/api/sync/groups", headers=h, json={"name": "G", "members": _MEMBERS}
    ).json()["id"]


_CREDS = {"ip": "1.2.3.4", "ssh_port": 22, "ssh_user": "root", "ssh_password": "x"}


def test_run_requires_confirm():
    a, _ = _auth()
    gid = _make_group(a)
    r = client.post(
        f"/api/sync/groups/{gid}/run",
        headers=a,
        json={
            "standby_key": "s-low",
            "primary_creds": _CREDS,
            "standby_creds": _CREDS,
            "confirm": False,
        },
    )
    assert r.status_code == 400


def test_run_rejects_primary_target():
    a, _ = _auth()
    gid = _make_group(a)
    r = client.post(
        f"/api/sync/groups/{gid}/run",
        headers=a,
        json={
            "standby_key": "p-top",
            "primary_creds": _CREDS,
            "standby_creds": _CREDS,
            "confirm": True,
        },
    )
    assert r.status_code == 422  # plan_sync guard: cannot restore onto a primary


def test_run_returns_task_id(monkeypatch):
    a, _ = _auth()
    gid = _make_group(a)

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(panel_sync, "run_sync", noop)  # avoid real SSH in the bg task
    r = client.post(
        f"/api/sync/groups/{gid}/run",
        headers=a,
        json={
            "standby_key": "s-low",
            "primary_creds": _CREDS,
            "standby_creds": _CREDS,
            "confirm": True,
        },
    )
    assert r.status_code == 200
    assert r.json()["task_type"] == "panel-sync"


# ── orchestrator flow (fake SSH sessions) ─────────────────────
from app.services import backup_service

_PC = {"ip": "10.0.0.1", "ssh_port": 22, "ssh_user": "root", "ssh_password": "x"}
_SC = {"ip": "10.0.0.2", "ssh_port": 22, "ssh_user": "root", "ssh_password": "y"}


class _FakeSSH:
    def __init__(self, tag, log, backup_rc=0):
        self.tag = tag
        self.log = log
        self.backup_rc = backup_rc
        self.scripts = []

    async def connect(self):
        self.log.append(f"{self.tag}:connect")

    async def run_script(self, script, task, check=False, timeout=None):
        self.scripts.append(script)
        # primary's run_script is the backup; standby's is the restore.
        if self.tag == "primary":
            self.log.append("primary:backup")
            return self.backup_rc
        self.log.append("standby:restore")
        return 0

    async def get_output(self, cmd):
        return "/opt/rw-backup-restore/backups/remnawave_backup_1.tar.gz"

    async def download_file(self, remote, local):
        self.log.append("download")
        with open(local, "wb") as f:
            f.write(b"x" * 512)  # >128 sanity floor

    async def upload_file(self, local, remote):
        self.log.append("upload")

    async def run(self, cmd, task, check=False):
        return 0

    async def close(self):
        pass


def _install_fake_ssh(monkeypatch, backup_rc=0, sink=None):
    log = [] if sink is None else sink
    sessions = {}

    def fake_sess(creds):
        tag = "primary" if creds["ip"] == _PC["ip"] else "standby"
        s = _FakeSSH(tag, log, backup_rc=backup_rc if tag == "primary" else 0)
        sessions[tag] = s
        return s

    monkeypatch.setattr(panel_sync, "_sess", fake_sess)
    return log, sessions


def test_run_sync_transfers_bundle_and_restores_in_order(monkeypatch):
    a, aid = _auth()
    group = sync_store.add_group({"name": "G", "members": _MEMBERS}, aid)
    log, sessions = _install_fake_ssh(monkeypatch)
    task = task_store.create(total_steps=1)
    asyncio.run(panel_sync.run_sync(task, group, "s-low", _PC, _SC, aid))

    # backup primary → relay (download+upload) → restore standby, in that order.
    assert log == [
        "primary:connect",
        "standby:connect",
        "primary:backup",
        "download",
        "upload",
        "standby:restore",
    ]
    assert task.status == TaskStatus.SUCCESS
    # The standby restore targets the RELAYED bundle, not its own local newest.
    assert backup_service.SYNC_BUNDLE_PATH in sessions["standby"].scripts[-1]
    assert sync_store.get_group(group["id"], aid)["last_sync_status"] == "success"


def test_run_sync_backup_failure_stops_before_restore(monkeypatch):
    a, aid = _auth()
    group = sync_store.add_group({"name": "G", "members": _MEMBERS}, aid)
    log, sessions = _install_fake_ssh(monkeypatch, backup_rc=1)
    task = task_store.create(total_steps=1)
    asyncio.run(panel_sync.run_sync(task, group, "s-low", _PC, _SC, aid))

    assert "standby:restore" not in log  # restore MUST NOT run after a failed backup
    assert task.status == TaskStatus.FAILED
    assert sync_store.get_group(group["id"], aid)["last_sync_status"] == "error"


def test_run_sync_inflight_lock_rejects_concurrent(monkeypatch):
    a, aid = _auth()
    group = sync_store.add_group({"name": "G", "members": _MEMBERS}, aid)
    _install_fake_ssh(monkeypatch)
    panel_sync._inflight.add(_SC["ip"])  # simulate an in-progress sync of this standby
    try:
        task = task_store.create(total_steps=1)
        asyncio.run(panel_sync.run_sync(task, group, "s-low", _PC, _SC, aid))
        assert task.status == TaskStatus.FAILED
    finally:
        panel_sync._inflight.discard(_SC["ip"])
