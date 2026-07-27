"""Shared media store + Obsidian-style notes (folders, wiki-links, embeds).

The security half of this file is the point: user bytes are served back from our
own origin, so raster images must go out inline and everything else — an SVG
above all — must go out as an opaque attachment.
"""
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.services import library_store

client = TestClient(app)

# 1x1 transparent GIF — small, real, and unambiguously an image.
GIF = bytes.fromhex("47494638396101000100800000ffffff00000021f90401000000002c000000000100010000020144003b")


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"med-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _upload(h, name="shot.gif", data=GIF, mime="image/gif"):
    return client.post("/api/media/upload", headers=h,
                       files={"file": (name, data, mime)})


# ── media store ───────────────────────────────────────────────
def test_upload_list_fetch_delete():
    h = _auth()
    r = _upload(h)
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["inline"] is True and item["size"] == len(GIF)
    assert "path" not in item, "путь на диске наружу не отдаём"

    assert [x["id"] for x in client.get("/api/media", headers=h).json()] == [item["id"]]

    got = client.get(f"/api/media/{item['id']}", headers=h)
    assert got.status_code == 200 and got.content == GIF
    assert got.headers["content-type"].startswith("image/gif")
    assert got.headers["x-content-type-options"] == "nosniff"

    assert client.delete(f"/api/media/{item['id']}", headers=h).status_code == 204
    assert client.get(f"/api/media/{item['id']}", headers=h).status_code == 404


def test_svg_is_never_served_inline():
    """An SVG is an XML document that can carry <script> — inline it would be
    stored XSS against the panel."""
    h = _auth()
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    r = _upload(h, "evil.svg", svg, "image/svg+xml")
    assert r.status_code == 201 and r.json()["inline"] is False

    got = client.get(f"/api/media/{r.json()['id']}", headers=h)
    assert got.headers["content-type"] == "application/octet-stream"
    assert got.headers["content-disposition"].startswith("attachment")


def test_rejects_unknown_type_and_oversize():
    h = _auth()
    assert _upload(h, "x.html", b"<h1>", "text/html").status_code == 400
    from app.services import media_store
    big = b"\0" * (media_store.MAX_FILE_BYTES + 1)
    assert _upload(h, "big.png", big, "image/png").status_code == 400


def test_media_is_isolated_per_account():
    a, b = _auth(), _auth()
    mid = _upload(a).json()["id"]
    assert client.get(f"/api/media/{mid}", headers=b).status_code == 404
    assert client.get("/api/media", headers=b).json() == []


def test_stored_extension_comes_from_the_mime_not_the_name():
    """`shot.png.html` must not land on disk as .html — the extension is derived
    from the ALLOWED mime, never from the upload's own name."""
    from app.services import accounts, media_store

    aid = client.post("/api/auth/register",
                      json={"login": f"med-{uuid.uuid4().hex[:8]}", "password": "pw"}).json()["id"]
    media_store.add("shot.png.html", GIF, "image/gif", account_id=aid)

    names = [p.name for p in (accounts.data_dir(aid) / "media" / "files").iterdir()]
    assert names and all(n.endswith(".gif") for n in names), names


# ── notes: folders + wiki-links ───────────────────────────────
def test_folder_is_normalised_and_subtree_renames():
    h = _auth()
    client.post("/api/library/notes", headers=h,
                json={"name": "A", "text": "", "folder": "/Инфра//Провайдеры/"})
    client.post("/api/library/notes", headers=h,
                json={"name": "B", "text": "", "folder": "Инфра"})
    items = {i["name"]: i for i in client.get("/api/library", headers=h).json()}
    assert items["A"]["folder"] == "Инфра/Провайдеры"

    r = client.post("/api/library/folders/rename", headers=h,
                    json={"src": "Инфра", "dst": "Инфраструктура"})
    assert r.json()["moved"] == 2
    items = {i["name"]: i for i in client.get("/api/library", headers=h).json()}
    assert items["A"]["folder"] == "Инфраструктура/Провайдеры"
    assert items["B"]["folder"] == "Инфраструктура"


def test_graph_resolves_links_and_reports_unresolved():
    h = _auth()
    a = client.post("/api/library/notes", headers=h,
                    json={"name": "Ноды", "text": "см. [[Провайдеры]] и [[Нет такой]]"}).json()["id"]
    b = client.post("/api/library/notes", headers=h,
                    json={"name": "Провайдеры", "text": "назад к [[Ноды|нодам]]"}).json()["id"]

    g = client.get("/api/library/graph", headers=h).json()
    assert g[a]["out"] == [b] and g[b]["in"] == [a]
    assert g[b]["out"] == [a] and g[a]["in"] == [b]     # alias form still resolves
    assert g[a]["unresolved"] == ["Нет такой"]


def test_renaming_a_note_retargets_links_to_it():
    h = _auth()
    target = client.post("/api/library/notes", headers=h,
                         json={"name": "Старое", "text": ""}).json()["id"]
    src = client.post("/api/library/notes", headers=h,
                      json={"name": "Ссылки", "text": "[[Старое]] и [[Старое|алиас]]"}).json()["id"]

    client.put(f"/api/library/notes/{target}", headers=h,
               json={"name": "Новое", "text": "", "folder": ""})

    text = client.get(f"/api/library/notes/{src}", headers=h).json()["text"]
    assert text == "[[Новое]] и [[Новое|алиас]]"
    assert client.get("/api/library/graph", headers=h).json()[src]["out"] == [target]


def test_embed_syntax_is_not_mistaken_for_a_link():
    """`![[id]]` is a media embed, `[[id]]` is a note link — one must not eat the
    other, or every embedded image would show up as a broken link."""
    text = "текст ![[abc123]] и [[Заметка]]"
    assert library_store.media_ids_of(text) == ["abc123"]
    g = library_store._links_of(text)
    assert g == ["Заметка"]
