"""Wave-5 PR-2 — Fail2Ban list: валидация, стор, скрипт синхронизации, роут."""
import uuid as _uuid

from fastapi.testclient import TestClient

from app.main import app
from app.services import accounts, f2b_list

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"f2b-{_uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_validate_entry():
    assert f2b_list.validate_entry("203.0.113.10") == "203.0.113.10"
    assert f2b_list.validate_entry("198.51.100.9/24") == "198.51.100.0/24"  # нормализация
    assert f2b_list.validate_entry("2001:db8::1") == "2001:db8::1"
    for bad in ("", "example.com", "999.1.1.1", "10.0.0.0/33"):
        try:
            f2b_list.validate_entry(bad)
            raise AssertionError(f"{bad} должен был упасть")
        except ValueError:
            pass


def test_save_dedups(tmp_path, monkeypatch):
    aid = "acc-f2b"
    monkeypatch.setattr(accounts, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(accounts, "data_dir", lambda a=None: tmp_path / "accounts" / (a or aid))
    saved = f2b_list.save(["1.1.1.1", "1.1.1.1", "10.0.0.0/8"], aid)
    assert saved == ["1.1.1.1", "10.0.0.0/8"]
    assert f2b_list.load(aid) == saved


def test_sync_script_bans_unbans_persists():
    s = f2b_list.sync_script(["1.2.3.4", "10.0.0.0/8"])
    assert "fail2ban-client set sshd banip" in s
    assert "unbanip" in s                       # снятая запись разбанивается
    assert "nai-banlist.txt" in s and ".prev" in s
    assert "@reboot" in s                       # персистентность
    assert "1.2.3.4" in s and "10.0.0.0/8" in s


def test_route_put_get_roundtrip():
    a = _auth()
    r = client.put("/api/f2b-list", headers=a,
                   json={"entries": ["1.2.3.4", "bad!!"]})
    assert r.status_code == 422
    r = client.put("/api/f2b-list", headers=a,
                   json={"entries": ["1.2.3.4", "192.168.0.0/16"]})
    assert r.status_code == 200 and r.json()["count"] == 2
    r = client.get("/api/f2b-list", headers=a)
    assert r.json()["entries"] == ["1.2.3.4", "192.168.0.0/16"]


class _FakeF2BSSH:
    commands: list[str] = []
    scripts: list[str] = []
    banned = "['198.51.100.8', '2001:db8::8']\n"

    def __init__(self, *args, **kwargs):
        self.host = args[0]

    async def connect(self):
        return None

    async def close(self):
        return None

    async def get_output(self, command):
        self.commands.append(command)
        if "nai-banlist.txt.prev" in command:
            return "192.0.2.9\n"
        return self.banned

    async def get_script_output(self, script):
        self.scripts.append(script)
        return "[f2b-list] applied\n"


def _fake_node_ssh(monkeypatch):
    from app.services import f2b_list as service

    _FakeF2BSSH.commands = []
    _FakeF2BSSH.scripts = []
    monkeypatch.setattr(service, "SSHSession", _FakeF2BSSH)
    return _FakeF2BSSH


def test_node_collect_parses_banned_output(monkeypatch):
    fake = _fake_node_ssh(monkeypatch)
    response = client.post(
        "/api/f2b-list/node/collect", headers=_auth(), json={
            "ip": "203.0.113.20", "ssh_user": "root", "ssh_password": "pw",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"ips": ["198.51.100.8", "2001:db8::8"]}
    assert any("fail2ban-client get sshd banned" in command for command in fake.commands)


def test_node_push_applies_central_list(monkeypatch):
    fake = _fake_node_ssh(monkeypatch)
    auth = _auth()
    assert client.put("/api/f2b-list", headers=auth, json={
        "entries": ["198.51.100.8", "203.0.113.9"],
    }).status_code == 200

    response = client.post(
        "/api/f2b-list/node/push", headers=auth, json={
            "ip": "203.0.113.20", "ssh_user": "root", "ssh_password": "pw",
        },
    )
    assert response.status_code == 200
    assert response.json()["applied"] == 1
    assert response.json()["unbanned"] == 1
    assert any("198.51.100.8" in script for script in fake.scripts)


def test_nodes_sync_merges_collected_addresses_before_push(monkeypatch):
    fake = _fake_node_ssh(monkeypatch)
    auth = _auth()
    response = client.post(
        "/api/f2b-list/nodes/sync", headers=auth, json={
            "nodes": [{
                "ip": "203.0.113.20", "ssh_user": "root", "ssh_password": "pw",
            }],
            "merge_collected": True,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["central_count"] == 2
    assert payload["results"] == [{
        "ip": "203.0.113.20", "ok": True, "collected": 2, "applied": 0,
    }]
    assert any("198.51.100.8" in script and "2001:db8::8" in script for script in fake.scripts)


def test_nodes_sync_pushes_final_union_to_every_node(monkeypatch):
    from app.services import f2b_list as service

    pushed = []

    async def collect(node, _jails):
        return ["198.51.100.1" if node.ip.endswith("1") else "198.51.100.2"]

    async def apply(node, entries, _jails):
        pushed.append((node.ip, list(entries)))
        return {"applied": len(entries), "unbanned": 0, "skipped": 0}

    monkeypatch.setattr(service, "collect_node", collect)
    monkeypatch.setattr(service, "apply_node", apply)
    response = client.post("/api/f2b-list/nodes/sync", headers=_auth(), json={
        "nodes": [
            {"ip": "203.0.113.1", "ssh_user": "root", "ssh_password": "pw"},
            {"ip": "203.0.113.2", "ssh_user": "root", "ssh_password": "pw"},
        ],
        "merge_collected": True,
    })
    assert response.status_code == 200
    assert pushed == [
        ("203.0.113.1", ["198.51.100.1", "198.51.100.2"]),
        ("203.0.113.2", ["198.51.100.1", "198.51.100.2"]),
    ]


def test_node_request_rejects_invalid_ip_and_jail():
    auth = _auth()
    response = client.post(
        "/api/f2b-list/node/collect", headers=auth, json={
            "ip": "not-an-ip", "ssh_user": "root", "ssh_password": "pw",
        },
    )
    assert response.status_code == 422
    response = client.post(
        "/api/f2b-list/node/collect", headers=auth, json={
            "ip": "203.0.113.20", "ssh_user": "root", "ssh_password": "pw",
            "jails": ["sshd; id"],
        },
    )
    assert response.status_code == 422
