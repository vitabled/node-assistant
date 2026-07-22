"""Wave-7 Plan G Ф4 — baseline of the vendor subscription-page frontend.

The archive is built on someone else's machine, so most of these tests are about
refusing to trust it. The docker-command ORDER is tested too: `docker create`
auto-pulls a missing image while `docker image inspect` does not, so resolving
the digest first would fail on a clean node (measured on Docker 29).
"""
import io
import tarfile
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import subpage_baseline as bl

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"bl-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _tar(tmp_path: Path, entries, *, symlink=None, absolute=False, parent=False) -> Path:
    p = tmp_path / "frontend.tgz"
    with tarfile.open(p, "w:gz") as tf:
        for name, data in entries:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        if symlink:
            info = tarfile.TarInfo(symlink)
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tf.addfile(info)
        if absolute:
            info = tarfile.TarInfo("/etc/evil")
            info.size = 1
            tf.addfile(info, io.BytesIO(b"x"))
        if parent:
            info = tarfile.TarInfo("../evil")
            info.size = 1
            tf.addfile(info, io.BytesIO(b"x"))
    return p


# ── script generators ─────────────────────────────────────────
def test_create_precedes_inspect():
    """`docker image inspect` does NOT pull; `docker create` does. Resolving the
    digest before creating the container fails on a node that lacks the image."""
    s = bl.extract_tree_script("remnawave/subscription-page:7.2.6")
    assert s.index("docker create") < s.index("docker image inspect")


def test_script_quotes_the_image_and_cleans_up():
    s = bl.extract_tree_script("evil image; rm -rf /")
    assert "'evil image; rm -rf /'" in s
    assert "docker rm -f" in s and "trap" in s
    assert "docker cp" in s and "tar czf" in s


def test_script_falls_back_to_image_id_without_repo_digests():
    s = bl.extract_tree_script("local/built:dev")
    assert "RepoDigests" in s and ".Id" in s


def test_parse_probe():
    out = "some noise\nDIGEST=sha256:abc\nBYTES=1234\n"
    assert bl.parse_probe(out) == {"DIGEST": "sha256:abc", "BYTES": "1234"}
    assert bl.parse_probe("") == {}


def test_safe_digest_sanitises_a_path_segment():
    assert bl.safe_digest("sha256:abc123") == "sha256_abc123"
    assert "/" not in bl.safe_digest("../../etc/passwd")
    with pytest.raises(bl.BaselineError):
        bl.safe_digest("")


# ── archive validation ────────────────────────────────────────
def test_extracts_a_normal_tree(tmp_path):
    arc = _tar(tmp_path, [("index.html", b"<%- panelData %>"),
                          ("assets/app.js", b"x" * 10)])
    manifest = bl.extract_archive(arc, tmp_path / "out")
    assert sorted(f["path"] for f in manifest) == ["assets/app.js", "index.html"]
    assert (tmp_path / "out" / "assets" / "app.js").read_bytes() == b"x" * 10


def test_rejects_a_symlink_member(tmp_path):
    arc = _tar(tmp_path, [("index.html", b"x")], symlink="link")
    with pytest.raises(bl.BaselineError):
        bl.extract_archive(arc, tmp_path / "out")


def test_rejects_an_absolute_member(tmp_path):
    arc = _tar(tmp_path, [("index.html", b"x")], absolute=True)
    with pytest.raises(bl.BaselineError):
        bl.extract_archive(arc, tmp_path / "out")


def test_rejects_a_parent_traversal_member(tmp_path):
    arc = _tar(tmp_path, [("index.html", b"x")], parent=True)
    with pytest.raises(bl.BaselineError):
        bl.extract_archive(arc, tmp_path / "out")


def test_rejects_nothing_written_when_a_member_is_bad(tmp_path):
    """Validation runs over ALL members before anything is written — a rejected
    archive must not leave a half-extracted baseline behind."""
    arc = _tar(tmp_path, [("index.html", b"x")], parent=True)
    out = tmp_path / "out"
    with pytest.raises(bl.BaselineError):
        bl.extract_archive(arc, out)
    assert not (out / "index.html").exists()


def test_rejects_an_oversized_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(bl, "MAX_BASELINE_BYTES", 10)
    arc = _tar(tmp_path, [("a.bin", b"0" * 20)])
    with pytest.raises(bl.BaselineError):
        bl.extract_archive(arc, tmp_path / "out")


def test_rejects_too_many_members(tmp_path, monkeypatch):
    monkeypatch.setattr(bl, "MAX_BASELINE_FILES", 2)
    arc = _tar(tmp_path, [(f"f{i}.txt", b"x") for i in range(3)])
    with pytest.raises(bl.BaselineError):
        bl.extract_archive(arc, tmp_path / "out")


# ── cache ─────────────────────────────────────────────────────
def test_save_and_read_baseline(tmp_path):
    arc = _tar(tmp_path, [("index.html", b"<%- panelData %>")])
    meta = bl.save_baseline("sha256:deadbeef", "img:1", arc)
    assert meta["files_count"] == 1 and meta["bytes"] == 16
    assert bl.has_baseline("sha256:deadbeef")
    assert bl.read_file("sha256:deadbeef", "index.html") == b"<%- panelData %>"
    assert bl.read_file("sha256:deadbeef", "nope.html") is None
    assert bl.read_file("sha256:missing", "index.html") is None


def test_read_file_refuses_a_path_outside_the_manifest(tmp_path):
    arc = _tar(tmp_path, [("index.html", b"x")])
    bl.save_baseline("sha256:guard", "img:1", arc)
    # Not in the manifest → refused before the filesystem is touched.
    assert bl.read_file("sha256:guard", "../../../etc/passwd") is None


# ── routes ────────────────────────────────────────────────────
def test_routes_require_auth():
    assert client.get("/api/subpages/baselines").status_code == 401
    assert client.post("/api/subpages/baselines/pull",
                       json={"ip": "1.2.3.4"}).status_code == 401


def test_baselines_literal_route_is_not_swallowed_by_page_id(tmp_path):
    """`GET /baselines` must reach the baseline handler, not be parsed as a
    page id — literal paths are declared above the parameterised ones."""
    h = _auth()
    r = client.get("/api/subpages/baselines", headers=h)
    assert r.status_code == 200 and "baselines" in r.json()


def test_unknown_digest_is_404():
    h = _auth()
    assert client.get("/api/subpages/baselines/nope/files", headers=h).status_code == 404


def test_pull_rejects_a_malicious_image_name():
    h = _auth()
    r = client.post("/api/subpages/baselines/pull", headers=h,
                    json={"ip": "1.2.3.4", "image": "img; rm -rf /"})
    assert r.status_code == 422


def test_pull_returns_a_streamable_task(monkeypatch):
    h = _auth()
    # Don't actually SSH anywhere: the point is the task handshake.
    async def boom(self):
        raise RuntimeError("no network in tests")
    monkeypatch.setattr("app.services.ssh_manager.SSHSession.connect", boom)
    r = client.post("/api/subpages/baselines/pull", headers=h,
                    json={"ip": "192.0.2.1", "ssh_password": "x"})
    assert r.status_code == 200
    assert r.json()["task_id"] and r.json()["task_type"] == "subpage-baseline"
