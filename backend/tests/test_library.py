"""Wave-5 Plan C (scoped) — library files + notes CRUD + isolation."""
import uuid

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register", json={"login": f"lib-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_notes_crud():
    h = _auth()
    n = client.post("/api/library/notes", headers=h, json={"name": "N1", "text": "# hi"})
    assert n.status_code == 201 and "text" not in n.json()  # list-safe shape
    nid = n.json()["id"]
    assert client.get(f"/api/library/notes/{nid}", headers=h).json()["text"] == "# hi"
    client.put(f"/api/library/notes/{nid}", headers=h, json={"name": "N1x", "text": "# bye"})
    assert client.get(f"/api/library/notes/{nid}", headers=h).json()["text"] == "# bye"
    lst = client.get("/api/library", headers=h).json()
    assert len(lst) == 1 and lst[0]["kind"] == "note" and "text" not in lst[0]


def test_file_upload_download_delete():
    h = _auth()
    up = client.post("/api/library/upload", headers=h,
                     files={"file": ("doc.txt", b"hello library", "text/plain")})
    assert up.status_code == 201
    fid = up.json()["id"]
    assert up.json()["kind"] == "file" and up.json()["size"] == 13
    dl = client.get(f"/api/library/files/{fid}", headers=h)
    assert dl.status_code == 200 and dl.content == b"hello library"
    assert client.delete(f"/api/library/{fid}", headers=h).status_code == 204
    assert client.get("/api/library", headers=h).json() == []
    assert client.get(f"/api/library/files/{fid}", headers=h).status_code == 404


def test_isolation():
    a = _auth()
    client.post("/api/library/notes", headers=a, json={"name": "secret", "text": "x"})
    b = _auth()
    assert client.get("/api/library", headers=b).json() == []
