"""Ф10 — SSL management: DeployCertRequest validation, the «already has cert»
probe/skip flow, and the manual-domains CRUD store."""
import sys
import types
import uuid

sys.modules.setdefault("asyncssh", types.ModuleType("asyncssh"))

import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

from app.main import app
from app.models.deploy import DeployCertRequest
import app.api.certs as certsapi

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"ssl-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ── model validation ──────────────────────────────────────────

def test_cloudflare_requires_token_email_optional():
    DeployCertRequest(ip="1.2.3.4", ssh_password="pw", domain="n.example.com",
                      cert_provider="cloudflare", cf_api_key="cf")
    with pytest.raises(ValidationError):
        DeployCertRequest(ip="1.2.3.4", ssh_password="pw", domain="n.example.com",
                          cert_provider="cloudflare", cf_api_key="")


def test_letsencrypt_zerossl_require_email():
    for prov in ("letsencrypt", "zerossl"):
        DeployCertRequest(ip="1.2.3.4", ssh_password="pw", domain="n.example.com",
                          cert_provider=prov, email="a@b.co")
        with pytest.raises(ValidationError):
            DeployCertRequest(ip="1.2.3.4", ssh_password="pw", domain="n.example.com",
                              cert_provider=prov, email="")


@pytest.mark.parametrize("bad", ['n.ex.com";reboot', "x$(id).com", "no spaces here"])
def test_domain_shell_safety(bad):
    with pytest.raises(ValidationError):
        DeployCertRequest(ip="1.2.3.4", ssh_password="pw", domain=bad,
                          cert_provider="letsencrypt", email="a@b.co")


# ── deploy flow: «already has cert» skips unless force ─────────

class _FakeSSH:
    def __init__(self, *a, **k):
        self.scripts = []

    async def connect(self, *a, **k):
        pass

    async def get_output(self, script):
        if "os-release" in script:
            return "Ubuntu"
        if "_fullchain.pem" in script:      # _probe_cert
            return "Jul 15 12:00:00 2027 GMT"   # a cert IS present
        return ""

    async def run_script(self, script, task, **k):
        self.scripts.append(script)

    async def close(self):
        pass


def test_deploy_skips_when_cert_present_and_not_forced(monkeypatch):
    import asyncio
    fake = _FakeSSH()
    monkeypatch.setattr(certsapi, "SSHSession", lambda *a, **k: fake)

    async def _noop_upsert(*a, **k):
        pass
    monkeypatch.setattr(certsapi, "upsert_a_record", _noop_upsert)

    from app.services.task_store import task_store
    task = task_store.create(total_steps=3)
    req = DeployCertRequest(ip="1.2.3.4", ssh_password="pw", domain="n.example.com",
                            cert_provider="letsencrypt", email="a@b.co", force=False)
    asyncio.run(certsapi._deploy(req, task.task_id))
    # cert present + not forced → no issue script ran, task succeeded
    assert fake.scripts == []
    assert any("уже установлен" in ln for ln in task.logs)


def test_deploy_runs_when_forced(monkeypatch):
    import asyncio
    fake = _FakeSSH()
    monkeypatch.setattr(certsapi, "SSHSession", lambda *a, **k: fake)

    async def _noop_upsert(*a, **k):
        pass
    monkeypatch.setattr(certsapi, "upsert_a_record", _noop_upsert)

    from app.services.task_store import task_store
    task = task_store.create(total_steps=3)
    req = DeployCertRequest(ip="1.2.3.4", ssh_password="pw", domain="n.example.com",
                            cert_provider="letsencrypt", email="a@b.co", force=True)
    asyncio.run(certsapi._deploy(req, task.task_id))
    # forced → the acme issue script + the restart script ran
    assert any("acme.sh" in s for s in fake.scripts)


# ── domains CRUD ──────────────────────────────────────────────

def test_domains_crud_and_isolation():
    a, b = _auth(), _auth()
    r = client.post("/api/domains", headers=a, json={"domain": "Node1.Example.com"})
    assert r.status_code == 201
    assert r.json()["domain"] == "node1.example.com"   # normalized lower
    # duplicate → 409
    assert client.post("/api/domains", headers=a, json={"domain": "node1.example.com"}).status_code == 409
    # isolation: b sees none
    assert client.get("/api/domains", headers=a).json()[0]["domain"] == "node1.example.com"
    assert client.get("/api/domains", headers=b).json() == []
    # malformed rejected
    assert client.post("/api/domains", headers=a, json={"domain": "not a domain"}).status_code == 422
    # delete
    did = client.get("/api/domains", headers=a).json()[0]["id"]
    assert client.delete(f"/api/domains/{did}", headers=a).status_code == 204
    assert client.get("/api/domains", headers=a).json() == []


def test_domains_requires_auth():
    assert client.get("/api/domains").status_code == 401
