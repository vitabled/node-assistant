"""Wave-5 PR-4 — stop certs deploy, bundle-download, перенос сертификатов."""
import asyncio
import uuid as _uuid

from fastapi.testclient import TestClient

from app.main import app
import app.api.certs as certs

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"up-{_uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ── stop ───────────────────────────────────────────────────────
def test_stop_unknown_task_is_404():
    r = client.post("/api/certs/stop", headers=_auth(), json={"task_id": "nope"})
    assert r.status_code == 404


def test_deploy_registers_cancellable_task(monkeypatch):
    started = {}

    async def fake_deploy(req, task_id):
        started["task_id"] = task_id
        await asyncio.sleep(60)

    monkeypatch.setattr(certs, "_deploy", fake_deploy)
    r = client.post("/api/certs/deploy", headers=_auth(), json={
        "ip": "1.2.3.4", "ssh_port": 22, "ssh_user": "root", "ssh_password": "x",
        "domain": "n.example.com", "cert_provider": "letsencrypt", "email": "a@b.co",
    })
    assert r.status_code == 200
    tid = r.json()["task_id"]
    r2 = client.post("/api/certs/stop", headers=_auth(), json={"task_id": tid})
    assert r2.status_code == 200 and r2.json()["ok"] is True
    assert started.get("task_id") == tid


# ── download bundle ────────────────────────────────────────────
def test_cert_files_include_cert():
    assert "cert" in certs._CERT_FILES
    assert certs._CERT_FILES["cert"][0].endswith(".crt")


# ── transfer ───────────────────────────────────────────────────
def test_transfer_scripts():
    rd = certs._read_bundle_script("n.example.com")
    assert "/etc/ssl/private/n.example.com.key" in rd
    assert "/root/.acme.sh/n.example.com_ecc" in rd
    wr = certs._write_bundle_script("n.example.com", "QkFTRTY0")
    assert "mkdir -p /etc/letsencrypt/live/n.example.com" in wr
    assert "ln -sf /etc/ssl/certs/n.example.com_fullchain.pem" in wr
    assert "chmod 600 /etc/ssl/private/n.example.com.key" in wr


def test_transfer_route_flow(monkeypatch):
    runs = []

    class FakeSSH:
        def __init__(self, ip, *a, **k):
            self.ip = ip

        async def connect(self):
            pass

        async def get_script_output(self, script, timeout=None):
            runs.append((self.ip, "write" if "tar xzf" in script else "read"))
            if "tar xzf" in script:
                return "WROTE=n.example.com"
            return "__OK__QUJD"  # base64 "ABC"

        async def get_output(self, cmd):
            return "RELOADED"

        async def close(self):
            pass

    monkeypatch.setattr(certs, "SSHSession", FakeSSH)
    r = client.post("/api/certs/transfer", headers=_auth(), json={
        "source": {"ip": "1.1.1.1", "ssh_password": "x"},
        "target": {"ip": "2.2.2.2", "ssh_password": "y"},
        "domains": ["n.example.com"],
    })
    assert r.status_code == 200
    tid = r.json()["task_id"]
    for _ in range(100):
        t = certs.task_store.get(tid)
        if t and t.status.value in ("success", "failed"):
            break
        import time
        time.sleep(0.05)
    assert ("1.1.1.1", "read") in runs and ("2.2.2.2", "write") in runs
    t = certs.task_store.get(tid)
    assert t.status.value == "success"


def test_transfer_validates_domain():
    r = client.post("/api/certs/transfer", headers=_auth(), json={
        "source": {"ip": "1.1.1.1", "ssh_password": "x"},
        "target": {"ip": "2.2.2.2", "ssh_password": "y"},
        "domains": ["bad domain!!"],
    })
    assert r.status_code == 422
