"""Wave-4 Plan D — Certwarden script generators + route validation."""
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.services import certwarden as cw

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"cw-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_server_compose_and_deploy():
    comp = cw.server_compose()
    assert cw.SERVER_IMAGE in comp
    for port in ("4050", "4055", "4060"):
        assert port in comp
    s = cw.server_deploy_script()
    assert "docker compose up -d" in s
    assert "__CW_OK__" in s and "__CW_FAIL__" in s
    assert "{{.Names}}" in s  # docker template survived f-string-free build


def test_client_script_download_api_and_restart():
    s = cw.client_install_script(
        "https://cw.example.com", "node1.example.com",
        "cert-node1", "key-node1", "CERTKEY123", "PRIVKEY456",
        restart_containers=["remnanode", "remnawave-nginx"],
    )
    # download API path templates (values injected via shell vars at runtime)
    assert "/certwarden/api/v1/download/certificates/${CERT_NAME}" in s
    assert "/certwarden/api/v1/download/privatekeys/${KEY_NAME}" in s
    assert 'CERT_NAME="cert-node1"' in s and 'KEY_NAME="key-node1"' in s
    assert 'CERT_KEY="CERTKEY123"' in s and 'KEY_KEY="PRIVKEY456"' in s
    assert 'DOMAIN="node1.example.com"' in s
    assert "/etc/ssl/certs/${DOMAIN}_fullchain.pem" in s
    assert "docker restart" in s and "remnanode" in s
    assert "crontab" in s and "__CW_CLIENT_OK__" in s


def test_client_script_defaults_restart():
    s = cw.client_install_script("https://cw.x", "n.example.com", "c", "k", "a", "b")
    assert "remnanode" in s and "remnawave-nginx" in s


def test_server_registry_endpoints():
    a = _auth()
    assert client.get("/api/certwarden/server", headers=a).json() == {}
    assert client.delete("/api/certwarden/server", headers=a).json() == {"ok": True}


def test_deploy_validation():
    a = _auth()
    # bad placement
    assert client.post("/api/certwarden/server/deploy", headers=a, json={
        "ip": "1.2.3.4", "ssh_password": "x", "placement": "bogus", "server_url": "https://cw.x",
    }).status_code == 422
    # bad url
    assert client.post("/api/certwarden/server/deploy", headers=a, json={
        "ip": "1.2.3.4", "ssh_password": "x", "placement": "panel", "server_url": "ftp://cw.x",
    }).status_code == 422


def test_client_install_rejects_bad_apikey():
    a = _auth()
    r = client.post("/api/certwarden/client/install", headers=a, json={
        "ip": "1.2.3.4", "ssh_password": "x", "server_url": "https://cw.x",
        "domain": "n.example.com", "cert_name": "c", "key_name": "k",
        "cert_apikey": "bad key; rm -rf /", "key_apikey": "ok",
    })
    assert r.status_code == 422


def test_requires_auth():
    assert client.get("/api/certwarden/server").status_code == 401
