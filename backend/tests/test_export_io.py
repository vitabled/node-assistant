"""Wave-5 Plan L (slice 1) — export/import round-trip, secret-strip, confirm, isolation."""
import uuid

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register", json={"login": f"ex-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _export(h, **body) -> bytes:
    """`_export(h)` — весь аккаунт; `_export(h, stores=[...])` — выбранное."""
    r = client.post("/api/export", headers=h, json=body)
    assert r.status_code == 200, r.text
    return r.content


def _import(h, blob, confirm=True):
    return client.post("/api/import", headers=h,
                       files={"file": ("e.tar.gz", blob, "application/gzip")},
                       data={"confirm": "true" if confirm else "false"})


def test_roundtrip_strips_secrets_and_moves_data():
    a = _auth()
    # seed account A: a config template, a host, and a panel with a secret token
    client.post("/api/config-templates", headers=a, json={"name": "t1", "kind": "xray-json", "content_json": {}})
    client.post("/api/hosts", headers=a, json={"remark": "H1", "address": "n.example.com", "port": 443})
    client.post("/api/settings/remnawave", headers=a, json={"panel_url": "https://p", "api_token": "SECRET"})

    blob = _export(a)

    b = _auth()
    rep = _import(b, blob)
    assert rep.status_code == 200 and "settings.json" in rep.json()["applied"]
    # data moved
    assert len(client.get("/api/config-templates", headers=b).json()) == 1
    assert len(client.get("/api/hosts", headers=b).json()) == 1
    rw = client.get("/api/settings", headers=b).json()["remnawave"]
    assert rw["panel_url"] == "https://p"     # non-secret carried over
    assert rw["api_token"] == ""              # secret stripped


def test_import_keeps_target_credentials():
    # A no-secrets import must NOT touch the target's credential sections.
    a = _auth()
    client.post("/api/settings/remnawave", headers=a, json={"panel_url": "https://src", "api_token": "x"})
    blob = _export(a)
    b = _auth()
    client.post("/api/settings/remnawave", headers=b, json={"panel_url": "https://dst", "api_token": "KEEPME"})
    _import(b, blob)
    rw = client.get("/api/settings", headers=b).json()["remnawave"]
    assert rw["panel_url"] == "https://dst"   # target's panel config untouched
    assert rw["api_token"] == "KEEPME"        # target's secret preserved


def test_confirm_and_bad_archive():
    h = _auth()
    blob = _export(h)
    assert _import(h, blob, confirm=False).status_code == 400
    assert _import(h, b"not a tar.gz").status_code == 422


def test_vault_ciphertext_never_leaves_in_an_export():
    """The vault ships its INVENTORY but never the Fernet blobs.

    Regression: `_strip_secrets` only understood settings.json, so adding
    vault.json to the export list shipped every stored password/key as
    ciphertext — recoverable by anyone who also has ENCRYPTION_KEY."""
    a = _auth()
    client.post("/api/vault", headers=a, json={
        "name": "prod-root", "kind": "ssh_password", "resource": "10.0.0.1",
        "fields": {"password": "s3cr3t-pw"}})
    blob = _export(a)
    assert b"s3cr3t-pw" not in blob          # plaintext obviously never

    b = _auth()
    assert _import(b, blob).status_code == 200
    rows = client.get("/api/vault", headers=b).json()
    assert len(rows) == 1 and rows[0]["name"] == "prod-root"   # inventory carried
    assert rows[0]["has_secret"] is False                      # secret did not
    assert client.post(f"/api/vault/{rows[0]['id']}/reveal", headers=b).status_code == 404


def test_settings_enc_sections_are_all_swept():
    """Every *_enc key is zeroed, not just the two sections named in the old code."""
    a = _auth()
    client.post("/api/settings/auto-backup", headers=a,
                json={"enabled": True, "chat_id": "42", "bot_token": "111:bot-secret-token"})
    blob = _export(a)
    assert b"bot-secret-token" not in blob
    b = _auth()
    _import(b, blob)
    ab = client.get("/api/settings/auto-backup", headers=b).json()
    assert ab["chat_id"] == "42" and ab["has_token"] is False


def test_isolation():
    a = _auth()
    client.post("/api/hosts", headers=a, json={"remark": "solo", "address": "a.com", "port": 443})
    blob = _export(a)
    b = _auth()
    _import(b, blob)
    # importing into B did not touch A
    assert len(client.get("/api/hosts", headers=a).json()) == 1
    assert len(client.get("/api/hosts", headers=b).json()) == 1


# ── Выборочный экспорт/импорт ─────────────────────────────────
def _import_sel(h, blob, stores=""):
    return client.post("/api/import", headers=h,
                       files={"file": ("e.tar.gz", blob, "application/gzip")},
                       data={"confirm": "true", "stores": stores})


def test_export_only_selected_stores():
    a = _auth()
    client.post("/api/hostings", headers=a, json={"name": "H1"})
    client.post("/api/hosts", headers=a, json={"remark": "R1", "address": "n.example.com", "port": 443})

    blob = _export(a, stores=["hostings.json"])
    peek = client.post("/api/import/peek", headers=a,
                       files={"file": ("e.tar.gz", blob, "application/gzip")}).json()
    assert peek["stores"] == ["hostings.json"], "лишние сторы в архив не попали"


def test_export_only_one_settings_section():
    """«Только HAProxy» не должен утащить остальную конфигурацию аккаунта."""
    a = _auth()
    client.post("/api/settings/remnawave", headers=a,
                json={"panel_url": "https://p", "api_token": "T"})

    blob = _export(a, stores=["settings:haproxy"])
    peek = client.post("/api/import/peek", headers=a,
                       files={"file": ("e.tar.gz", blob, "application/gzip")}).json()
    assert peek["stores"] == ["settings.json"]
    assert peek["settings_sections"] == ["haproxy"], "секция ровно одна"


def test_import_applies_only_selected_stores():
    a = _auth()
    client.post("/api/hostings", headers=a, json={"name": "Только я"})
    client.post("/api/hosts", headers=a, json={"remark": "Не я", "address": "n.example.com", "port": 443})
    blob = _export(a)   # полный архив

    b = _auth()
    rep = _import_sel(b, blob, stores="hostings.json")
    assert rep.status_code == 200 and list(rep.json()["applied"]) == ["hostings.json"]
    assert len(client.get("/api/hostings", headers=b).json()) == 1
    assert client.get("/api/hosts", headers=b).json() == [], "невыбранный стор не применяется"


def test_selected_section_does_not_touch_the_rest_of_settings():
    a = _auth()
    client.post("/api/settings/deploy-defaults", headers=a, json={"ssh_user": "from-source"})
    blob = _export(a)

    b = _auth()
    client.post("/api/settings/deploy-defaults", headers=b, json={"ssh_user": "target-value"})
    _import_sel(b, blob, stores="settings:appearance")

    dd = client.get("/api/settings", headers=b).json()["deploy_defaults"]
    assert dd["ssh_user"] == "target-value", "невыбранная секция настроек не тронута"
