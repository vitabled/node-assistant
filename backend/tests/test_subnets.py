"""Wave-5 PR-5 — «Подсети»: провайдеры/списки/строки/столбцы, обогащение,
импорт/экспорт (json/csv/txt)."""
import json as _json
import uuid as _uuid

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
