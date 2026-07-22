"""Wave-5 Plan G — stats widget layout store: default/roundtrip/isolation/validation."""
import uuid

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register", json={"login": f"w-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_default_empty_and_roundtrip():
    h = _auth()
    assert client.get("/api/stats/users/widgets", headers=h).json()["layout"] == []
    layout = [
        {"instance_id": "a", "kind": "node-load", "w": 2, "order": 0, "settings": {"hours": 168}},
        {"instance_id": "b", "kind": "top-users", "w": 1, "order": 1, "settings": {}},
    ]
    assert client.put("/api/stats/users/widgets", headers=h, json={"layout": layout}).status_code == 200
    got = client.get("/api/stats/users/widgets", headers=h).json()["layout"]
    assert [w["instance_id"] for w in got] == ["a", "b"] and got[0]["w"] == 2


def test_isolation():
    a = _auth()
    client.put("/api/stats/users/widgets", headers=a,
               json={"layout": [{"instance_id": "x", "kind": "migrations", "w": 1, "order": 0, "settings": {}}]})
    b = _auth()
    assert client.get("/api/stats/users/widgets", headers=b).json()["layout"] == []


def test_validation():
    h = _auth()
    assert client.put("/api/stats/users/widgets", headers=h,
                      json={"layout": [{"instance_id": "a", "kind": "bogus", "w": 1, "order": 0}]}).status_code == 422
    assert client.put("/api/stats/users/widgets", headers=h,
                      json={"layout": [{"instance_id": "a", "kind": "node-load", "w": 3, "order": 0}]}).status_code == 422


# ── Волна 6, План B Ф1: набор скрытых серверов ──

def _put(h, body):
    return client.put("/api/stats/users/widgets", headers=h, json=body)


def test_fresh_account_has_an_empty_hidden_set():
    a = _auth()
    assert client.get("/api/stats/users/widgets", headers=a).json()["hidden"] == {
        "nodes": {}, "checker": {}
    }


def test_hidden_round_trips_both_axes():
    a = _auth()
    hidden = {
        "nodes": {"uuid-1": "de-1", "uuid-2": "nl-2"},
        "checker": {"local": {"n1": "node-1"}, "abc123": {"n9": "remote-9"}},
    }
    assert _put(a, {"layout": [], "hidden": hidden}).status_code == 200
    assert client.get("/api/stats/users/widgets", headers=a).json()["hidden"] == hidden


def test_document_written_before_hidden_existed_still_reads():
    """Документы старой версии не имеют ключа `hidden` — GET не должен падать."""
    a = _auth()
    _put(a, {"layout": [{"instance_id": "w1", "kind": "node-load", "w": 1, "order": 0}]})
    # Эмулируем старый документ, выкинув ключ на уровне стора.
    from app.services import storage as st
    from app.services import accounts
    aid = client.get("/api/auth/me", headers=a).json()["id"]
    st.save_stat_widgets({"layout": [{"instance_id": "w1", "kind": "node-load", "w": 1,
                                      "order": 0, "settings": {}}]}, account_id=aid)
    r = client.get("/api/stats/users/widgets", headers=a).json()
    assert r["hidden"] == {"nodes": {}, "checker": {}}
    assert len(r["layout"]) == 1


def test_hidden_rejects_over_the_count_limits():
    a = _auth()
    too_many = {f"u{i}": "n" for i in range(201)}
    assert _put(a, {"layout": [], "hidden": {"nodes": too_many}}).status_code == 422
    assert _put(a, {"layout": [], "hidden": {
        "checker": {f"c{i}": {} for i in range(21)}}}).status_code == 422
    assert _put(a, {"layout": [], "hidden": {
        "checker": {"local": too_many}}}).status_code == 422


def test_hidden_truncates_a_long_name_instead_of_rejecting():
    a = _auth()
    _put(a, {"layout": [], "hidden": {"nodes": {"u1": "x" * 200}}})
    assert len(client.get("/api/stats/users/widgets", headers=a).json()["hidden"]["nodes"]["u1"]) == 64


def test_hidden_is_isolated_between_accounts():
    a = _auth()
    _put(a, {"layout": [], "hidden": {"nodes": {"u1": "secret-node"}}})
    b = _auth()
    assert client.get("/api/stats/users/widgets", headers=b).json()["hidden"] == {
        "nodes": {}, "checker": {}
    }


def test_put_without_hidden_clears_it_full_replace():
    """Контракт full-replace: тело без `hidden` обнуляет набор."""
    a = _auth()
    _put(a, {"layout": [], "hidden": {"nodes": {"u1": "n"}}})
    _put(a, {"layout": []})
    assert client.get("/api/stats/users/widgets", headers=a).json()["hidden"]["nodes"] == {}
