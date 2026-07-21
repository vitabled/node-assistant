"""Wave-4 Plan E — domain-replace script generators + FQDN validation."""
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.services import replace_domain as rd

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"rd-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_is_fqdn():
    assert rd.is_fqdn("node1.example.com")
    assert not rd.is_fqdn("bad domain")
    assert not rd.is_fqdn("evil.com; rm -rf /")
    assert not rd.is_fqdn("node.example.com\n")  # trailing newline rejected


def test_node_script_replaces_and_preserves_nginx_vars():
    s = rd.node_replace_script("old.example.com", "new.example.com")
    # both domains present, escaped-dot sed, cert bridge + restart + status markers
    assert "new.example.com" in s and "old.example.com" in s
    assert r"sed 's/[.]/\./g'" in s          # dots escaped in the OLD pattern
    assert "letsencrypt/live" in s
    assert "__NODE_OK__" in s and "__NODE_FAIL__" in s
    # native nginx vars must NOT be touched by our generator
    assert "$http_upgrade" not in s
    # docker Go-template survived (not eaten as a format brace)
    assert "{{.Names}}" in s


def test_node_script_autodetect_when_old_empty():
    s = rd.node_replace_script("", "new.example.com")
    assert "detected old domain" in s
    assert "__NO_OLD_DOMAIN__" in s  # guards empty/equal


def test_panel_script_both_pairs():
    s = rd.panel_replace_script("p.old.com", "p.new.com", "s.old.com", "s.new.com")
    assert "p.old.com" in s and "p.new.com" in s
    assert "s.old.com" in s and "s.new.com" in s
    assert ".env" in s and "Caddyfile" in s
    assert "__PANEL_OK__" in s and "__PANEL_FAIL__" in s


def test_node_route_rejects_bad_domain():
    a = _auth()
    r = client.post("/api/replace-domain/node", headers=a, json={
        "ip": "1.2.3.4", "ssh_password": "x", "new_domain": "evil.com; rm -rf /",
    })
    assert r.status_code == 422


def test_node_route_rejects_bad_provider():
    a = _auth()
    r = client.post("/api/replace-domain/node", headers=a, json={
        "ip": "1.2.3.4", "ssh_password": "x", "new_domain": "n.example.com",
        "cert_provider": "bogus",
    })
    assert r.status_code == 422


def test_requires_auth():
    assert client.post("/api/replace-domain/node", json={
        "ip": "1.2.3.4", "ssh_password": "x", "new_domain": "n.example.com"}).status_code == 401
