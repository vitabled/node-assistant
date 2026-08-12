"""Wave-4 PR-11 — копия сайта: bulk-импорт в Библиотеку, удаление группы, роут."""
import asyncio
import uuid as _uuid
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.services import accounts, library_store, sitecopy

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"sc-{_uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ── library_store bulk ─────────────────────────────────────────
def test_add_files_bulk_groups_and_counts(tmp_path, monkeypatch):
    aid = "acc-bulk"
    monkeypatch.setattr(accounts, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(accounts, "data_dir", lambda a=None: tmp_path / "accounts" / (a or aid))
    files = [("index.html", b"<html>1</html>", "text/html"),
             ("assets/app.css", b"body{}", "text/css"),
             ("big.bin", b"x" * (library_store.MAX_FILE_BYTES + 1), "application/octet-stream")]
    stats = library_store.add_files_bulk(files, "Сайты/example-1", aid)
    assert stats["imported"] == 2
    assert stats["skipped_oversize"] == 1
    items = library_store.list_items(aid)
    assert {i["name"] for i in items} == {"index.html", "assets/app.css"}
    assert all(i.get("folder") == "Сайты/example-1" for i in items)
    # файлы реально лежат на диске
    got = library_store.get_file(items[0]["id"], aid)
    assert got and got[0] == b"<html>1</html>"


def test_delete_files_by_folder_removes_group(tmp_path, monkeypatch):
    aid = "acc-del"
    monkeypatch.setattr(accounts, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(accounts, "data_dir", lambda a=None: tmp_path / "accounts" / (a or aid))
    library_store.add_files_bulk([("a.html", b"a", "text/html"),
                                  ("b.html", b"b", "text/html")], "Сайты/x-1", aid)
    library_store.add_file("keep.pdf", b"k", "application/pdf", aid)   # без папки — не трогаем
    assert library_store.delete_files_by_folder("Сайты/x-1", aid) == 2
    names = {i["name"] for i in library_store.list_items(aid)}
    assert names == {"keep.pdf"}


# ── sitecopy helpers ───────────────────────────────────────────
def test_collect_files_html_first_and_skips_cache(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "hts-cache").mkdir()
    (tmp_path / "z.html").write_bytes(b"<html>z</html>")
    (tmp_path / "assets" / "a.css").write_bytes(b"body{}")
    (tmp_path / "hts-cache" / "internal.txt").write_bytes(b"x")
    entries = sitecopy.collect_files(tmp_path)
    rels = [e[0] for e in entries]
    assert rels[0] == "z.html"                      # html первым
    assert "assets/a.css" in rels
    assert "hts-cache/internal.txt" not in rels     # кэш не импортируем


def test_build_httrack_cmd_flags():
    cmd = sitecopy.build_httrack_cmd("https://example.com", Path("/tmp/m"), depth=3, max_bytes=1000)
    s = " ".join(cmd)
    assert "-r3" in s and "-%e0" in s and "--max-size 1000" in s
    assert cmd[0] == "httrack" and cmd[1] == "https://example.com"


# ── маршрут ────────────────────────────────────────────────────
def test_route_validation():
    a = _auth()
    assert client.post("/api/sitecopy", headers=a, json={"url": "ftp://x"}).status_code == 422
    assert client.post("/api/sitecopy", headers=a,
                       json={"url": "http://127.0.0.1/x"}).status_code == 422  # SSRF
    assert client.post("/api/sitecopy", headers=a,
                       json={"url": "https://example.com", "depth": 99}).status_code == 422
