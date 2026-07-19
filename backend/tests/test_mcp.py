"""Tests for the MCP config/status API (api/mcp.py) + token vault (mcp_server).

Docker orchestration itself is not exercised (no daemon in CI) — start() fails
soft to a warning, which is asserted. The token generation, encryption-at-rest,
and config persistence ARE covered.
"""

import asyncio
import uuid

from fastapi.testclient import TestClient

from app.services import accounts, mcp_server, storage
from app.main import app

client = TestClient(app)


def _auth():
    login = f"mc-{uuid.uuid4().hex[:8]}"
    r = client.post("/api/auth/register", json={"login": login, "password": "pw-1"})
    body = r.json()
    return {"Authorization": f"Bearer {body['token']}"}, body["id"]


def test_mcp_routes_require_account():
    assert client.get("/api/mcp/config").status_code == 401
    assert client.post("/api/mcp/config", json={"enabled": True}).status_code == 401
    assert client.get("/api/mcp/status").status_code == 401


def test_default_config_is_disabled_no_token():
    h, _ = _auth()
    cfg = client.get("/api/mcp/config", headers=h).json()
    assert cfg["enabled"] is False
    assert cfg["auth_token"] is None  # no token until enabled
    assert cfg["remnawave_ready"] is False  # fresh account has no panel creds
    assert cfg["readonly"] is True


def test_enable_generates_stable_token_encrypted_at_rest():
    h, aid = _auth()
    r = client.post(
        "/api/mcp/config", headers=h, json={"enabled": True, "readonly": True}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    # Remnawave not configured → start() fails soft to a warning (not a 500).
    assert "warning" in body
    token = body["auth_token"]
    assert token and len(token) > 20

    # Stable across reads.
    again = client.get("/api/mcp/config", headers=h).json()
    assert again["auth_token"] == token

    # Encrypted at rest: settings.json holds the ciphertext, never the plaintext.
    raw = (accounts.data_dir(aid) / "settings.json").read_text(encoding="utf-8")
    assert token not in raw
    assert "auth_token_enc" in raw
    # And the vault decrypts back to the same token.
    assert mcp_server.ensure_auth_token(aid) == token


def test_status_reports_container_state():
    h, _ = _auth()
    client.post("/api/mcp/config", headers=h, json={"enabled": True})
    st = client.get("/api/mcp/status", headers=h).json()
    assert st["enabled"] is True
    # No docker daemon in the test env → 'no-docker' or 'absent'; never crashes.
    assert st["container"] in ("no-docker", "absent", "stopped", "running")
    assert st["reachable"] is False


def test_disable_persists_and_hides_token():
    h, _ = _auth()
    client.post("/api/mcp/config", headers=h, json={"enabled": True})
    r = client.post("/api/mcp/config", headers=h, json={"enabled": False})
    assert r.json()["enabled"] is False
    got = client.get("/api/mcp/config", headers=h).json()
    assert got["enabled"] is False
    assert got["auth_token"] is None  # not surfaced while disabled


def test_port_validation():
    h, _ = _auth()
    assert (
        client.post(
            "/api/mcp/config", headers=h, json={"enabled": False, "http_port": 70000}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/mcp/config", headers=h, json={"enabled": False, "http_port": 0}
        ).status_code
        == 422
    )


def test_get_config_does_not_generate_token(monkeypatch):
    # A GET must be side-effect-free: no token minted just by reading config.
    h, aid = _auth()
    calls = {"n": 0}
    real = mcp_server.encrypt_new_token
    monkeypatch.setattr(
        mcp_server,
        "encrypt_new_token",
        lambda: (calls.__setitem__("n", calls["n"] + 1), real())[1],
    )
    # enabled=False → token stays null and none generated.
    for _ in range(3):
        assert client.get("/api/mcp/config", headers=h).json()["auth_token"] is None
    assert calls["n"] == 0


def test_start_uses_env_file_not_argv_for_secrets(monkeypatch):
    h, aid = _auth()
    # Configure Remnawave so start() passes the creds check.
    data = storage.load_settings(aid)
    data["remnawave"] = {"panel_url": "https://p.example", "api_token": "SEKRET-TOK"}
    storage.save_settings(data, aid)

    calls = []

    async def fake_docker(*args, timeout=60):
        calls.append(tuple(args))
        if args and args[0] == "version":
            return (0, "27.0")
        if args and args[0] == "run":
            return (0, "container-id")
        return (0, "")

    monkeypatch.setattr(mcp_server, "_docker", fake_docker)
    try:
        asyncio.run(mcp_server.start(aid))
        run_call = next(a for a in calls if a and a[0] == "run")
        joined = " ".join(run_call)
        # Secrets go via --env-file, NOT argv `-e KEY=VALUE`.
        assert "--env-file" in run_call
        assert "SEKRET-TOK" not in joined
        assert "REMNAWAVE_API_TOKEN=SEKRET-TOK" not in joined
        # Non-secret vars stay inline.
        assert any("NODE_ASSISTANT_BASE_URL=" in a for a in run_call)
        # Ownership recorded.
        assert mcp_server._get_owner()["account_id"] == aid
    finally:
        try:
            mcp_server._OWNER_FILE.unlink()
        except OSError:
            pass


def test_status_reports_foreign_when_another_account_owns(monkeypatch):
    _, aid_a = _auth()
    _, aid_b = _auth()
    mcp_server._set_owner(aid_a, 3100)

    async def running():
        return "running"

    async def yes(cfg=None):
        return True

    monkeypatch.setattr(mcp_server, "container_state", running)
    monkeypatch.setattr(mcp_server, "reachable", yes)
    try:
        sa = asyncio.run(mcp_server.status(aid_a))
        sb = asyncio.run(mcp_server.status(aid_b))
        assert sa["container"] == "running"
        assert sb["container"] == "foreign"  # honest: not this account's container
        assert sb["reachable"] is False
    finally:
        try:
            mcp_server._OWNER_FILE.unlink()
        except OSError:
            pass
