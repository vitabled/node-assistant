"""Ф5 — Orion subscription-page catalogue: CRUD, size limit, isolation, auth."""

import sys
import types
import uuid

sys.modules.setdefault("asyncssh", types.ModuleType("asyncssh"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)

_HTML = "<!doctype html><html><head><title>Orion</title></head><body>sub</body></html>"


def _auth():
    r = client.post(
        "/api/auth/register",
        json={"login": f"sub-{uuid.uuid4().hex[:8]}", "password": "pw"},
    )
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_subpages_crud():
    a = _auth()

    # empty catalogue
    assert client.get("/api/subpages", headers=a).json() == {"pages": []}

    # add
    r = client.post(
        "/api/subpages", headers=a, json={"name": "index.html", "html": _HTML}
    )
    assert r.status_code == 201
    page = r.json()
    assert page["id"] and page["name"] == "index.html"
    assert page["size"] == len(_HTML.encode("utf-8"))

    # list (metadata only — no html key)
    pages = client.get("/api/subpages", headers=a).json()["pages"]
    assert len(pages) == 1
    assert "html" not in pages[0]

    # raw html round-trips, with defence-in-depth headers (scriptless sandbox +
    # nosniff) so opening /raw in a new tab can't run the uploaded JS.
    raw = client.get(f"/api/subpages/{page['id']}/raw", headers=a)
    assert raw.status_code == 200
    assert raw.text == _HTML
    assert raw.headers["content-type"].startswith("text/html")
    assert raw.headers["content-security-policy"] == "sandbox"
    assert raw.headers["x-content-type-options"] == "nosniff"

    # delete → gone
    assert client.delete(f"/api/subpages/{page['id']}", headers=a).status_code == 204
    assert client.get("/api/subpages", headers=a).json() == {"pages": []}
    assert client.get(f"/api/subpages/{page['id']}/raw", headers=a).status_code == 404
    assert client.delete(f"/api/subpages/{page['id']}", headers=a).status_code == 404


def test_subpages_size_limit_and_empty_name():
    a = _auth()
    # > 512 KiB → 413
    big = "x" * (512 * 1024 + 1)
    assert (
        client.post(
            "/api/subpages", headers=a, json={"name": "big.html", "html": big}
        ).status_code
        == 413
    )
    # empty name → 422
    assert (
        client.post(
            "/api/subpages", headers=a, json={"name": "  ", "html": _HTML}
        ).status_code
        == 422
    )
    # empty/whitespace html → 422 (symmetric with the name check)
    assert (
        client.post(
            "/api/subpages", headers=a, json={"name": "x.html", "html": "   "}
        ).status_code
        == 422
    )
    # exactly at the limit is accepted
    at = "x" * (512 * 1024)
    assert (
        client.post(
            "/api/subpages", headers=a, json={"name": "ok.html", "html": at}
        ).status_code
        == 201
    )


def test_subpages_duplicate_name_allowed():
    a = _auth()
    r1 = client.post(
        "/api/subpages", headers=a, json={"name": "index.html", "html": _HTML}
    )
    r2 = client.post(
        "/api/subpages", headers=a, json={"name": "index.html", "html": _HTML}
    )
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["id"] != r2.json()["id"]  # unique ids despite same name


def test_subpages_isolation():
    a, b = _auth(), _auth()
    pid = client.post(
        "/api/subpages", headers=a, json={"name": "a.html", "html": _HTML}
    ).json()["id"]
    # b cannot see or read a's page
    assert client.get("/api/subpages", headers=b).json() == {"pages": []}
    assert client.get(f"/api/subpages/{pid}/raw", headers=b).status_code == 404
    assert client.delete(f"/api/subpages/{pid}", headers=b).status_code == 404
    # a still has it
    assert len(client.get("/api/subpages", headers=a).json()["pages"]) == 1


def test_subpages_page_cap():
    from app.services import subpage_store

    a = _auth()
    # Fill to the cap directly through the store (fast), then the API must 422.
    aid = client.get("/api/subpages", headers=a)  # ensure account dir exists
    assert aid.status_code == 200
    token = a["Authorization"].split()[1]
    from app.services import accounts

    acct = accounts.account_id_from_token(token)
    for i in range(subpage_store.MAX_PAGES):
        subpage_store.add_page(f"p{i}", _HTML, account_id=acct)
    r = client.post("/api/subpages", headers=a, json={"name": "over", "html": _HTML})
    assert r.status_code == 422
    assert "лимит" in r.json()["detail"].lower()


def test_subpages_requires_auth():
    assert client.get("/api/subpages").status_code == 401
    assert (
        client.post("/api/subpages", json={"name": "x", "html": _HTML}).status_code
        == 401
    )
    assert client.get("/api/subpages/abc/raw").status_code == 401
    assert client.delete("/api/subpages/abc").status_code == 401
