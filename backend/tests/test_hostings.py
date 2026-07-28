"""Wave-4 Plan A — «Хостинги» catalogue CRUD + isolation + validation."""
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.models.hostings import HostingBody

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


# ── Метрики хостинга (1..100, каждая опциональна) ──────────────
def test_metrics_roundtrip():
    a = _auth()
    r = client.post("/api/hostings", headers=a, json={
        "name": "Scored",
        "metrics": {"price": 80, "quality": 92.5, "loyalty": 70,
                    "fairuse": 55, "panel": 40, "ru_access": 12.5},
    })
    assert r.status_code == 201, r.text
    m = r.json()["metrics"]
    assert m == {"price": 80.0, "quality": 92.5, "loyalty": 70.0, "fairuse": 55.0,
                 "panel": 40.0, "ru_access": 12.5, "fairuse_hidden": False}
    assert client.get("/api/hostings", headers=a).json()[0]["metrics"]["quality"] == 92.5


def test_metrics_bounds():
    a = _auth()
    ok = client.post("/api/hostings", headers=a, json={
        "name": "Edges", "metrics": {"price": 1.0, "quality": 100.0},
    })
    assert ok.status_code == 201, ok.text
    assert ok.json()["metrics"]["price"] == 1.0
    assert ok.json()["metrics"]["quality"] == 100.0
    # not-scored stays not-scored, it is not a zero
    assert ok.json()["metrics"]["loyalty"] is None

    for bad in ({"price": 0.9}, {"quality": 100.1}):
        assert client.post("/api/hostings", headers=a,
                           json={"name": "Bad", "metrics": bad}).status_code == 422


def test_metrics_rounded_to_one_decimal():
    a = _auth()
    r = client.post("/api/hostings", headers=a, json={
        "name": "Rounded", "metrics": {"price": 73.46, "quality": 12.34},
    })
    assert r.status_code == 201, r.text
    assert r.json()["metrics"]["price"] == 73.5
    assert r.json()["metrics"]["quality"] == 12.3


def test_metrics_default_for_a_record_without_them():
    """Cards stored before metrics existed have no such key — the store hands the
    raw JSON back, so the model must default instead of 422-ing."""
    legacy = {"name": "Old", "website": "", "tariffs": [], "locations": []}
    assert HostingBody(**legacy).metrics.price is None

    a = _auth()
    r = client.post("/api/hostings", headers=a, json=legacy)
    assert r.status_code == 201, r.text
    assert r.json()["metrics"] == {"price": None, "quality": None, "loyalty": None,
                                   "fairuse": None, "panel": None, "ru_access": None,
                                   "fairuse_hidden": False}


def test_metrics_fairuse_hidden_persists():
    a = _auth()
    r = client.post("/api/hostings", headers=a, json={
        "name": "NoFairuse", "metrics": {"price": 60, "fairuse_hidden": True},
    })
    assert r.status_code == 201, r.text
    hid = r.json()["id"]
    got = client.get("/api/hostings", headers=a).json()[0]["metrics"]
    assert got["fairuse_hidden"] is True and got["fairuse"] is None

    client.put(f"/api/hostings/{hid}", headers=a, json={
        "name": "NoFairuse", "metrics": {"price": 60, "fairuse": 30},
    })
    assert client.get("/api/hostings", headers=a).json()[0]["metrics"]["fairuse_hidden"] is False


# ── Произвольные заметки хостинга + заметка тарифа + признак API ──
def test_note_fields_roundtrip():
    a = _auth()
    r = client.post("/api/hostings", headers=a, json={
        "name": "Noted",
        "note_fields": [{"topic": "Оплата", "text": "Только карты РФ"},
                        {"topic": "", "text": "без темы"},
                        {"topic": "только тема", "text": ""}],
    })
    assert r.status_code == 201, r.text
    nf = r.json()["note_fields"]
    assert nf == [{"topic": "Оплата", "text": "Только карты РФ"},
                  {"topic": "", "text": "без темы"},
                  {"topic": "только тема", "text": ""}]
    assert client.get("/api/hostings", headers=a).json()[0]["note_fields"][0]["topic"] == "Оплата"


def test_empty_note_field_is_dropped():
    """An untouched «добавить поле» row must not be persisted."""
    a = _auth()
    r = client.post("/api/hostings", headers=a, json={
        "name": "Blanks",
        "note_fields": [{"topic": "  ", "text": " \n "}, {"topic": "Ок", "text": ""}],
    })
    assert r.status_code == 201, r.text
    assert r.json()["note_fields"] == [{"topic": "Ок", "text": ""}]


def test_note_fields_capped_at_30():
    a = _auth()
    r = client.post("/api/hostings", headers=a, json={
        "name": "Many",
        "note_fields": [{"topic": f"t{i}", "text": "x"} for i in range(40)],
    })
    assert r.status_code == 201, r.text
    nf = r.json()["note_fields"]
    assert len(nf) == 30 and nf[-1]["topic"] == "t29"


def test_note_field_lengths_trimmed():
    a = _auth()
    r = client.post("/api/hostings", headers=a, json={
        "name": "Long",
        "note_fields": [{"topic": "т" * 200, "text": "x" * 6000}],
    })
    assert r.status_code == 201, r.text
    nf = r.json()["note_fields"][0]
    assert len(nf["topic"]) == 80 and len(nf["text"]) == 5000


def test_note_field_keeps_line_breaks():
    """Unlike a tag, a note is multi-line — CR/LF inside the text survive, while
    the single-line topic collapses them."""
    a = _auth()
    r = client.post("/api/hostings", headers=a, json={
        "name": "Multiline",
        "note_fields": [{"topic": "Под\r\nдержка", "text": "  первая\nвторая\n\nтретья  "}],
    })
    assert r.status_code == 201, r.text
    nf = r.json()["note_fields"][0]
    assert nf["topic"] == "Под держка"
    assert nf["text"] == "первая\nвторая\n\nтретья"


def test_tariff_note_roundtrip():
    a = _auth()
    r = client.post("/api/hostings", headers=a, json={
        "name": "WithTariffNote",
        "tariffs": [{"name": "CX22", "price": 5.5, "note": " берут\nтолько год  "},
                    {"name": "CX32", "price": 9}],
    })
    assert r.status_code == 201, r.text
    t = r.json()["tariffs"]
    assert t[0]["note"] == "берут\nтолько год"     # trimmed, line break kept
    assert t[1]["note"] == ""                       # not given → empty
    assert len(client.post("/api/hostings", headers=a, json={
        "name": "LongNote", "tariffs": [{"name": "X", "note": "n" * 3000}],
    }).json()["tariffs"][0]["note"]) == 2000


def test_has_api_tristate():
    a = _auth()
    for value in (True, False, None):
        r = client.post("/api/hostings", headers=a, json={"name": "Api", "has_api": value})
        assert r.status_code == 201, r.text
        assert r.json()["has_api"] is value


def test_record_without_the_new_keys_reads_with_defaults():
    """Cards stored before these fields existed have no such keys — the store
    hands the raw JSON back, so the model must default instead of 422-ing."""
    legacy = {"name": "Old", "website": "", "tariffs": [{"name": "legacy", "price": 3}],
              "locations": []}
    parsed = HostingBody(**legacy)
    assert parsed.note_fields == [] and parsed.has_api is None
    assert parsed.tariffs[0].note == ""

    a = _auth()
    r = client.post("/api/hostings", headers=a, json=legacy)
    assert r.status_code == 201, r.text
    assert r.json()["note_fields"] == [] and r.json()["has_api"] is None


def test_bs_subnets_round_trip_and_pruning():
    """Таблица «БС подсети»: строки сохраняются, пустая от нетронутого
    «Добавить» отсеивается, ячейка остаётся однострочной."""
    h = _auth()
    body = {"name": "BS", "bs_subnets": [
        {"network": "10.0.0.0/24", "asn": "AS12345", "org": "Пример  ЛТД",
         "checked_at": "2026-07-01", "response": "отвечает,\n20 ms"},
        {"network": "", "asn": "", "org": "", "checked_at": "", "response": ""},
    ]}
    r = client.post("/api/hostings", headers=h, json=body)
    assert r.status_code == 201, r.text

    rows = client.get("/api/hostings", headers=h).json()[0]["bs_subnets"]
    assert len(rows) == 1, "пустая строка не должна сохраняться"
    assert rows[0]["network"] == "10.0.0.0/24" and rows[0]["asn"] == "AS12345"
    assert rows[0]["org"] == "Пример ЛТД"          # пробелы схлопнуты
    assert rows[0]["response"] == "отвечает, 20 ms"  # перевод строки — тоже


def test_bs_subnets_default_for_old_records():
    h = _auth()
    client.post("/api/hostings", headers=h, json={"name": "Без таблицы"})
    assert client.get("/api/hostings", headers=h).json()[0]["bs_subnets"] == []
