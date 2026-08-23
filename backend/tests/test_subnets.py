"""Wave-5 PR-5 — «Подсети»: провайдеры/списки/строки/столбцы, обогащение,
импорт/экспорт (json/csv/txt/xlsx)."""
import io
import json as _json
import uuid as _uuid

import openpyxl
from fastapi.testclient import TestClient

from app.main import app
from app.services import subnets_store as store

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"sn-{_uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_list(a):
    p = client.post("/api/subnets/providers", headers=a, json={"name": "МТС"}).json()
    l = client.post(f"/api/subnets/providers/{p['id']}/lists", headers=a,
                    json={"name": "Основной"}).json()
    return p["id"], l["id"]


def test_provider_list_row_flow():
    a = _auth()
    pid, lid = _mk_list(a)
    r = client.post(f"/api/subnets/providers/{pid}/lists/{lid}/rows", headers=a,
                    json={"subnets": ["203.0.113.9/24", "2001:db8::/32", "мусор"]})
    assert r.status_code == 201
    assert r.json()["added"] == ["203.0.113.0/24", "2001:db8::/32"]
    assert len(r.json()["errors"]) == 1
    data = client.get("/api/subnets", headers=a).json()
    lst = data["providers"][0]["lists"][0]
    assert [c["key"] for c in lst["columns"]] == ["subnet", "ipver", "asn", "asnname", "date", "operators"]
    row = lst["rows"][0]
    assert row["values"]["subnet"] == "203.0.113.0/24"
    assert row["values"]["ipver"] == "IPv4"
    assert row["operators"]["mts"] is True
    assert data["operators"][1]["key"] == "beeline"


def test_parse_subnet():
    assert store.parse_subnet("1.2.3.4") == ("1.2.3.4/32", "IPv4")
    assert store.parse_subnet("1.2.3.9/24") == ("1.2.3.0/24", "IPv4")
    assert store.parse_subnet("2001:db8::1") == ("2001:db8::1/128", "IPv6")
    try:
        store.parse_subnet("not-an-ip")
        raise AssertionError("должно упасть")
    except ValueError:
        pass


def test_column_ops_and_operator_toggle():
    a = _auth()
    pid, lid = _mk_list(a)
    client.post(f"/api/subnets/providers/{pid}/lists/{lid}/rows", headers=a,
                json={"subnets": ["1.2.3.0/24"]})
    # новый столбец
    col = client.post(f"/api/subnets/providers/{pid}/lists/{lid}/columns", headers=a,
                      json={"title": "Комментарий"}).json()
    # порядок: комментарий первым
    r = client.put(f"/api/subnets/providers/{pid}/lists/{lid}/columns-order", headers=a,
                   json={"order": [col["key"], "subnet", "operators"]})
    assert r.status_code == 200
    data = client.get("/api/subnets", headers=a).json()
    cols = [c["key"] for c in data["providers"][0]["lists"][0]["columns"]]
    assert cols[0] == col["key"] and cols[1] == "subnet"
    # удаление «Подсети» запрещено
    r = client.delete(f"/api/subnets/providers/{pid}/lists/{lid}/columns/subnet", headers=a)
    assert r.status_code == 422
    # тоггл оператора
    rid = data["providers"][0]["lists"][0]["rows"][0]["id"]
    r = client.patch(f"/api/subnets/providers/{pid}/lists/{lid}/rows/{rid}/operator/beeline",
                     headers=a, json={"on": False})
    assert r.status_code == 200
    data = client.get("/api/subnets", headers=a).json()
    row = data["providers"][0]["lists"][0]["rows"][0]
    assert row["operators"]["beeline"] is False
    assert row["operators"]["mts"] is True


def test_enrich_marks_asn(monkeypatch):
    import app.api.subnets as api
    a = _auth()
    pid, lid = _mk_list(a)
    client.post(f"/api/subnets/providers/{pid}/lists/{lid}/rows", headers=a,
                json={"subnets": ["8.8.8.0/24"]})
    data = client.get("/api/subnets", headers=a).json()
    rid = data["providers"][0]["lists"][0]["rows"][0]["id"]

    class R:
        def json(self):
            return {"status": "success", "as": "AS15169 Google LLC", "asname": "Google", "org": "Google"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def get(self, url, params=None):
            return R()

    monkeypatch.setattr(api.httpx, "AsyncClient", lambda *a, **k: FakeClient())
    r = client.post(f"/api/subnets/providers/{pid}/lists/{lid}/enrich", headers=a,
                    json={"row_ids": [rid]})
    assert r.status_code == 200 and r.json()["updated"] == 1
    data = client.get("/api/subnets", headers=a).json()
    row = data["providers"][0]["lists"][0]["rows"][0]
    assert row["values"]["asn"] == "AS15169"
    assert "Google" in row["values"]["asnname"]


# ── импорт/экспорт (json/csv/txt) ─────────────────────────────
def _rows(a, pid=0, lid=0):
    data = client.get("/api/subnets", headers=a).json()
    return data["providers"][pid]["lists"][lid]["rows"]


def test_export_json_tree():
    a = _auth()
    pid, lid = _mk_list(a)
    client.post(f"/api/subnets/providers/{pid}/lists/{lid}/rows", headers=a,
                json={"subnets": ["203.0.113.0/24", "198.51.100.0/24"]})
    r = client.get("/api/subnets/export", headers=a)
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    assert ".json" in r.headers["content-disposition"]
    body = r.json()
    assert body["_format"] == "na-subnets" and body["_version"] == 1
    assert len(body["providers"][0]["lists"][0]["rows"]) == 2


def test_export_csv_list():
    a = _auth()
    pid, lid = _mk_list(a)
    client.post(f"/api/subnets/providers/{pid}/lists/{lid}/rows", headers=a,
                json={"subnets": ["203.0.113.0/24"]})
    r = client.get("/api/subnets/export", headers=a,
                   params={"provider_id": pid, "list_id": lid, "format": "csv"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0].split(",")[0] == "subnet"
    assert "mts" in lines[0]
    assert lines[1].startswith("203.0.113.0/24,IPv4")
    # csv без списка — 400
    assert client.get("/api/subnets/export", headers=a,
                      params={"format": "csv"}).status_code == 400
    # неизвестный формат — 400
    assert client.get("/api/subnets/export", headers=a,
                      params={"format": "xml", "list_id": lid}).status_code == 400


def test_export_txt_list():
    a = _auth()
    pid, lid = _mk_list(a)
    client.post(f"/api/subnets/providers/{pid}/lists/{lid}/rows", headers=a,
                json={"subnets": ["203.0.113.0/24", "2001:db8::/32"]})
    r = client.get("/api/subnets/export", headers=a,
                   params={"list_id": lid, "format": "txt"})
    assert r.status_code == 200
    lines = [l for l in r.text.splitlines() if l and not l.startswith("#")]
    assert lines == ["203.0.113.0/24", "2001:db8::/32"]


def test_import_json_merge_skips_duplicates():
    a = _auth()
    pid, lid = _mk_list(a)
    client.post(f"/api/subnets/providers/{pid}/lists/{lid}/rows", headers=a,
                json={"subnets": ["203.0.113.0/24"]})
    snapshot = client.get("/api/subnets/export", headers=a).json()
    rows = snapshot["providers"][0]["lists"][0]["rows"]
    rows.append({"id": "x", "values": {"subnet": "198.51.100.0/24"}, "operators": {}})
    blob = _json.dumps(snapshot).encode()
    r = client.post("/api/subnets/import", headers=a,
                    files={"file": ("snap.json", blob, "application/json")},
                    data={"mode": "merge"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["imported"] == 1 and body["skipped"] == 1
    subnets = {r_["values"]["subnet"] for r_ in _rows(a)}
    assert subnets == {"203.0.113.0/24", "198.51.100.0/24"}


def test_import_json_replace_into_list():
    a = _auth()
    pid, lid = _mk_list(a)
    client.post(f"/api/subnets/providers/{pid}/lists/{lid}/rows", headers=a,
                json={"subnets": ["203.0.113.0/24"]})
    snap = {"_format": "na-subnets", "_version": 1, "providers": [
        {"name": "X", "lists": [{"name": "Y", "rows": [
            {"values": {"subnet": "10.0.0.0/8"}, "operators": {"mts": False}}]}]}]}
    r = client.post("/api/subnets/import", headers=a,
                    files={"file": ("s.json", _json.dumps(snap).encode(), "application/json")},
                    data={"provider_id": pid, "list_id": lid, "mode": "replace"})
    assert r.status_code == 200 and r.json()["imported"] == 1
    rows = _rows(a)
    assert len(rows) == 1 and rows[0]["values"]["subnet"] == "10.0.0.0/8"
    assert rows[0]["operators"]["mts"] is False


def test_import_csv_with_header_sets_operators():
    a = _auth()
    pid, lid = _mk_list(a)
    csv_text = ("subnet,version,asn,asnname,date,mts,beeline,Комментарий\n"
                "203.0.113.5/24,IPv4,AS64500,Example,2026-01-01,1,0,тест\n"
                "мусор,IPv4,,,,,,\n")
    r = client.post("/api/subnets/import", headers=a,
                    files={"file": ("rows.csv", csv_text.encode(), "text/csv")},
                    data={"provider_id": pid, "list_id": lid})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["imported"] == 1 and body["skipped"] == 1 and body["errors"]
    row = _rows(a)[0]
    assert row["values"]["subnet"] == "203.0.113.0/24"
    assert row["values"]["asn"] == "AS64500"
    assert row["operators"]["mts"] is True and row["operators"]["beeline"] is False
    lst = client.get("/api/subnets", headers=a).json()["providers"][0]["lists"][0]
    ckey = next(c["key"] for c in lst["columns"] if c["title"] == "Комментарий")
    assert row["values"][ckey] == "тест"


def test_import_txt_ignores_comments():
    a = _auth()
    pid, lid = _mk_list(a)
    txt = ("# заголовок\n\n203.0.113.0/24\n"
           "198.51.100.0/24 — офис\n2001:db8::/32 # ipv6\n")
    r = client.post("/api/subnets/import", headers=a,
                    files={"file": ("list.txt", txt.encode(), "text/plain")},
                    data={"provider_id": pid, "list_id": lid})
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 3
    assert {x["values"]["subnet"] for x in _rows(a)} == {
        "203.0.113.0/24", "198.51.100.0/24", "2001:db8::/32"}


def test_import_creates_new_list():
    a = _auth()
    r = client.post("/api/subnets/import", headers=a,
                    files={"file": ("l.txt", b"203.0.113.0/24\n", "text/plain")})
    assert r.status_code == 200 and r.json()["imported"] == 1
    data = client.get("/api/subnets", headers=a).json()
    assert data["providers"][0]["name"] == "Импортированные"
    lst = data["providers"][0]["lists"][0]
    assert lst["name"].startswith("Импорт ") and len(lst["rows"]) == 1


def test_import_invalid_file_400():
    a = _auth()
    pid, lid = _mk_list(a)
    # битый json
    r = client.post("/api/subnets/import", headers=a,
                    files={"file": ("b.json", b"{not json", "application/json")},
                    data={"provider_id": pid, "list_id": lid})
    assert r.status_code == 400 and "JSON" in r.json()["detail"]
    # пустой файл
    r = client.post("/api/subnets/import", headers=a,
                    files={"file": ("e.txt", b"", "text/plain")})
    assert r.status_code == 400
    # без подсетей
    r = client.post("/api/subnets/import", headers=a,
                    files={"file": ("c.txt", "# только комментарий\n".encode(),
                                    "text/plain")})
    assert r.status_code == 400
    # неизвестный режим
    r = client.post("/api/subnets/import", headers=a,
                    files={"file": ("l.txt", b"1.2.3.0/24\n", "text/plain")},
                    data={"mode": "bogus"})
    assert r.status_code == 400
    # несуществующий список
    r = client.post("/api/subnets/import", headers=a,
                    files={"file": ("l.txt", b"1.2.3.0/24\n", "text/plain")},
                    data={"list_id": "nope"})
    assert r.status_code == 404


def test_import_limit_5000_rows_400():
    a = _auth()
    pid, lid = _mk_list(a)
    import app.api.subnets as api
    big = "\n".join(f"10.{i // 256}.{i % 256}.0/24" for i in range(api.MAX_IMPORT_ROWS + 1))
    r = client.post("/api/subnets/import", headers=a,
                    files={"file": ("big.txt", big.encode(), "text/plain")},
                    data={"provider_id": pid, "list_id": lid})
    assert r.status_code == 400 and "5000" in r.json()["detail"]


# ── rows с метаданными / batch-ячейки / import-json ────────────
def test_rows_with_metadata():
    a = _auth()
    pid, lid = _mk_list(a)
    r = client.post(f"/api/subnets/providers/{pid}/lists/{lid}/rows", headers=a,
                    json={"rows": [
                        {"subnet": "203.0.113.9/24", "operator": "mts",
                         "country": "RU", "asn": "AS64500"},
                        {"subnet": "198.51.100.0/24", "operators": {"beeline": False},
                         "comment": "офис"},
                    ]})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["added"] == ["203.0.113.0/24", "198.51.100.0/24"]
    assert body["errors"] == []
    rows = _rows(a)
    row = next(x for x in rows if x["values"]["subnet"] == "203.0.113.0/24")
    assert row["values"]["country"] == "RU"
    assert row["values"]["asn"] == "AS64500"
    assert row["operators"]["mts"] is True
    row2 = next(x for x in rows if x["values"]["subnet"] == "198.51.100.0/24")
    assert row2["operators"]["beeline"] is False
    assert row2["values"]["comment"] == "офис"
    # колонки автоматически не создаются
    lst = client.get("/api/subnets", headers=a).json()["providers"][0]["lists"][0]
    assert [c["key"] for c in lst["columns"]] == ["subnet", "ipver", "asn", "asnname", "date", "operators"]


def test_rows_old_format_still_works():
    a = _auth()
    pid, lid = _mk_list(a)
    r = client.post(f"/api/subnets/providers/{pid}/lists/{lid}/rows", headers=a,
                    json={"subnets": ["203.0.113.9/24", "2001:db8::/32", "мусор"]})
    assert r.status_code == 201
    body = r.json()
    assert body["added"] == ["203.0.113.0/24", "2001:db8::/32"]
    assert len(body["errors"]) == 1
    row = _rows(a)[0]
    assert row["values"]["subnet"] == "203.0.113.0/24"
    assert row["values"]["ipver"] == "IPv4"


def test_rows_body_requires_subnets_or_rows():
    a = _auth()
    pid, lid = _mk_list(a)
    r = client.post(f"/api/subnets/providers/{pid}/lists/{lid}/rows", headers=a, json={})
    assert r.status_code == 422
    r = client.post(f"/api/subnets/providers/{pid}/lists/{lid}/rows", headers=a,
                    json={"subnets": [], "rows": []})
    assert r.status_code == 422


def test_rows_unknown_operator_kept_as_is():
    a = _auth()
    pid, lid = _mk_list(a)
    r = client.post(f"/api/subnets/providers/{pid}/lists/{lid}/rows", headers=a,
                    json={"rows": [{"subnet": "203.0.113.0/24", "operator": "yota"}]})
    assert r.status_code == 201
    row = _rows(a)[0]
    assert row["values"]["operator"] == "yota"  # как есть, без падения
    assert row["operators"]["mts"] is False  # неизвестный оператор → все флаги False
    # битая строка в пачке не убивает остальные
    r = client.post(f"/api/subnets/providers/{pid}/lists/{lid}/rows", headers=a,
                    json={"rows": [{"subnet": "мусор", "country": "XX"},
                                   {"subnet": "198.51.100.0/24"}]})
    assert r.status_code == 201
    body = r.json()
    assert body["added"] == ["198.51.100.0/24"] and len(body["errors"]) == 1


def test_batch_update_cells():
    a = _auth()
    pid, lid = _mk_list(a)
    client.post(f"/api/subnets/providers/{pid}/lists/{lid}/rows", headers=a,
                json={"subnets": ["203.0.113.0/24", "198.51.100.0/24", "10.0.0.0/8"]})
    rows = _rows(a)
    r = client.patch(f"/api/subnets/providers/{pid}/lists/{lid}/rows/batch", headers=a,
                     json={"updates": [
                         {"row_id": rows[0]["id"], "col": "asn", "value": "AS64500"},
                         {"row_id": rows[1]["id"], "col": "asnname", "value": "Example"},
                         {"row_id": rows[2]["id"], "col": "date", "value": "2026-08-23"},
                     ]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["updated"] == 3 and body["skipped"] == 0
    assert body["errors"] == []
    fresh = _rows(a)
    by_id = {x["id"]: x for x in fresh}
    assert by_id[rows[0]["id"]]["values"]["asn"] == "AS64500"
    assert by_id[rows[1]["id"]]["values"]["asnname"] == "Example"
    assert by_id[rows[2]["id"]]["values"]["date"] == "2026-08-23"


def test_batch_update_broken_row_does_not_kill():
    a = _auth()
    pid, lid = _mk_list(a)
    client.post(f"/api/subnets/providers/{pid}/lists/{lid}/rows", headers=a,
                json={"subnets": ["203.0.113.0/24"]})
    rid = _rows(a)[0]["id"]
    r = client.patch(f"/api/subnets/providers/{pid}/lists/{lid}/rows/batch", headers=a,
                     json={"updates": [
                         {"row_id": "nope", "col": "asn", "value": "X"},
                         {"row_id": rid, "col": "asn", "value": "AS1"},
                         {"row_id": rid, "col": "", "value": "Y"},
                     ]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["updated"] == 1 and body["skipped"] == 2 and len(body["errors"]) == 2
    assert _rows(a)[0]["values"]["asn"] == "AS1"


def test_batch_update_limits_and_404():
    a = _auth()
    pid, lid = _mk_list(a)
    updates = [{"row_id": f"r{i}", "col": "asn", "value": "x"} for i in range(501)]
    r = client.patch(f"/api/subnets/providers/{pid}/lists/{lid}/rows/batch",
                     headers=a, json={"updates": updates})
    assert r.status_code == 422  # больше 500
    r = client.patch(f"/api/subnets/providers/{pid}/lists/{lid}/rows/batch",
                     headers=a, json={"updates": []})
    assert r.status_code == 422  # пусто
    r = client.patch("/api/subnets/providers/nope/lists/nope2/rows/batch",
                     headers=a, json={"updates": [{"row_id": "r", "col": "c"}]})
    assert r.status_code == 404


def test_import_json_rows_with_metadata():
    a = _auth()
    pid, lid = _mk_list(a)
    rows = [{"subnet": f"10.{i}.0.0/24", "country": "RU", "asn": f"AS{1000 + i}"}
            for i in range(10)]
    r = client.post(f"/api/subnets/providers/{pid}/lists/{lid}/import-json", headers=a,
                    json={"rows": rows})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["added"] == 10 and body["skipped"] == 0
    fresh = _rows(a)
    assert len(fresh) == 10
    assert fresh[0]["values"]["country"] == "RU"
    assert fresh[0]["values"]["asn"] == "AS1000"
    # колонки автоматически не создаются
    lst = client.get("/api/subnets", headers=a).json()["providers"][0]["lists"][0]
    assert [c["key"] for c in lst["columns"]] == ["subnet", "ipver", "asn", "asnname", "date", "operators"]


def test_import_json_skips_duplicates_and_bad_rows():
    a = _auth()
    pid, lid = _mk_list(a)
    r = client.post(f"/api/subnets/providers/{pid}/lists/{lid}/import-json", headers=a,
                    json={"rows": [
                        {"subnet": "203.0.113.0/24", "country": "RU"},
                        {"subnet": "203.0.113.0/24", "country": "DE"},  # дубль
                        {"subnet": "мусор"},                            # битая
                        {"subnet": "198.51.100.0/24", "asnname": "Example"},
                    ]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["added"] == 2 and body["skipped"] == 2
    assert len(body["errors"]) == 1
    fresh = _rows(a)
    by_subnet = {x["values"]["subnet"]: x for x in fresh}
    assert set(by_subnet) == {"203.0.113.0/24", "198.51.100.0/24"}
    assert by_subnet["198.51.100.0/24"]["values"]["asnname"] == "Example"
    # дубль против уже существующих строк
    r = client.post(f"/api/subnets/providers/{pid}/lists/{lid}/import-json", headers=a,
                    json={"rows": [{"subnet": "203.0.113.0/24"}]})
    assert r.json()["added"] == 0 and r.json()["skipped"] == 1
    # пустой rows — 422, нет списка — 404
    assert client.post(f"/api/subnets/providers/{pid}/lists/{lid}/import-json",
                       headers=a, json={"rows": []}).status_code == 422
    assert client.post("/api/subnets/providers/nope/lists/nope2/import-json",
                       headers=a, json={"rows": [{"subnet": "1.2.3.0/24"}]}).status_code == 404


# ── экспорт xlsx (Excel, группы по провайдеру) ──────────────────
def _xlsx_list(a, rows):
    """Список со строками {subnet, provider?, operator?} → (wb, ws)."""
    pid, lid = _mk_list(a)
    r = client.post(f"/api/subnets/providers/{pid}/lists/{lid}/rows",
                    headers=a, json={"rows": rows})
    assert r.status_code == 201, r.text
    r = client.get("/api/subnets/export", headers=a,
                   params={"provider_id": pid, "list_id": lid, "format": "xlsx"})
    assert r.status_code == 200, r.text
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    return wb, wb.active


def test_export_xlsx_ok_zip_magic():
    a = _auth()
    pid, lid = _mk_list(a)
    client.post(f"/api/subnets/providers/{pid}/lists/{lid}/rows", headers=a,
                json={"subnets": ["203.0.113.0/24"]})
    r = client.get("/api/subnets/export", headers=a,
                   params={"provider_id": pid, "list_id": lid, "format": "xlsx"})
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    assert ".xlsx" in r.headers["content-disposition"]
    assert r.content[:4] == b"PK\x03\x04"  # zip-магия xlsx
    # пустой список тоже валиден: только заголовок, без групп
    pid2, lid2 = _mk_list(a)
    r = client.get("/api/subnets/export", headers=a,
                   params={"list_id": lid2, "format": "xlsx"})
    assert r.status_code == 200 and r.content[:4] == b"PK\x03\x04"
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert wb.active.max_row == 1


def test_export_xlsx_requires_list_id():
    a = _auth()
    r = client.get("/api/subnets/export", headers=a, params={"format": "xlsx"})
    assert r.status_code == 400
    assert "list_id" in r.json()["detail"]


def test_export_xlsx_groups_by_provider():
    a = _auth()
    rows = [
        {"subnet": "203.0.113.0/24", "provider": "Ростелеком"},
        {"subnet": "198.51.100.0/24", "provider": "МТС"},
        {"subnet": "192.0.2.0/24", "provider": "Билайн"},
        {"subnet": "10.10.0.0/16", "provider": "МТС"},
        {"subnet": "2001:db8::/32"},  # без провайдера — в конец
    ]
    wb, ws = _xlsx_list(a, rows)
    # заголовок: табличные колонки, operators → 5 колонок с лейблами
    head = [ws.cell(1, c).value for c in range(1, 11)]
    assert head == ["subnet", "ipver", "asn", "asnname", "date",
                    "MTS", "Beeline", "МегаФон", "Tele2", "T-Mobile"]
    assert ws["A1"].font.bold
    assert ws.freeze_panes == "A2"
    # строки отсортированы по провайдеру: Билайн → МТС → Ростелеком → «—»
    assert [ws.cell(r, 1).value for r in range(2, 7)] == [
        "192.0.2.0/24", "198.51.100.0/24", "10.10.0.0/16",
        "203.0.113.0/24", "2001:db8::/32"]
    # группы по провайдеру: outline_level=1, hidden=False (сворачивание «+/-»)
    ranges = [(2, 2), (3, 4), (5, 5), (6, 6)]
    for start, end in ranges:
        for r in range(start, end + 1):
            dim = ws.row_dimensions[r]
            assert dim.outline_level == 1, f"строка {r} не сгруппирована"
            assert dim.hidden is False
    # за пределами данных групп нет
    assert ws.max_row == 6


def test_export_xlsx_values_match_source():
    a = _auth()
    rows = [
        {"subnet": "203.0.113.9/24", "provider": "МТС", "operator": "МТС"},
        {"subnet": "198.51.100.0/24", "provider": "Билайн",
         "operator": "Билайн + Tele2"},
    ]
    _, ws = _xlsx_list(a, rows)
    # первая строка данных — группа «Билайн» (сортировка по провайдеру)
    assert ws["A2"].value == "198.51.100.0/24"
    assert ws["B2"].value == "IPv4"
    # операторы: 1/0, как в CSV
    assert [ws.cell(2, c).value for c in range(6, 11)] == [0, 1, 0, 1, 0]
    assert [ws.cell(3, c).value for c in range(6, 11)] == [1, 0, 0, 0, 0]
    # провайдер в колонки таблицы не входит — он виден через группы:
    # строка 2 — отдельная группа «Билайн», строка 3 — «МТС»
    assert ws.row_dimensions[2].outline_level == 1
    assert ws.row_dimensions[3].outline_level == 1
