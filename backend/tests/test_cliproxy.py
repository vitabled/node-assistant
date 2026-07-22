"""Wave-7 Plan F — self-hosted CLIProxyAPI gateway with OAuth provider accounts.

Two properties carry real cost if broken, so they get the loudest tests:
  • an EMPTY `api-keys` list makes the proxy an OPEN relay — the config must be
    seeded into the volume before the container can ever start;
  • FIVE failed Management auths ban the source IP for 30 minutes, so a 401 must
    raise and never be retried.
"""
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import cliproxy_management as mgmt
from app.services import cliproxy_server as srv

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"cp-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ── config rendering: the open-proxy failure mode ─────────────
def test_config_always_carries_a_client_key():
    yaml = srv.render_config("secret-key-value")
    assert "api-keys:" in yaml
    body = yaml.split("api-keys:")[1]
    assert "secret-key-value" in body


def test_empty_master_key_is_refused_outright():
    """An empty api-keys list registers no auth provider and every request
    passes — better to fail loudly than to start an open relay."""
    with pytest.raises(srv.CliProxyError):
        srv.render_config("")


def test_key_is_quoted_so_yaml_cannot_be_reinterpreted():
    yaml = srv.render_config('a:b#c"d')
    assert '"a:b#c\\"d"' in yaml


def test_management_secret_key_stays_empty_in_the_file():
    """A non-empty secret-key is bcrypt-hashed and written BACK into the volume
    at startup; MANAGEMENT_PASSWORD does the same job without mutating it."""
    assert 'secret-key: ""' in srv.render_config("k")


def test_auth_dir_is_inside_the_mounted_volume():
    # OAuth tokens must survive a restart.
    assert 'auth-dir: "/conf/auths"' in srv.render_config("k")


# ── docker argv ───────────────────────────────────────────────
def test_seed_writes_config_from_stdin_not_argv():
    argv = srv.seed_config_argv("supersecret")
    assert "supersecret" not in " ".join(argv)   # never in `docker inspect`
    assert "cat > /conf/config.yaml" in argv[-1]
    assert f"{srv.VOLUME_NAME}:/conf" in argv


def test_seed_refuses_an_empty_key():
    with pytest.raises(srv.CliProxyError):
        srv.seed_config_argv("")


def test_run_argv_mounts_the_directory_not_the_file():
    """Bind-mounting the config FILE makes Docker pre-create it as a directory."""
    argv = srv.run_argv(srv.DEFAULT_IMAGE, "pw")
    assert f"{srv.VOLUME_NAME}:/conf" in argv
    assert "--config" in argv and "/conf/config.yaml" in argv


def test_run_argv_does_not_publish_a_port():
    """The gateway is reachable only on our own docker network."""
    assert "-p" not in srv.run_argv(srv.DEFAULT_IMAGE, "pw")


def test_run_argv_sets_management_password_env():
    argv = srv.run_argv(srv.DEFAULT_IMAGE, "pw")
    assert "MANAGEMENT_PASSWORD=pw" in argv


def test_run_argv_rejects_an_option_like_image():
    with pytest.raises(srv.CliProxyError):
        srv.run_argv("--privileged", "pw")


def test_default_image_is_pinned_and_from_docker_hub():
    assert srv.DEFAULT_IMAGE.startswith("eceasy/cli-proxy-api:")
    assert ":latest" not in srv.DEFAULT_IMAGE
    assert "ghcr.io" not in srv.DEFAULT_IMAGE


# ── management client: the IP-ban rule ────────────────────────
@pytest.mark.anyio
async def test_401_raises_and_issues_exactly_one_request(monkeypatch):
    calls = {"n": 0}

    async def fake_request(self, method, url, **kw):
        calls["n"] += 1
        return httpx.Response(401, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    c = mgmt.ManagementClient("http://gw:8317", "bad")
    with pytest.raises(mgmt.ManagementAuthError):
        await c.list_auth_files()
    assert calls["n"] == 1


@pytest.mark.anyio
@pytest.mark.parametrize("code", [403, 404])
async def test_403_and_404_mean_management_disabled(monkeypatch, code):
    async def fake_request(self, method, url, **kw):
        return httpx.Response(code, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    c = mgmt.ManagementClient("http://gw:8317", "k")
    with pytest.raises(mgmt.ManagementDisabled):
        await c.list_auth_files()


def test_client_refuses_an_empty_key():
    with pytest.raises(mgmt.ManagementError):
        mgmt.ManagementClient("http://gw:8317", "")


def test_config_endpoint_is_not_wrapped():
    """GET /config returns every plaintext key; an endpoint that does not exist
    cannot be piped to a browser by accident."""
    assert not hasattr(mgmt.ManagementClient, "get_config")


# ── OAuth provider mapping ────────────────────────────────────
@pytest.mark.anyio
@pytest.mark.parametrize("provider,endpoint", [
    ("claude", "/anthropic-auth-url"),
    ("anthropic", "/anthropic-auth-url"),
    ("openai", "/codex-auth-url"),
    ("grok", "/xai-auth-url"),
    ("kimi", "/kimi-auth-url"),
])
async def test_oauth_endpoint_mapping(monkeypatch, provider, endpoint):
    seen = {}

    async def fake_request(self, method, url, **kw):
        seen["url"] = url
        return httpx.Response(200, json={"url": "https://x", "state": "s"},
                              request=httpx.Request(method, url))

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    await mgmt.ManagementClient("http://gw:8317", "k").start_oauth(provider)
    assert seen["url"].endswith(endpoint)


@pytest.mark.anyio
async def test_gemini_gets_its_own_explanation():
    """Gemini has no OAuth login at all — a generic 'unknown provider' would send
    the operator looking for a button that cannot exist."""
    c = mgmt.ManagementClient("http://gw:8317", "k")
    with pytest.raises(mgmt.ManagementError) as e:
        await c.start_oauth("gemini")
    assert "Antigravity" in str(e.value)


@pytest.mark.anyio
async def test_unknown_provider_is_rejected_before_the_network(monkeypatch):
    async def boom(self, method, url, **kw):
        raise AssertionError("must not reach the network")

    monkeypatch.setattr(httpx.AsyncClient, "request", boom)
    with pytest.raises(mgmt.ManagementError):
        await mgmt.ManagementClient("http://gw:8317", "k").start_oauth("nope")


def test_scrub_drops_internal_fields():
    entry = {"name": "a.json", "provider": "claude", "email": "x@y.z",
             "status": "ok", "path": "/conf/auths/a.json", "token": "SECRET"}
    out = mgmt.scrub_auth_file(entry)
    assert out["label"] == "x@y.z"
    assert "path" not in out and "token" not in out


# ── routes ────────────────────────────────────────────────────
def test_routes_require_auth():
    assert client.get("/api/cliproxy/config").status_code == 401
    assert client.get("/api/cliproxy/status").status_code == 401


def test_status_reports_disabled_by_default():
    h = _auth()
    r = client.get("/api/cliproxy/status", headers=h)
    assert r.status_code == 200
    d = r.json()
    assert d["enabled"] is False and d["has_keys"] is False
    # no owner recorded yet → anyone may configure it
    assert d["owner_is_me"] is True


def test_management_key_is_never_returned():
    h = _auth()
    body = client.get("/api/cliproxy/config", headers=h).json()
    assert "mgmt" not in str(body).lower() or "cliproxy_mgmt_key" not in str(body)
    assert "management_key" not in body


def test_accounts_without_a_configured_gateway_is_400():
    h = _auth()
    assert client.get("/api/cliproxy/accounts", headers=h).status_code == 400


def test_oauth_callback_needs_a_code_or_redirect_url():
    h = _auth()
    r = client.post("/api/cliproxy/oauth/callback", headers=h, json={"state": "s"})
    assert r.status_code in (400, 422)


@pytest.fixture
def anyio_backend():
    return "asyncio"
