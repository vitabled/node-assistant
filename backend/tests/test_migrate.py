"""Tests for Marzban→Remnawave migration (Ф7).

The migrate binary + Marzban/Remnawave APIs are mocked; the PURE parser, Reality
patch-builder and docker-arg builder are tested directly.
"""

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from app.services import marzban_migrate, marzban_reality
from app.api import migrate as migrate_api
from app.main import app

client = TestClient(app)


def _auth():
    login = f"mg-{uuid.uuid4().hex[:8]}"
    r = client.post("/api/auth/register", json={"login": login, "password": "pw-1"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ── pure: output parser ───────────────────────────────────────
def test_parse_migrate_output():
    text = "Starting...\nCreated 120 users\nSkipped: 5\nFailed 2\nDone"
    out = marzban_migrate.parse_migrate_output(text)
    assert out["created"] == 120 and out["skipped"] == 5 and out["failed"] == 2


def test_parse_migrate_output_empty():
    assert marzban_migrate.parse_migrate_output("") == {
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "failed": 0,
    }


# ── pure: docker args ─────────────────────────────────────────
def test_migrate_docker_args_flags():
    args = marzban_migrate.migrate_docker_args(
        {
            "marzban_url": "https://mz",
            "marzban_username": "a",
            "marzban_password": "b",
            "remnawave_url": "https://rw",
            "remnawave_token": "t",
            "internal_squad_uuids": ["u1", "u2"],
            "batch_size": 50,
        }
    )
    j = " ".join(args)
    assert "--panel-type=marzban" in j
    assert "--panel-url=https://mz" in j
    assert "--remnawave-url=https://rw" in j
    assert "--preserve-status" in j and "--preserve-subhash" in j  # default ON
    assert "--internal-squad=u1,u2" in j
    assert "--batch-size=50" in j


# ── pure: Reality patch builder ───────────────────────────────
def test_build_reality_patch_matches_by_tag():
    marzban = {
        "inbounds": [
            {
                "tag": "VLESS_R",
                "streamSettings": {
                    "security": "reality",
                    "realitySettings": {
                        "privateKey": "PRIV",
                        "shortIds": ["ab"],
                        "serverNames": ["ms.com"],
                    },
                },
            },
            {
                "tag": "ORPHAN",
                "streamSettings": {"realitySettings": {"privateKey": "X"}},
            },
        ]
    }
    profile = {
        "inbounds": [
            {"tag": "VLESS_R", "streamSettings": {"security": "none"}},
            {"tag": "OTHER", "streamSettings": {}},
        ]
    }
    patched, report = marzban_reality.build_reality_patch(marzban, profile)
    assert report["matched"] == ["VLESS_R"]
    assert report["unmatched"] == ["ORPHAN"]  # no same-tag Remnawave inbound
    rs = patched["inbounds"][0]["streamSettings"]["realitySettings"]
    assert rs["privateKey"] == "PRIV" and rs["shortIds"] == ["ab"]
    assert patched["inbounds"][0]["streamSettings"]["security"] == "reality"
    # source profile untouched (deep copy)
    assert "realitySettings" not in profile["inbounds"][0]["streamSettings"]


def test_build_reality_patch_no_reality_inbounds():
    patched, report = marzban_reality.build_reality_patch(
        {"inbounds": []}, {"inbounds": []}
    )
    assert report["matched"] == [] and report["unmatched"] == []


# ── API gating ────────────────────────────────────────────────
def test_migrate_routes_require_account():
    assert client.post("/api/migrate/preview", json={}).status_code == 401


# ── preview (mocked Marzban) ──────────────────────────────────
def test_preview_returns_counts_and_loss_report(monkeypatch):
    h = _auth()

    async def fake_login(url, u, p):
        return "tok"

    async def fake_counts(url, tok):
        return {"total_users": 42, "inbound_tags": ["VLESS_R", "TROJAN"]}

    monkeypatch.setattr(marzban_migrate, "marzban_login", fake_login)
    monkeypatch.setattr(marzban_migrate, "marzban_counts", fake_counts)
    r = client.post(
        "/api/migrate/preview",
        headers=h,
        json={
            "marzban_url": "https://mz.example",
            "marzban_username": "a",
            "marzban_password": "b",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total_users"] == 42
    assert "VLESS_R" in body["inbound_tags"]
    assert any("Reality" in x for x in body["will_not_migrate"])


def test_preview_bad_creds_400(monkeypatch):
    h = _auth()

    async def boom(url, u, p):
        raise marzban_migrate.MarzbanApiError("Неверные admin-креды Marzban (401).")

    monkeypatch.setattr(marzban_migrate, "marzban_login", boom)
    r = client.post(
        "/api/migrate/preview",
        headers=h,
        json={
            "marzban_url": "https://mz.example",
            "marzban_username": "a",
            "marzban_password": "b",
        },
    )
    assert r.status_code == 400


# ── reality endpoint (mocked Marzban + Remnawave) ─────────────
class _FakeRW:
    def __init__(self, *a, **k):
        self.updated = None

    async def get_config_profile(self, uuid):
        return {
            "config": {
                "inbounds": [{"tag": "VLESS_R", "streamSettings": {"security": "none"}}]
            }
        }

    async def update_config_profile(self, uuid, config):
        self.updated = config
        return {}


def test_reality_endpoint_patches_profile(monkeypatch):
    h = _auth()
    fake = _FakeRW()

    async def fake_login(url, u, p):
        return "tok"

    async def fake_core(url, tok):
        return {
            "inbounds": [
                {
                    "tag": "VLESS_R",
                    "streamSettings": {
                        "realitySettings": {"privateKey": "PRIV", "shortIds": ["cd"]}
                    },
                }
            ]
        }

    monkeypatch.setattr(marzban_migrate, "marzban_login", fake_login)
    monkeypatch.setattr(marzban_migrate, "marzban_core_config", fake_core)
    monkeypatch.setattr(migrate_api, "RemnavaveClient", lambda *a, **k: fake)
    monkeypatch.setattr(
        migrate_api.net_guard, "is_safe_url", lambda u: True
    )  # placeholder url

    r = client.post(
        "/api/migrate/reality",
        headers=h,
        json={
            "marzban_url": "https://mz",
            "marzban_username": "a",
            "marzban_password": "b",
            "remnawave_url": "https://rw",
            "remnawave_token": "t",
            "config_profile_uuid": "prof-1",
        },
    )
    assert r.status_code == 200
    assert r.json()["applied"] is True
    assert r.json()["matched"] == ["VLESS_R"]
    # The profile was PATCHed with the copied Reality key.
    assert (
        fake.updated["inbounds"][0]["streamSettings"]["realitySettings"]["privateKey"]
        == "PRIV"
    )


# ── run gating ────────────────────────────────────────────────
def test_run_requires_confirm():
    h = _auth()
    r = client.post(
        "/api/migrate/run",
        headers=h,
        json={
            "marzban_url": "https://mz",
            "marzban_username": "a",
            "marzban_password": "b",
            "remnawave_url": "https://rw",
            "remnawave_token": "t",
            "confirm": False,
        },
    )
    assert r.status_code == 400


def test_run_returns_task_id(monkeypatch):
    h = _auth()

    async def noop(task, cfg):
        return None

    monkeypatch.setattr(marzban_migrate, "run_migrate", noop)
    monkeypatch.setattr(
        migrate_api.net_guard, "is_safe_url", lambda u: True
    )  # placeholder url
    r = client.post(
        "/api/migrate/run",
        headers=h,
        json={
            "marzban_url": "https://mz",
            "marzban_username": "a",
            "marzban_password": "b",
            "remnawave_url": "https://rw",
            "remnawave_token": "t",
            "confirm": True,
        },
    )
    assert r.status_code == 200 and r.json()["task_type"] == "marzban-migrate"


# ── SSRF guards ───────────────────────────────────────────────
def test_marzban_login_blocks_internal_url():
    with pytest.raises(marzban_migrate.MarzbanApiError):
        asyncio.run(marzban_migrate.marzban_login("http://169.254.169.254", "a", "b"))


def test_marzban_get_re_guards_internal_url():
    with pytest.raises(marzban_migrate.MarzbanApiError):
        asyncio.run(marzban_migrate.marzban_counts("http://127.0.0.1", "tok"))


def test_reality_rejects_internal_remnawave_url(monkeypatch):
    h = _auth()

    async def fake_login(url, u, p):
        return "tok"

    async def fake_core(url, tok):
        return {"inbounds": []}

    monkeypatch.setattr(marzban_migrate, "marzban_login", fake_login)
    monkeypatch.setattr(marzban_migrate, "marzban_core_config", fake_core)
    r = client.post(
        "/api/migrate/reality",
        headers=h,
        json={
            "marzban_url": "https://mz",
            "marzban_username": "a",
            "marzban_password": "b",
            "remnawave_url": "http://127.0.0.1:3000",
            "remnawave_token": "t",
            "config_profile_uuid": "p1",
        },
    )
    assert r.status_code == 400
    assert "remnawave_url" in r.json()["detail"]


def test_reality_no_match_does_not_patch(monkeypatch):
    h = _auth()

    class _RW:
        def __init__(self, *a, **k):
            self.updated = None

        async def get_config_profile(self, uuid):
            return {
                "config": {"inbounds": [{"tag": "DIFFERENT", "streamSettings": {}}]}
            }

        async def update_config_profile(self, uuid, config):
            self.updated = config

    fake = _RW()

    async def fake_login(url, u, p):
        return "tok"

    async def fake_core(url, tok):
        return {
            "inbounds": [
                {
                    "tag": "VLESS_R",
                    "streamSettings": {"realitySettings": {"privateKey": "P"}},
                }
            ]
        }

    monkeypatch.setattr(marzban_migrate, "marzban_login", fake_login)
    monkeypatch.setattr(marzban_migrate, "marzban_core_config", fake_core)
    monkeypatch.setattr(migrate_api, "_remnawave_client", lambda u, t: fake)
    r = client.post(
        "/api/migrate/reality",
        headers=h,
        json={
            "marzban_url": "https://mz",
            "marzban_username": "a",
            "marzban_password": "b",
            "remnawave_url": "https://rw",
            "remnawave_token": "t",
            "config_profile_uuid": "p1",
        },
    )
    assert r.status_code == 200
    assert r.json()["applied"] is False
    assert fake.updated is None


def test_legacy_secret_reads_over_ssh(monkeypatch):
    h = _auth()

    class _SSH:
        def __init__(self, *a, **k):
            pass

        async def connect(self):
            pass

        async def get_output(self, cmd):
            return "  legacy-secret-abc  "

        async def close(self):
            pass

    monkeypatch.setattr(migrate_api, "SSHSession", _SSH)
    r = client.post(
        "/api/migrate/legacy-secret",
        headers=h,
        json={"ip": "1.2.3.4", "ssh_port": 22, "ssh_user": "root", "ssh_password": "x"},
    )
    assert r.status_code == 200
    assert r.json()["secret_key"] == "legacy-secret-abc"
    assert r.json()["env_hint"] == "MARZBAN_LEGACY_SECRET_KEY"


def test_redact_masks_secrets():
    out = marzban_migrate._redact("token=SECRETTOK pass=PW123", "SECRETTOK", "PW123")
    assert "SECRETTOK" not in out and "PW123" not in out


def test_image_pinned_server_side():
    from app.api.migrate import RunBody, _MIGRATE_IMAGE

    assert "image" not in RunBody.model_fields  # not a request field
    assert _MIGRATE_IMAGE == "remnawave/migrate:latest"
