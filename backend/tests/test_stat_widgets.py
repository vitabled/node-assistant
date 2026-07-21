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
    assert client.get("/api/stats/users/widgets", headers=h).json() == {"layout": []}
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
    assert client.get("/api/stats/users/widgets", headers=b).json() == {"layout": []}


def test_validation():
    h = _auth()
    assert client.put("/api/stats/users/widgets", headers=h,
                      json={"layout": [{"instance_id": "a", "kind": "bogus", "w": 1, "order": 0}]}).status_code == 422
    assert client.put("/api/stats/users/widgets", headers=h,
                      json={"layout": [{"instance_id": "a", "kind": "node-load", "w": 3, "order": 0}]}).status_code == 422
