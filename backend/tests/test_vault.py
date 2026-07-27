"""Wave-9 Plan A Ф1 — «Хранилище» CRUD, isolation, secrecy, limits, download."""
import json
import uuid
from typing import get_args

from fastapi import Depends
from fastapi.testclient import TestClient

from app.api import vault as vault_api
from app.api.auth import require_account
from app.main import app
from app.services import accounts
from app.services import vault_store as store

# The router is wired into main.py by the integrating agent; add it here when it
# isn't there yet so this file tests the REAL app either way (same gating).
if not any(getattr(r, "path", "").startswith("/api/vault") for r in app.routes):
    app.include_router(vault_api.router, dependencies=[Depends(require_account)])

client = TestClient(app)

SECRET = "sk-live-9f3a-DO-NOT-LEAK"


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"vault-{uuid.uuid4().hex[:8]}", "password": "pw"})
    body = r.json()
    return {"Authorization": f"Bearer {body['token']}"}, body["id"]


def _create(headers, **over):
    body = {"name": "Hetzner API", "kind": "api_key", "resource": "api.hetzner.com",
            "fields": {"token": SECRET}}
    body.update(over)
    return client.post("/api/vault", headers=headers, json=body)


def test_requires_auth():
    assert client.get("/api/vault").status_code == 401


def test_crud_and_isolation():
    a, _ = _auth()
    b, _ = _auth()
    assert client.get("/api/vault", headers=a).json() == []

    r = _create(a, tags=["  prod ", "prod", "eu\r\nrack"])
    assert r.status_code == 201
    e = r.json()
    eid = e["id"]
    assert e["kind"] == "api_key" and e["resource"] == "api.hetzner.com"
    assert e["field_names"] == ["token"] and e["has_secret"] is True
    assert e["broken"] is False and e["revealed_at"] is None
    assert e["created_at"] > 0 and e["updated_at"] > 0
    assert e["tags"] == ["prod", "eu rack"]          # trimmed, deduped, CR/LF → space

    lst = client.get("/api/vault", headers=a).json()
    assert len(lst) == 1 and lst[0]["id"] == eid

    r = client.put(f"/api/vault/{eid}", headers=a, json={"name": "Hetzner (prod)"})
    assert r.status_code == 200 and r.json()["name"] == "Hetzner (prod)"

    # per-account isolation
    assert client.get("/api/vault", headers=b).json() == []
    assert client.put(f"/api/vault/{eid}", headers=b, json={"name": "steal"}).status_code == 404
    assert client.post(f"/api/vault/{eid}/reveal", headers=b).status_code == 404

    assert client.delete(f"/api/vault/{eid}", headers=a).status_code == 204
    assert client.delete(f"/api/vault/{eid}", headers=a).status_code == 404
    assert client.get("/api/vault", headers=a).json() == []


def test_no_plaintext_on_disk():
    a, aid = _auth()
    _create(a, fields={"token": SECRET})
    raw = (accounts.data_dir(aid) / "vault.json").read_bytes()
    assert SECRET.encode() not in raw
    assert b"fields_enc" in raw
    assert b"entries" in raw


def test_list_never_carries_a_secret():
    a, aid = _auth()
    eid = _create(a).json()["id"]
    lst = client.get("/api/vault", headers=a).json()
    assert SECRET not in json.dumps(lst, ensure_ascii=False)
    # the hint is a masked prefix, not the value
    hint = lst[0]["hint"]
    assert hint and hint != SECRET and "*" in hint and SECRET.startswith(hint.split("*")[0])
    # nor does the single-entry public shape (store called with an explicit account)
    assert SECRET not in json.dumps(store.get_entry(eid, aid), ensure_ascii=False)


def test_reveal_returns_fields_and_stamps_revealed_at():
    a, _ = _auth()
    eid = _create(a).json()["id"]
    r = client.post(f"/api/vault/{eid}/reveal", headers=a)
    assert r.status_code == 200 and r.json()["fields"] == {"token": SECRET}
    lst = client.get("/api/vault", headers=a).json()
    assert lst[0]["revealed_at"] and lst[0]["revealed_at"] > 0
    assert client.post("/api/vault/nope/reveal", headers=a).status_code == 404


def test_update_without_fields_keeps_the_secret():
    a, _ = _auth()
    eid = _create(a).json()["id"]
    r = client.put(f"/api/vault/{eid}", headers=a,
                   json={"name": "renamed", "note": "прод"})
    assert r.status_code == 200 and r.json()["field_names"] == ["token"]
    assert client.post(f"/api/vault/{eid}/reveal", headers=a).json()["fields"]["token"] == SECRET
    # ...and passing fields does replace it
    client.put(f"/api/vault/{eid}", headers=a, json={"fields": {"token": "new-value"}})
    assert client.post(f"/api/vault/{eid}/reveal", headers=a).json()["fields"] == {"token": "new-value"}


def test_limits():
    a, aid = _auth()
    # name over 80 → pydantic 422
    assert _create(a, name="x" * 81).status_code == 422
    # secret over 64 KiB → store ValueError → 400
    r = _create(a, kind="note", fields={"text": "x" * (65 * 1024)})
    assert r.status_code == 400 and "Секрет" in r.json()["detail"]
    # unknown kind → 422 (closed Literal)
    assert _create(a, kind="totp").status_code == 422
    # 501st entry → 400 (seed the file so the check is exercised, not 500 writes)
    seeded = [{"id": f"seed{i}", "name": "n", "kind": "note", "fields_enc": ""}
              for i in range(store.MAX_ENTRIES)]
    (accounts.data_dir(aid) / "vault.json").write_text(
        json.dumps({"entries": seeded}), encoding="utf-8")
    r = _create(a)
    assert r.status_code == 400 and "лимит" in r.json()["detail"]
    # a secret-less entry still lists cleanly
    assert client.get("/api/vault", headers=a).json()[0]["has_secret"] is False


def test_broken_ciphertext_is_flagged_not_raised():
    a, aid = _auth()
    eid = _create(a).json()["id"]
    p = accounts.data_dir(aid) / "vault.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data["entries"][0]["fields_enc"] = "gAAAAA-not-a-real-token"
    p.write_text(json.dumps(data), encoding="utf-8")

    lst = client.get("/api/vault", headers=a).json()         # must not 500
    assert len(lst) == 1 and lst[0]["broken"] is True
    assert lst[0]["field_names"] == [] and lst[0]["has_secret"] is False and lst[0]["hint"] == ""
    assert client.post(f"/api/vault/{eid}/reveal", headers=a).status_code == 404
    assert store.read_fields(eid, aid) is None
    # still editable/removable so the user can fix or drop it
    assert client.put(f"/api/vault/{eid}", headers=a, json={"name": "fix me"}).status_code == 200


def test_download_ssh_key():
    a, _ = _auth()
    key = "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----"
    eid = _create(a, name="deploy key #1", kind="ssh_key",
                  fields={"private_key": key, "passphrase": "pp"}).json()["id"]
    r = client.get(f"/api/vault/{eid}/download", headers=a)
    assert r.status_code == 200 and r.text == key
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-disposition"] == 'attachment; filename="deploykey1.pem"'
    assert r.headers["content-type"].startswith("application/octet-stream")
    assert client.get("/api/vault/nope/download", headers=a).status_code == 404


def test_download_rejects_other_kinds():
    a, _ = _auth()
    eid = _create(a).json()["id"]                            # api_key
    r = client.get(f"/api/vault/{eid}/download", headers=a)
    assert r.status_code == 400 and "SSH" in r.json()["detail"]


def test_schemas_endpoint_not_shadowed_by_entry_id():
    a, _ = _auth()
    r = client.get("/api/vault/schemas", headers=a)
    assert r.status_code == 200
    schemas = r.json()
    assert {s["kind"] for s in schemas} == set(store.KINDS)
    by_kind = {s["kind"]: s for s in schemas}
    assert [f["key"] for f in by_kind["ssh_key"]["fields"]] == ["private_key", "passphrase"]
    assert by_kind["ssh_key"]["fields"][1]["required"] is False
    assert by_kind["provider_creds"]["fields"] == []          # Plan C fills these in
    assert all(f["kind"] in ("text", "password", "textarea")
               for s in schemas for f in s["fields"])
    # the router Literal must not drift from the store's KINDS
    assert set(get_args(vault_api.VaultKind)) == set(store.KINDS)
