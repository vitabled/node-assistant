"""Wave-5 Plan I — AI system-prompt presets: builtin/user CRUD, gating,
active-preset resolution + build_system integration."""
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.models.settings import AiConfig
from app.services import ai_agent, prompt_presets_store as presets

client = TestClient(app)


def _register():
    login = f"aip-{uuid.uuid4().hex[:8]}"
    r = client.post("/api/auth/register", json={"login": login, "password": "pw"})
    return r.json()["id"], {"Authorization": f"Bearer {r.json()['token']}"}


def test_builtins_present_and_readonly():
    _aid, h = _register()
    lst = client.get("/api/ai/prompts", headers=h).json()
    ids = {p["id"]: p for p in lst}
    assert "default" in ids and "precise" in ids and "cloudflare-agent-setup" in ids
    assert all(ids[i]["builtin"] for i in ("default", "precise", "cloudflare-agent-setup"))
    # cloudflare is a vendored placeholder → unavailable
    assert ids["cloudflare-agent-setup"]["unavailable"] is True
    assert ids["cloudflare-agent-setup"]["license"] == "CC-BY-4.0"
    # builtin cannot be edited or deleted
    assert client.put("/api/ai/prompts/default", headers=h,
                      json={"name": "x", "text": "y"}).status_code == 400
    assert client.delete("/api/ai/prompts/default", headers=h).status_code == 400


def test_user_crud_fork_and_isolation():
    _a, ha = _register()
    r = client.post("/api/ai/prompts", headers=ha, json={"name": "mine", "text": "будь краток"})
    assert r.status_code == 201
    pid = r.json()["id"]
    assert client.put(f"/api/ai/prompts/{pid}", headers=ha,
                      json={"name": "mine2", "text": "ещё короче"}).status_code == 200
    # fork a builtin → new editable user preset with copied text
    fk = client.post("/api/ai/prompts/precise/fork", headers=ha)
    assert fk.status_code == 201 and fk.json()["builtin"] is False and fk.json()["text"]
    # account B sees builtins but not A's user presets
    _b, hb = _register()
    b_ids = {p["id"] for p in client.get("/api/ai/prompts", headers=hb).json()}
    assert pid not in b_ids and "default" in b_ids
    assert client.delete(f"/api/ai/prompts/{pid}", headers=ha).status_code == 204


def test_resolve_and_build_system():
    aid, _h = _register()
    suffix = "list_rules"  # substring of the tooling suffix
    # default fallback when empty / unknown
    assert presets.resolve_active_text("", aid) == presets.resolve_active_text("default", aid)
    assert presets.resolve_active_text("does-not-exist", aid) == presets.resolve_active_text("default", aid)
    # unavailable preset falls back to default text
    assert presets.resolve_active_text("cloudflare-agent-setup", aid) == presets.resolve_active_text("default", aid)
    # a user preset resolves to its own text, and build_system appends the suffix
    p = presets.create_preset("cust", "СИСТЕМНЫЙ ТЕКСТ ПРЕСЕТА", account_id=aid)
    cfg = AiConfig(active_preset_id=p["id"])
    sysmsg = ai_agent.build_system(aid, cfg)
    assert "СИСТЕМНЫЙ ТЕКСТ ПРЕСЕТА" in sysmsg and suffix in sysmsg
    # default config → default text + suffix
    assert suffix in ai_agent.build_system(aid, AiConfig())
