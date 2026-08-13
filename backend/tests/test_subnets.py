"""Wave-5 PR-5 — «Подсети»: провайдеры/списки/строки/столбцы, обогащение."""
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
