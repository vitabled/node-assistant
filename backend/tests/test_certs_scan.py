"""Wave-4 PR-3 — автоскан доменов сервера по SSH (парсер + endpoint)."""
import uuid

from fastapi.testclient import TestClient

from app.main import app
import app.api.certs as certs

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"scan-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


SAMPLE = """
== nginx ==
server_name node1.example.com www.node1.example.com;
server_name _;
server_name sub.example.com, panel.example.com
== certbot ==
node1.example.com
panel.example.com-0001
== xray ==
"dest": "dl.google.com:443"
"serverName": "yahoo.com"
"dest": "cdn.example.com:8443,static.example.com:8443"
== env ==
FAKE_SITE=https://mask.example.com/path
SERVER_NAME=mask2.example.com
"""


def test_parse_scan_collects_all_sources():
    got = certs._parse_scan(SAMPLE)
    by_domain = {d["domain"]: d["sources"] for d in got}
    assert by_domain["node1.example.com"] == ["certbot", "nginx"]
    assert by_domain["panel.example.com"] == ["certbot", "nginx"]  # -0001 слит
    assert by_domain["dl.google.com"] == ["xray"]                  # :443 срезан
    assert by_domain["cdn.example.com"] == ["xray"]                # список через запятую
    assert by_domain["mask.example.com"] == ["env"]                # https:// и путь срезаны
    assert by_domain["mask2.example.com"] == ["env"]
    assert "_" not in by_domain                                    # заглушки выкинуты


def test_parse_scan_skips_wildcards_and_junk():
    got = certs._parse_scan("== nginx ==\nserver_name *.example.com localhost _;\n")
    assert got == []


def test_scan_endpoint(monkeypatch):
    class FakeSSH:
        def __init__(self, ip, port, user, password):
            pass

        async def connect(self):
            pass

        async def get_script_output(self, script, timeout=None):
            assert "server_name" in script
            return SAMPLE

        async def close(self):
            pass

    monkeypatch.setattr(certs, "SSHSession", FakeSSH)
    r = client.post("/api/certs/scan-domains", headers=_auth(), json={
        "ip": "1.2.3.4", "ssh_port": 22, "ssh_user": "root", "ssh_password": "x",
    })
    assert r.status_code == 200
    domains = {d["domain"] for d in r.json()["domains"]}
    assert "node1.example.com" in domains and "mask.example.com" in domains


def test_scan_endpoint_ssh_failure_is_502(monkeypatch):
    class FakeSSH:
        def __init__(self, *a):
            pass

        async def connect(self):
            raise OSError("timed out")

        async def close(self):
            pass

    monkeypatch.setattr(certs, "SSHSession", FakeSSH)
    r = client.post("/api/certs/scan-domains", headers=_auth(), json={
        "ip": "1.2.3.4", "ssh_port": 22, "ssh_user": "root", "ssh_password": "x",
    })
    assert r.status_code == 502 and "Сканирование не удалось" in r.json()["detail"]
