"""Ф8 (wave1) — Переменные: read / write the panel /opt/remnawave/.env over SSH.

Locks the behaviour of the two /api/panel/env routes with a mocked SSHSession:
  (a) READ parses pairs and MASKS secret keys (SECRET/PASSWORD/TOKEN/KEY/
      DATABASE_URL) while leaving ordinary keys in the clear;
  (b) WRITE merges onto the server's current .env — untouched masked secrets are
      preserved (never sent by the client, never wiped), new pairs added, deleted
      keys removed — then applies via `docker compose up -d`;
  (c) validation: bad key / multiline value → 422; missing .env → 404; SSH
      failure → 502; unauthenticated → 401;
  (d) values go through the SILENT channel only (no Task, nothing logged); the
      preserved secret round-trips server-side but never leaves the response.
"""

import uuid

from fastapi.testclient import TestClient

import app.api.panel_deploy as panel_deploy  # noqa: E402
from app.main import app

client = TestClient(app)


def _auth():
    r = client.post(
        "/api/auth/register",
        json={"login": f"pe-{uuid.uuid4().hex[:8]}", "password": "pw"},
    )
    return {"Authorization": f"Bearer {r.json()['token']}"}


# A fixture .env covering every masking pattern + ordinary keys.
_ENV_FIXTURE = (
    "# comment line ignored\n"
    "POSTGRES_USER=postgres\n"
    "POSTGRES_PASSWORD=supersecret\n"
    "JWT_AUTH_SECRET=deadbeef\n"
    "SOME_TOKEN=t0ken\n"
    "MY_KEY=k0key\n"
    "DATABASE_URL=postgresql://postgres:supersecret@remnawave-db:5432/postgres\n"
    "METRICS_PASS=metr1cssecret\n"
    "\n"
    "FRONT_END_DOMAIN=panel.example.com\n"
    "APP_PORT=3000\n"
)


class EnvSSH:
    """Mock SSHSession dispatching on script content. Class-level capture so tests
    can assert on the exact write/compose scripts pushed through the silent
    channel. `run_script` is intentionally ABSENT — if an endpoint ever tried to
    stream (and thus log) a value, the test would AttributeError."""

    scripts: list = []
    present = True
    compose_ok = True

    def __init__(self, *a, **k):
        pass

    async def connect(self, timeout=30):
        pass

    async def get_script_output(self, script, timeout=None):
        EnvSSH.scripts.append(script)
        if "cat > /opt/remnawave/.env" in script:
            return "__ENV_SAVED__"
        if "head -c" in script:
            return (
                ("__ENV_PRESENT__\n" + _ENV_FIXTURE)
                if EnvSSH.present
                else "__ENV_ABSENT__"
            )
        if "docker compose up" in script:
            body = "Container remnawave-backend Recreated"
            return (
                f"{body}\n__COMPOSE_OK__"
                if EnvSSH.compose_ok
                else f"{body}\n__COMPOSE_FAIL__"
            )
        return ""

    async def close(self):
        pass


def _reset(monkeypatch, *, present=True, compose_ok=True):
    EnvSSH.scripts = []
    EnvSSH.present = present
    EnvSSH.compose_ok = compose_ok
    monkeypatch.setattr(panel_deploy, "SSHSession", EnvSSH)


def _creds(**over):
    base = {"ip": "1.2.3.4", "ssh_password": "pw", "ssh_port": 22}
    base.update(over)
    return base


# ── pure helpers (parser + masking first — TDD) ─────────────────


def test_parse_env_text_drops_comments_and_blanks():
    pairs = dict(panel_deploy._parse_env_text(_ENV_FIXTURE))
    assert pairs["POSTGRES_USER"] == "postgres"
    assert pairs["DATABASE_URL"].startswith("postgresql://")  # later '=' stays in value
    assert "# comment line ignored" not in pairs
    assert "" not in pairs


def test_is_secret_env_key():
    for k in (
        "POSTGRES_PASSWORD",
        "JWT_AUTH_SECRET",
        "SOME_TOKEN",
        "MY_KEY",
        "DATABASE_URL",
        "jwt_auth_secret",  # case-insensitive
        "METRICS_PASS",  # generated basic-auth secret — PASS, not PASSWORD
        "WEBHOOK_SECRET_HEADER",
    ):
        assert panel_deploy._is_secret_env_key(k), k
    for k in ("POSTGRES_USER", "APP_PORT", "FRONT_END_DOMAIN", "METRICS_USER"):
        assert not panel_deploy._is_secret_env_key(k), k


# ── (a) READ ───────────────────────────────────────────────────


def test_read_requires_auth():
    assert client.post("/api/panel/env/read", json=_creds()).status_code == 401


def test_read_parses_and_masks(monkeypatch):
    _reset(monkeypatch)
    r = client.post("/api/panel/env/read", headers=_auth(), json=_creds())
    assert r.status_code == 200
    body = r.json()
    assert body["present"] is True
    pairs = {p["key"]: p for p in body["pairs"]}

    # secrets masked → real value never leaves the server
    for k in (
        "POSTGRES_PASSWORD",
        "JWT_AUTH_SECRET",
        "SOME_TOKEN",
        "MY_KEY",
        "DATABASE_URL",
        "METRICS_PASS",  # PASS (not PASSWORD) must still mask
    ):
        assert pairs[k]["masked"] is True
        assert pairs[k]["value"] == panel_deploy._ENV_MASK
    assert "supersecret" not in r.text and "deadbeef" not in r.text
    assert "metr1cssecret" not in r.text

    # ordinary keys returned in the clear
    assert pairs["FRONT_END_DOMAIN"]["masked"] is False
    assert pairs["FRONT_END_DOMAIN"]["value"] == "panel.example.com"
    assert pairs["APP_PORT"]["value"] == "3000"
    assert pairs["POSTGRES_USER"]["value"] == "postgres"


def test_read_missing_env_404(monkeypatch):
    _reset(monkeypatch, present=False)
    r = client.post("/api/panel/env/read", headers=_auth(), json=_creds())
    assert r.status_code == 404
    assert "не найден" in r.json()["detail"]


def test_read_ssh_failure_502(monkeypatch):
    class BoomSSH:
        def __init__(self, *a, **k):
            pass

        async def connect(self, *a, **k):
            raise OSError("connection refused")

        async def close(self):
            pass

    monkeypatch.setattr(panel_deploy, "SSHSession", BoomSSH)
    r = client.post("/api/panel/env/read", headers=_auth(), json=_creds())
    assert r.status_code == 502
    assert "Не удалось подключиться" in r.json()["detail"]


def test_read_invalid_ip_422():
    r = client.post("/api/panel/env/read", headers=_auth(), json=_creds(ip="evil; rm"))
    assert r.status_code == 422


# ── (b) WRITE — merge semantics ─────────────────────────────────


def _written_env(scripts) -> str:
    """The heredoc body of the write script pushed through the silent channel."""
    for s in scripts:
        if "cat > /opt/remnawave/.env" in s:
            body = s.split("<<'ENV_WRITE_EOF'\n", 1)[1]
            return body.split("ENV_WRITE_EOF", 1)[0]
    return ""


def test_write_requires_auth():
    assert client.post("/api/panel/env/write", json=_creds()).status_code == 401


def test_write_merge_preserves_secret_and_edits(monkeypatch):
    _reset(monkeypatch)
    r = client.post(
        "/api/panel/env/write",
        headers=_auth(),
        json=_creds(
            pairs=[{"key": "FRONT_END_DOMAIN", "value": "new.example.com"}],
            deleted=["APP_PORT"],
        ),
    )
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "ok": True,
        "applied": 1,
        "removed": 1,
        "restarted": True,
        "detail": "",
    }
    env = _written_env(EnvSSH.scripts)
    # untouched secrets preserved (client never sent them, merge kept them)
    assert "POSTGRES_PASSWORD=supersecret" in env
    assert "JWT_AUTH_SECRET=deadbeef" in env
    # the edited non-secret is applied
    assert "FRONT_END_DOMAIN=new.example.com" in env
    assert "FRONT_END_DOMAIN=panel.example.com" not in env
    # the deleted key is gone
    assert "APP_PORT=" not in env
    # compose up was invoked to apply
    assert any("docker compose up" in s for s in EnvSSH.scripts)


def test_write_adds_new_pair(monkeypatch):
    _reset(monkeypatch)
    r = client.post(
        "/api/panel/env/write",
        headers=_auth(),
        json=_creds(pairs=[{"key": "FOO_BAR", "value": "baz"}]),
    )
    assert r.status_code == 200
    assert r.json()["applied"] == 1
    assert "FOO_BAR=baz" in _written_env(EnvSSH.scripts)


def test_write_overwrites_secret_when_explicitly_set(monkeypatch):
    _reset(monkeypatch)
    r = client.post(
        "/api/panel/env/write",
        headers=_auth(),
        json=_creds(pairs=[{"key": "JWT_AUTH_SECRET", "value": "rotated"}]),
    )
    assert r.status_code == 200
    env = _written_env(EnvSSH.scripts)
    assert "JWT_AUTH_SECRET=rotated" in env
    assert "JWT_AUTH_SECRET=deadbeef" not in env


def test_write_empty_value_does_not_wipe_existing_secret(monkeypatch):
    # Defense-in-depth: a blank value for an existing secret key keeps the secret
    # (a masked field left empty must not blank it out server-side).
    _reset(monkeypatch)
    r = client.post(
        "/api/panel/env/write",
        headers=_auth(),
        json=_creds(pairs=[{"key": "JWT_AUTH_SECRET", "value": ""}]),
    )
    assert r.status_code == 200
    env = _written_env(EnvSSH.scripts)
    assert "JWT_AUTH_SECRET=deadbeef" in env  # preserved, not wiped
    assert "JWT_AUTH_SECRET=\n" not in env


def test_write_compose_fail_reports_not_restarted(monkeypatch):
    _reset(monkeypatch, compose_ok=False)
    r = client.post(
        "/api/panel/env/write",
        headers=_auth(),
        json=_creds(pairs=[{"key": "FRONT_END_DOMAIN", "value": "new.example.com"}]),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["restarted"] is False
    assert body["detail"]  # compose output tail surfaced to the user


def test_write_invalid_key_422(monkeypatch):
    _reset(monkeypatch)
    r = client.post(
        "/api/panel/env/write",
        headers=_auth(),
        json=_creds(pairs=[{"key": "bad-key", "value": "x"}]),
    )
    assert r.status_code == 422


def test_write_value_newline_422(monkeypatch):
    _reset(monkeypatch)
    r = client.post(
        "/api/panel/env/write",
        headers=_auth(),
        json=_creds(pairs=[{"key": "GOOD", "value": "a\nb"}]),
    )
    assert r.status_code == 422


def test_write_invalid_deleted_key_422(monkeypatch):
    _reset(monkeypatch)
    r = client.post(
        "/api/panel/env/write",
        headers=_auth(),
        json=_creds(deleted=["not a key"]),
    )
    assert r.status_code == 422


def test_write_missing_env_404(monkeypatch):
    _reset(monkeypatch, present=False)
    r = client.post(
        "/api/panel/env/write",
        headers=_auth(),
        json=_creds(pairs=[{"key": "FOO", "value": "bar"}]),
    )
    assert r.status_code == 404


def test_write_compose_failure_reports_restarted_false(monkeypatch):
    _reset(monkeypatch, compose_ok=False)
    r = client.post(
        "/api/panel/env/write",
        headers=_auth(),
        json=_creds(pairs=[{"key": "FOO", "value": "bar"}]),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True  # .env was written
    assert body["restarted"] is False
    assert body["detail"]  # a compose-output tail is surfaced
    assert "__" not in body["detail"]  # markers stripped


def test_write_ssh_failure_502(monkeypatch):
    class BoomSSH:
        def __init__(self, *a, **k):
            pass

        async def connect(self, *a, **k):
            raise OSError("connection refused")

        async def close(self):
            pass

    monkeypatch.setattr(panel_deploy, "SSHSession", BoomSSH)
    r = client.post(
        "/api/panel/env/write",
        headers=_auth(),
        json=_creds(pairs=[{"key": "FOO", "value": "bar"}]),
    )
    assert r.status_code == 502


# ── (d) values only ever traverse the SILENT channel ────────────


def test_write_uses_silent_channel_only(monkeypatch):
    # EnvSSH has no run_script; a preserved secret is written via get_script_output
    # (silent). If any code path streamed it, the endpoint would AttributeError and
    # this 200 assertion would fail.
    _reset(monkeypatch)
    r = client.post(
        "/api/panel/env/write",
        headers=_auth(),
        json=_creds(pairs=[{"key": "FRONT_END_DOMAIN", "value": "x.example.com"}]),
    )
    assert r.status_code == 200
    # the secret rode ONLY in a silent write script (the client never saw it)
    assert "supersecret" not in r.text
    assert any("supersecret" in s for s in EnvSSH.scripts)
