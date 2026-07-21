"""Wave-5 Plan H — per-account API access tokens: store/resolve + require_account
bearer acceptance + readonly enforcement + isolation + MCP managed-token rotation."""
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.services import api_tokens

client = TestClient(app)


def _register():
    login = f"tok-{uuid.uuid4().hex[:8]}"
    r = client.post("/api/auth/register", json={"login": login, "password": "pw"})
    assert r.status_code == 201, r.text
    body = r.json()
    return body["id"], {"Authorization": f"Bearer {body['token']}"}


# ── service layer ────────────────────────────────────────────────
def test_create_hides_secret_and_resolves():
    aid, _ = _register()
    masked, token = api_tokens.create("ci", account_id=aid)
    assert token.startswith("nai_") and aid in token
    assert "hash" not in masked and "token" not in masked
    resolved = api_tokens.resolve(token)
    assert resolved is not None
    assert resolved.account_id == aid and resolved.readonly is False


def test_resolve_rejects_bad_expired_and_foreign():
    aid, _ = _register()
    _m, token = api_tokens.create("t", account_id=aid)
    # tampered secret
    assert api_tokens.resolve(f"nai_{aid}_wrongsecret") is None
    # non-prefixed / malformed
    assert api_tokens.resolve("bearer-jwt-lookalike") is None
    assert api_tokens.resolve("nai_no-underscore-body") is None
    # unknown account id embedded
    assert api_tokens.resolve(f"nai_{uuid.uuid4()}_abc") is None
    # expired
    _m2, expired = api_tokens.create("e", expires_in=-10, account_id=aid)
    assert api_tokens.resolve(expired) is None
    # a good one still works
    assert api_tokens.resolve(token).account_id == aid


def test_list_masks_and_revoke():
    aid, _ = _register()
    _m, token = api_tokens.create("x", account_id=aid)
    listed = api_tokens.list_tokens(aid)
    assert len(listed) == 1 and all("hash" not in t for t in listed)
    tid = listed[0]["id"]
    assert api_tokens.revoke(tid, account_id=aid) is True
    assert api_tokens.resolve(token) is None          # gone after revoke
    assert api_tokens.revoke("nonexistent", account_id=aid) is False


def test_mint_managed_rotates():
    aid, _ = _register()
    old = api_tokens.mint_managed("mcp-container", account_id=aid)
    new = api_tokens.mint_managed("mcp-container", account_id=aid)
    assert old != new
    assert api_tokens.resolve(old) is None            # previous rotated out
    r = api_tokens.resolve(new)
    assert r is not None and r.readonly is True        # managed = readonly
    assert len(api_tokens.list_tokens(aid)) == 1       # only one managed token


# ── HTTP layer (require_account accepts API tokens) ──────────────
def test_require_account_accepts_api_token_and_isolates():
    aid_a, auth_a = _register()
    # create a token via the API (owner uses their JWT)
    r = client.post("/api/api-tokens", json={"name": "api"}, headers=auth_a)
    assert r.status_code == 201, r.text
    token = r.json()["token"]
    assert token.startswith("nai_")

    # the API token itself authenticates a GET → sees account A's tokens
    api_auth = {"Authorization": f"Bearer {token}"}
    r = client.get("/api/api-tokens", headers=api_auth)
    assert r.status_code == 200 and len(r.json()) == 1

    # account B never sees A's tokens
    _aid_b, auth_b = _register()
    r = client.get("/api/api-tokens", headers=auth_b)
    assert r.status_code == 200 and r.json() == []


def test_readonly_token_blocks_mutations():
    _aid, auth = _register()
    r = client.post("/api/api-tokens", json={"name": "ro", "readonly": True}, headers=auth)
    ro = {"Authorization": f"Bearer {r.json()['token']}"}
    # GET allowed
    assert client.get("/api/api-tokens", headers=ro).status_code == 200
    # mutating method blocked
    r = client.post("/api/api-tokens", json={"name": "nope"}, headers=ro)
    assert r.status_code == 403


def test_revoked_token_is_unauthorized():
    _aid, auth = _register()
    r = client.post("/api/api-tokens", json={"name": "temp"}, headers=auth)
    body = r.json()
    tok = {"Authorization": f"Bearer {body['token']}"}
    assert client.get("/api/api-tokens", headers=tok).status_code == 200
    assert client.delete(f"/api/api-tokens/{body['id']}", headers=auth).status_code == 200
    assert client.get("/api/api-tokens", headers=tok).status_code == 401
