"""Wave-4 Plan F — Netbird generators, setup-key payload, PAT vault, routes."""
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.services import accounts, netbird, storage

client = TestClient(app)


def _register():
    r = client.post("/api/auth/register",
                    json={"login": f"nb-{uuid.uuid4().hex[:8]}", "password": "pw"})
    tok = r.json()["token"]
    return {"Authorization": f"Bearer {tok}"}, accounts.account_id_from_token(tok)


def test_setup_key_payload():
    p = netbird.setup_key_payload("mykey")
    assert p["type"] == "reusable" and p["usage_limit"] == 0
    assert p["expires_in"] == 31536000 and p["name"] == "mykey"


def test_control_plane_script():
    s = netbird.control_plane_deploy_script("nb.example.com", "a@b.com")
    assert "getting-started.sh" in s
    assert 'NETBIRD_DOMAIN="nb.example.com"' in s
    assert 'NETBIRD_LETSENCRYPT_EMAIL="a@b.com"' in s
    assert "__NB_OK__" in s and "__NB_FAIL__" in s


def test_agent_script_no_route_hijack():
    s = netbird.agent_install_script("https://nb.example.com", "SETUPKEY123456")
    assert "--disable-client-routes" in s and "--disable-server-routes" in s
    assert "pkgs.netbird.io/install.sh" in s
    assert "netbird up --setup-key" in s
    assert "netbirdIp" in s and "__NB_AGENT_OK__" in s


def test_parse_peer_ip():
    assert netbird.parse_peer_ip("noise\n__NB_PEER_IP__=100.64.0.5\n__NB_AGENT_OK__") == "100.64.0.5"
    assert netbird.parse_peer_ip("nothing here") is None


def test_pat_vault_encrypts_at_rest():
    _, aid = _register()
    netbird.set_control_plane("nb.example.com", account_id=aid)
    netbird.set_pat("super-secret-pat", account_id=aid)
    raw = storage.load_netbird(aid)
    assert raw["pat_enc"]
    assert "super-secret-pat" not in str(raw)     # encrypted, not plaintext
    assert netbird.get_pat(aid) == "super-secret-pat"
    pub = netbird.public_control_plane(aid)
    assert pub["has_pat"] is True and "pat_enc" not in pub


def test_fernet_roundtrip():
    enc = netbird._encrypt("x-secret")
    assert enc != "x-secret"
    assert netbird._decrypt(enc) == "x-secret"
    assert netbird._decrypt("garbage") is None


def test_routes_validation_and_auth():
    headers, _ = _register()
    # empty control-plane
    assert client.get("/api/netbird/control-plane", headers=headers).json() == {}
    # setup-key without a control plane → 400
    assert client.post("/api/netbird/setup-key", headers=headers, json={}).status_code == 400
    # bad domain on deploy → 422
    assert client.post("/api/netbird/control-plane/deploy", headers=headers, json={
        "ip": "1.2.3.4", "ssh_password": "x", "domain": "not a domain",
    }).status_code == 422
    # ungated → 401
    assert client.get("/api/netbird/control-plane").status_code == 401
