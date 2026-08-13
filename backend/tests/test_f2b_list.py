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
