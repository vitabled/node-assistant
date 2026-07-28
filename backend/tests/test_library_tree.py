"""Библиотека: пустые папки как отдельные записи индекса + порядок заметок.

Главное, что здесь фиксируется: удаление папки НЕ удаляет то, что внутри, а
перемещение поддерева двигает и заметки, и сами записи папок.
"""
import uuid

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"tree-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _folder(h, path):
    return client.post("/api/library/folders", headers=h, json={"path": path})


def _note(h, name, folder=""):
    return client.post("/api/library/notes", headers=h,
                       json={"name": name, "text": "", "folder": folder}).json()["id"]


def _items(h):
    return client.get("/api/library", headers=h).json()


def _folders(h):
    return sorted(i["path"] for i in _items(h) if i["kind"] == "folder")


def _notes(h):
    return {i["name"]: i for i in _items(h) if i["kind"] == "note"}


# ── пустые папки ──────────────────────────────────────────────
def test_empty_folder_shows_up_in_the_listing():
    h = _auth()
    r = _folder(h, "/Инфра//Провайдеры/")
    assert r.status_code == 201, r.text
    assert r.json()["kind"] == "folder" and r.json()["path"] == "Инфра/Провайдеры"

    rows = _items(h)
    assert len(rows) == 1
    # `path` у папки — это сама папка, а не имя файла на диске: он обязан дожить
    # до клиента (у file-записей путь по-прежнему вырезается).
    assert rows[0]["path"] == "Инфра/Провайдеры" and rows[0]["id"] == r.json()["id"]


def test_duplicate_folder_is_not_multiplied():
    h = _auth()
    first = _folder(h, "Инфра").json()
    again = _folder(h, "/Инфра/")
    assert again.status_code == 201 and again.json()["id"] == first["id"]
    assert _folders(h) == ["Инфра"]


def test_empty_path_is_rejected():
    h = _auth()
    assert _folder(h, "/./..//").status_code == 400


# ── удаление папки ────────────────────────────────────────────
def test_deleting_a_folder_lifts_its_contents_into_the_parent():
    h = _auth()
    fid = _folder(h, "Инфра/Провайдеры").json()["id"]
    _folder(h, "Инфра/Провайдеры/Пусто")
    _note(h, "Внутри", "Инфра/Провайдеры")
    _note(h, "Глубже", "Инфра/Провайдеры/Пусто")

    assert client.delete(f"/api/library/{fid}", headers=h).status_code == 204

    notes = _notes(h)
    assert set(notes) == {"Внутри", "Глубже"}, "заметки удалять нельзя"
    assert notes["Внутри"]["folder"] == "Инфра"
    assert notes["Глубже"]["folder"] == "Инфра/Пусто"
    assert _folders(h) == ["Инфра/Пусто"]


def test_deleting_a_top_level_folder_lifts_to_the_root():
    h = _auth()
    fid = _folder(h, "Инфра").json()["id"]
    _note(h, "N", "Инфра")

    client.delete(f"/api/library/{fid}", headers=h)
    assert _notes(h)["N"]["folder"] == ""
    assert _folders(h) == []


# ── переименование поддерева ──────────────────────────────────
def test_rename_moves_notes_folders_and_nested_ones():
    h = _auth()
    _folder(h, "Инфра")
    _folder(h, "Инфра/Провайдеры")
    _note(h, "N", "Инфра/Провайдеры")

    r = client.post("/api/library/folders/rename", headers=h,
                    json={"src": "Инфра", "dst": "Инфраструктура"})
    assert r.status_code == 200 and r.json()["moved"] == 3

    assert _folders(h) == ["Инфраструктура", "Инфраструктура/Провайдеры"]
    assert _notes(h)["N"]["folder"] == "Инфраструктура/Провайдеры"


def test_rename_to_root_drops_the_folder_row_but_keeps_nested_ones():
    h = _auth()
    _folder(h, "Инфра")
    _folder(h, "Инфра/Провайдеры")
    _note(h, "N", "Инфра")

    client.post("/api/library/folders/rename", headers=h, json={"src": "Инфра", "dst": ""})
    assert _folders(h) == ["Провайдеры"]
    assert _notes(h)["N"]["folder"] == ""


# ── порядок ───────────────────────────────────────────────────
def test_reorder_applies_folder_and_order():
    h = _auth()
    a, b = _note(h, "A"), _note(h, "B")
    assert _notes(h)["A"]["order"] == 0

    r = client.post("/api/library/reorder", headers=h, json={"items": [
        {"id": a, "folder": "Инфра", "order": 2},
        {"id": b, "order": 1},
    ]})
    assert r.status_code == 200 and r.json()["moved"] == 2

    notes = _notes(h)
    assert notes["A"]["folder"] == "Инфра" and notes["A"]["order"] == 2
    # folder не прислали → заметка осталась там же, поменялся только порядок.
    assert notes["B"]["folder"] == "" and notes["B"]["order"] == 1


def test_reorder_skips_unknown_ids():
    h = _auth()
    a = _note(h, "A")
    r = client.post("/api/library/reorder", headers=h, json={"items": [
        {"id": "нет-такого", "order": 5},
        {"id": a, "order": 3},
    ]})
    assert r.status_code == 200 and r.json()["moved"] == 1
    assert _notes(h)["A"]["order"] == 3


# ── изоляция ──────────────────────────────────────────────────
def test_folders_and_order_are_isolated_per_account():
    a, b = _auth(), _auth()
    _folder(a, "Инфра")
    nid = _note(a, "N", "Инфра")

    assert _items(b) == []
    # чужой id не должен ни примениться, ни сломать запрос
    r = client.post("/api/library/reorder", headers=b, json={"items": [{"id": nid, "order": 9}]})
    assert r.status_code == 200 and r.json()["moved"] == 0
    assert _notes(a)["N"]["order"] == 0
