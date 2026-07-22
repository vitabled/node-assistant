"""Wave-7 Plan G Ф5 — overlay variants of the subscription page.

Separate file from `test_subpages.py` on purpose: that one pins the LEGACY
single-HTML contract, and the first thing these tests check is that it still
holds. Keeping them apart makes a regression there unambiguous.
"""

import io
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import subpage_store

# NOTE: deliberately NO `sys.modules.setdefault("asyncssh", ...)` stub here.
# `test_subpages.py` installs one, which makes BOTH files un-runnable on their
# own (ssh_manager evaluates `asyncssh.SSHReader` at class-body time, and the
# empty stub has no such attribute). It only survives the full suite because
# some earlier test imports the real package first and `setdefault` no-ops.
# asyncssh is a real dependency — importing it is the honest thing to do.

client = TestClient(app)

_HTML = "<!doctype html><html><head><title>Orion</title></head><body>sub</body></html>"
_EJS = b"<!doctype html><body><%- panelData %></body>"


def _auth():
    r = client.post(
        "/api/auth/register",
        json={"login": f"ovl-{uuid.uuid4().hex[:8]}", "password": "pw"},
    )
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _overlay(h, name="v1"):
    r = client.post("/api/subpages/overlay", headers=h, json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def _put(h, pid, path, data=b"x"):
    return client.put(f"/api/subpages/{pid}/files/{path}", headers=h, content=data)


# ── the legacy contract must survive ──────────────────────────
def test_legacy_pages_untouched_by_overlays():
    h = _auth()
    legacy = client.post("/api/subpages", headers=h,
                         json={"name": "old.html", "html": _HTML}).json()
    ov = _overlay(h)
    listed = client.get("/api/subpages", headers=h).json()["pages"]
    assert {p["id"] for p in listed} == {legacy["id"], ov["id"]}
    assert client.get(f"/api/subpages/{legacy['id']}/raw", headers=h).status_code == 200
    # an overlay has no single html — the legacy route must not pretend otherwise
    assert client.get(f"/api/subpages/{ov['id']}/raw", headers=h).status_code == 404


def test_legacy_entries_have_no_kind_and_default_to_html():
    """A page written before Ф5 has no `kind` key at all; it must keep reading
    as html rather than 422-ing or being mistaken for an overlay.

    Asserted through the API, not `subpage_store` directly: the store resolves
    the account from the `current_account` ContextVar, which only exists inside
    a request."""
    h = _auth()
    p = client.post("/api/subpages", headers=h,
                    json={"name": "x", "html": _HTML}).json()
    assert "kind" not in p
    assert client.get(f"/api/subpages/{p['id']}/raw", headers=h).status_code == 200
    assert client.get(f"/api/subpages/{p['id']}/files", headers=h).status_code == 404


def test_files_routes_reject_a_legacy_page():
    h = _auth()
    p = client.post("/api/subpages", headers=h,
                    json={"name": "x", "html": _HTML}).json()
    assert client.get(f"/api/subpages/{p['id']}/files", headers=h).status_code == 404
    assert _put(h, p["id"], "a.txt").status_code == 404


# ── the catalogue screen must not break ───────────────────────
def test_overlay_entry_carries_a_numeric_size():
    """`SubPages.tsx` renders fmtSize(p.size), and fmtSize(undefined) prints
    'NaN КиБ' — so an overlay entry has to keep a numeric `size`."""
    h = _auth()
    ov = _overlay(h)
    assert ov["size"] == 0 and ov["kind"] == "overlay"
    _put(h, ov["id"], "assets/a.css", b"body{}")
    entry = next(p for p in client.get("/api/subpages", headers=h).json()["pages"]
                 if p["id"] == ov["id"])
    assert isinstance(entry["size"], int) and entry["size"] == 6
    assert entry["files_count"] == 1


# ── per-file CRUD ─────────────────────────────────────────────
def test_put_get_delete_member():
    h = _auth()
    ov = _overlay(h)
    r = _put(h, ov["id"], "assets/app.js", b"console.log(1)")
    assert r.status_code == 200 and r.json()["size"] == 14

    g = client.get(f"/api/subpages/{ov['id']}/files/assets/app.js", headers=h)
    assert g.status_code == 200 and g.content == b"console.log(1)"
    # never renderable on our origin
    assert g.headers["content-type"].startswith("application/octet-stream")
    assert g.headers["x-content-type-options"] == "nosniff"
    assert g.headers["content-disposition"] == "attachment"

    assert client.delete(f"/api/subpages/{ov['id']}/files/assets/app.js",
                         headers=h).status_code == 204
    assert client.get(f"/api/subpages/{ov['id']}/files/assets/app.js",
                      headers=h).status_code == 404


def test_binary_member_survives_roundtrip():
    """149 of the image's 160 files are binary — a text-only path would corrupt
    every font."""
    h = _auth()
    ov = _overlay(h)
    blob = bytes(range(256)) * 4
    _put(h, ov["id"], "assets/f.woff2", blob)
    got = client.get(f"/api/subpages/{ov['id']}/files/assets/f.woff2", headers=h)
    assert got.content == blob


def test_overwriting_a_member_does_not_double_count():
    h = _auth()
    ov = _overlay(h)
    _put(h, ov["id"], "a.txt", b"12345")
    _put(h, ov["id"], "a.txt", b"1")
    files = client.get(f"/api/subpages/{ov['id']}/files", headers=h).json()["files"]
    assert len(files) == 1 and files[0]["size"] == 1


# ── path guards ───────────────────────────────────────────────
def test_traversal_over_http_is_rejected():
    h = _auth()
    ov = _overlay(h)
    for bad in ["../evil", "a/../../evil"]:
        assert _put(h, ov["id"], bad).status_code in (404, 422), bad


@pytest.mark.parametrize("bad", [
    "/etc/passwd",          # absolute
    "..",                   # bare parent
    "a/../b",               # parent in the middle
    "C:/x",                 # windows drive
    "a\\b",                 # backslash separator
    "a//b",                 # empty segment
    "./a",                  # dot segment
    " ",                    # blank
    "",                     # empty
])
def test_normalize_relpath_rejects(bad):
    with pytest.raises(subpage_store.RelPathError):
        subpage_store.normalize_relpath(bad)


@pytest.mark.parametrize("bad", [
    "a b.js",               # space
    "файл.js",  # non-ascii
    "a;rm.js",              # shell metacharacter — these names reach `unzip`
    "a$x.js",
    "a|b.js",
])
def test_member_charset_is_narrow(bad):
    with pytest.raises(subpage_store.RelPathError):
        subpage_store.normalize_relpath(bad)


def test_depth_and_length_limits():
    with pytest.raises(subpage_store.RelPathError):
        subpage_store.normalize_relpath("/".join(["a"] * 11))
    with pytest.raises(subpage_store.RelPathError):
        subpage_store.normalize_relpath("a" * 201)
    assert subpage_store.normalize_relpath("assets/sub/dir/file.min.js") == \
        "assets/sub/dir/file.min.js"


# ── limits ────────────────────────────────────────────────────
def test_single_file_limit_is_413():
    h = _auth()
    ov = _overlay(h)
    big = b"0" * (subpage_store.MAX_FILE_BYTES + 1)
    assert _put(h, ov["id"], "big.bin", big).status_code == 413


def test_file_count_limit(monkeypatch):
    h = _auth()
    ov = _overlay(h)
    monkeypatch.setattr(subpage_store, "MAX_FILES_PER_VARIANT", 2)
    assert _put(h, ov["id"], "a.txt").status_code == 200
    assert _put(h, ov["id"], "b.txt").status_code == 200
    assert _put(h, ov["id"], "c.txt").status_code == 422


def test_variant_byte_budget(monkeypatch):
    h = _auth()
    ov = _overlay(h)
    monkeypatch.setattr(subpage_store, "MAX_VARIANT_BYTES", 10)
    assert _put(h, ov["id"], "a.txt", b"12345").status_code == 200
    assert _put(h, ov["id"], "b.txt", b"123456").status_code == 422


# ── EJS warning is a warning, not a refusal ───────────────────
def test_index_html_without_panel_data_warns_but_saves():
    h = _auth()
    ov = _overlay(h)
    r = _put(h, ov["id"], "index.html", b"<!doctype html><body>hi</body>")
    assert r.status_code == 200
    assert "panelData" in r.json()["warning"]
    assert client.get(f"/api/subpages/{ov['id']}/files/index.html",
                      headers=h).status_code == 200


def test_index_html_with_panel_data_has_no_warning():
    h = _auth()
    ov = _overlay(h)
    assert "warning" not in _put(h, ov["id"], "index.html", _EJS).json()


# ── zip, delete, isolation ────────────────────────────────────
def test_download_contains_only_overlay_files():
    h = _auth()
    ov = _overlay(h)
    _put(h, ov["id"], "index.html", _EJS)
    _put(h, ov["id"], "assets/a.css", b"body{}")
    r = client.get(f"/api/subpages/{ov['id']}/download", headers=h)
    assert r.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert sorted(names) == ["assets/a.css", "index.html"]


def test_deleting_an_overlay_removes_its_tree():
    h = _auth()
    ov = _overlay(h)
    _put(h, ov["id"], "assets/a.css", b"body{}")
    assert client.delete(f"/api/subpages/{ov['id']}", headers=h).status_code == 204
    assert client.get(f"/api/subpages/{ov['id']}/files", headers=h).status_code == 404


def test_overlays_are_per_account():
    h1, h2 = _auth(), _auth()
    ov = _overlay(h1)
    _put(h1, ov["id"], "a.txt", b"secret")
    assert client.get(f"/api/subpages/{ov['id']}/files", headers=h2).status_code == 404
    assert client.get(f"/api/subpages/{ov['id']}/files/a.txt",
                      headers=h2).status_code == 404


def test_requires_auth():
    assert client.post("/api/subpages/overlay", json={"name": "x"}).status_code == 401
