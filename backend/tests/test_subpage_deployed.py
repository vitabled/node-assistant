"""Wave-4 PR-10 — редактор развёрнутой подписочной страницы."""
import base64
import uuid as _uuid

from fastapi.testclient import TestClient

from app.main import app
import app.api.subpages as sp

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"dp-{_uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


CREDS = {"ip": "1.2.3.4", "ssh_port": 22, "ssh_user": "root", "ssh_password": "x"}


# ── парсер inspect ─────────────────────────────────────────────
def test_parse_inspect_dir_mode():
    out = "MODE=dir\nMOUNT=/opt/remnawave-subpage/frontend\nindex.html|1234\nassets/app.css|567\n"
    d = sp._parse_inspect(out)
    assert d["mode"] == "dir"
    assert d["mount"] == "/opt/remnawave-subpage/frontend"
    assert d["files"] == [{"path": "index.html", "size": 1234},
                          {"path": "assets/app.css", "size": 567}]


def test_parse_inspect_file_and_builtin():
    d = sp._parse_inspect("MODE=file\nMOUNT=/opt/remnawave-subpage/index.html\nFILE=index.html|999\n")
    assert d["mode"] == "file" and d["files"] == [{"path": "index.html", "size": 999}]
    d2 = sp._parse_inspect("MODE=builtin\n")
    assert d2["mode"] == "builtin" and d2["files"] == []


def test_rel_ok():
    assert sp._rel_ok("index.html")
    assert sp._rel_ok("assets/app.css")
    assert not sp._rel_ok("../etc/passwd")
    assert not sp._rel_ok("/etc/passwd")
    assert not sp._rel_ok("a/../../b")
    assert not sp._rel_ok("")


# ── эндпоинты с фейковым SSH ───────────────────────────────────
class FakeSSH:
    out = ""
    scripts = []

    def __init__(self, *a, **k):
        pass

    async def connect(self):
        pass

    async def get_script_output(self, script, timeout=None):
        FakeSSH.scripts.append(script)
        return FakeSSH.out

    async def close(self):
        pass


def test_inspect_route(monkeypatch):
    FakeSSH.out = "MODE=dir\nMOUNT=/opt/x/frontend\nindex.html|10\n"
    FakeSSH.scripts = []
    monkeypatch.setattr(sp, "SSHSession", FakeSSH)
    r = client.post("/api/subpages/deployed/inspect", headers=_auth(), json=CREDS)
    assert r.status_code == 200
    assert r.json()["mode"] == "dir"
    assert "docker inspect" in FakeSSH.scripts[0]


def test_read_route_returns_content(monkeypatch):
    html = "<html>привет</html>"
    FakeSSH.out = base64.b64encode(html.encode()).decode()
    monkeypatch.setattr(sp, "SSHSession", FakeSSH)
    r = client.post("/api/subpages/deployed/read", headers=_auth(),
                    json={**CREDS, "path": "index.html"})
    assert r.status_code == 200
    assert r.json()["content"] == html


def test_read_route_404_on_err_marker(monkeypatch):
    FakeSSH.out = "__NAI_ERR__файл не найден"
    monkeypatch.setattr(sp, "SSHSession", FakeSSH)
    r = client.post("/api/subpages/deployed/read", headers=_auth(),
                    json={**CREDS, "path": "missing.css"})
    assert r.status_code == 404


def test_read_route_rejects_traversal():
    r = client.post("/api/subpages/deployed/read", headers=_auth(),
                    json={**CREDS, "path": "../.env"})
    assert r.status_code == 422


def test_write_route_atomic_and_restart(monkeypatch):
    FakeSSH.out = "WROTE=/opt/x/frontend/index.html\nRESTARTED=1\n"
    FakeSSH.scripts = []
    monkeypatch.setattr(sp, "SSHSession", FakeSSH)
    r = client.post("/api/subpages/deployed/write", headers=_auth(),
                    json={**CREDS, "path": "index.html", "content": "<html>x</html>", "restart": True})
    assert r.status_code == 200
    assert r.json()["ok"] is True and r.json()["restarted"] is True
    script = FakeSSH.scripts[0]
    assert ".nai-backup/" in script and "mv \"$TMP\" \"$DST\"" in script
    assert "docker restart remnawave-subscription-page" in script
    assert base64.b64encode("<html>x</html>".encode()).decode() in script


def test_write_route_without_restart(monkeypatch):
    FakeSSH.out = "WROTE=/opt/x/frontend/index.html\nRESTARTED=skipped\n"
    FakeSSH.scripts = []
    monkeypatch.setattr(sp, "SSHSession", FakeSSH)
    r = client.post("/api/subpages/deployed/write", headers=_auth(),
                    json={**CREDS, "path": "index.html", "content": "x", "restart": False})
    assert r.json()["restarted"] is False
    assert "docker restart remnawave-subscription-page" not in FakeSSH.scripts[0]
