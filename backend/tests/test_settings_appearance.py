"""Wave-5 Plan B (Ф3) — per-account appearance persistence."""
import uuid

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"ap-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_appearance_defaults_present():
    h = _auth()
    ap = client.get("/api/settings", headers=h).json()["appearance"]
    assert ap == {
        "skin": "apple", "mode": "system", "accent": "blue",
        "density": "comfortable", "animations": True, "neon_glow": True,
    }


def test_save_and_read_back():
    h = _auth()
    body = {"skin": "neon", "mode": "dark", "accent": "magenta",
            "density": "compact", "animations": False, "neon_glow": True}
    assert client.post("/api/settings/appearance", json=body, headers=h).status_code == 200
    assert client.get("/api/settings", headers=h).json()["appearance"] == body


def test_invalid_values_rejected():
    h = _auth()
    for bad in ({"skin": "hologram"}, {"mode": "sepia"}, {"accent": "chartreuse"},
                {"density": "roomy"}):
        r = client.post("/api/settings/appearance", json={**bad}, headers=h)
        assert r.status_code == 422, (bad, r.text)


def test_isolation_between_accounts():
    a = _auth()
    client.post("/api/settings/appearance",
                json={"skin": "neon", "mode": "dark", "accent": "lime",
                      "density": "compact", "animations": False, "neon_glow": False},
                headers=a)
    b = _auth()
    assert client.get("/api/settings", headers=b).json()["appearance"]["skin"] == "apple"
