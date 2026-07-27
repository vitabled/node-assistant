"""Wave-4 Plan A — «Хостинги» catalogue CRUD + isolation + validation."""
import uuid

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"hg-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_requires_auth():
    assert client.get("/api/hostings").status_code == 401


def test_crud_and_isolation():
    a = _auth()
    b = _auth()
    assert client.get("/api/hostings", headers=a).json() == []

    body = {
        "name": "Hetzner", "website": "https://hetzner.com",
        "features": "BBR, IPv6", "notes": "хороший",
        "tariffs": [{"name": "CX22", "specs": "2 vCPU / 4 GB", "price": 5.5, "currency": "EUR", "period": "mo"}],
        "locations": [{"city": "Falkenstein", "country_code": "DE", "lat": 50.5, "lng": 12.4}],
    }
    r = client.post("/api/hostings", headers=a, json=body)
    assert r.status_code == 201
    hid = r.json()["id"]
    assert r.json()["name"] == "Hetzner" and r.json()["created_at"] > 0

    lst = client.get("/api/hostings", headers=a).json()
    assert len(lst) == 1 and lst[0]["tariffs"][0]["price"] == 5.5
    assert lst[0]["locations"][0]["country_code"] == "DE"

    # per-account isolation
    assert client.get("/api/hostings", headers=b).json() == []

    r = client.put(f"/api/hostings/{hid}", headers=a, json={**body, "name": "Hetzner Cloud"})
    assert r.status_code == 200 and r.json()["name"] == "Hetzner Cloud" and r.json()["id"] == hid

    assert client.delete(f"/api/hostings/{hid}", headers=a).status_code == 204
    assert client.delete(f"/api/hostings/{hid}", headers=a).status_code == 404
    assert client.get("/api/hostings", headers=a).json() == []


def test_validation():
    a = _auth()
    assert client.post("/api/hostings", headers=a, json={"name": ""}).status_code == 422
    # lat out of range
    assert client.post("/api/hostings", headers=a,
                       json={"name": "X", "locations": [{"lat": 200}]}).status_code == 422


def test_update_missing_404():
    a = _auth()
    assert client.put("/api/hostings/nope", headers=a, json={"name": "X"}).status_code == 404


# ── Wave-7 Plan D Ф1: network channel width on a tariff ────────
def test_tariff_bandwidth_roundtrip():
    a = _auth()
    r = client.post("/api/hostings", headers=a, json={
        "name": "Hetzner",
        "tariffs": [{"name": "CX22", "bandwidth": "1 Гбит/с, 20 ТБ", "price": 5.5}],
    })
    assert r.status_code == 201
    assert r.json()["tariffs"][0]["bandwidth"] == "1 Гбит/с, 20 ТБ"
    assert client.get("/api/hostings", headers=a).json()[0]["tariffs"][0]["bandwidth"] == "1 Гбит/с, 20 ТБ"


def test_tariff_without_bandwidth_still_reads():
    """Documents already stored before Ф1 have no `bandwidth` key — they must
    keep loading, with the field defaulting to empty rather than 422-ing."""
    a = _auth()
    r = client.post("/api/hostings", headers=a, json={
        "name": "Old", "tariffs": [{"name": "legacy", "specs": "2 vCPU", "price": 3}],
    })
    assert r.status_code == 201
    assert r.json()["tariffs"][0]["bandwidth"] == ""


# ── Wave-8 §1/§6: tags + ASN ───────────────────────────────────
def test_tags_normalised():
    a = _auth()
    r = client.post("/api/hostings", headers=a, json={
        "name": "Tagged",
        # dupes, whitespace, CR/LF, over-length, plus a fill past the 10-tag cap
        "tags": ["  vps ", "vps", "de\r\nrack", "x" * 40] + [f"t{i}" for i in range(12)],
    })
    assert r.status_code == 201
    tags = r.json()["tags"]
    assert "vps" in tags and tags.count("vps") == 1          # trimmed + deduped
    assert "de rack" in tags                                  # CR/LF → space
    assert all(len(t) <= 24 for t in tags)                    # length cap
    assert len(tags) <= 10                                    # count cap


def test_tags_pool_endpoint():
    a = _auth()
    client.post("/api/hostings", headers=a, json={"name": "A", "tags": ["ru", "budget"]})
    client.post("/api/hostings", headers=a, json={"name": "B", "tags": ["budget", "eu"]})
    pool = client.get("/api/hostings/tags", headers=a).json()
    assert pool == ["budget", "eu", "ru"]                      # sorted union, deduped
    # isolation: another account's pool is empty
    assert client.get("/api/hostings/tags", headers=_auth()).json() == []


def test_asns_roundtrip():
    a = _auth()
    r = client.post("/api/hostings", headers=a, json={
        "name": "Sel",
        "asns": [{"number": 49505, "name": "Selectel", "website": "https://selectel.ru"}],
    })
    assert r.status_code == 201
    asn = r.json()["asns"][0]
    assert asn["number"] == 49505 and asn["name"] == "Selectel"
    # negative ASN rejected
    assert client.post("/api/hostings", headers=a,
                       json={"name": "X", "asns": [{"number": -1}]}).status_code == 422


def test_media_ids_round_trip():
    """Hosting cards carry ids from the shared media store, never the bytes."""
    h = _auth()
    r = client.post("/api/hostings", headers=h,
                    json={"name": "WithPics", "media": ["abc123", "def456"]})
    assert r.status_code == 201, r.text
    hid = r.json()["id"]
    got = client.get("/api/hostings", headers=h).json()[0]
    assert got["media"] == ["abc123", "def456"]

    client.put(f"/api/hostings/{hid}", headers=h, json={"name": "WithPics", "media": []})
    assert client.get("/api/hostings", headers=h).json()[0]["media"] == []
