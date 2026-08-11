"""Wave-4 PR-6 — мосты: сборка outbound/правила, идемпотентность, удаление,
служебный пользователь, оркестрация create/delete."""
import asyncio
import uuid as _uuid

from fastapi.testclient import TestClient

from app.main import app
from app.services import bridges as svc

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"br-{_uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


BASE_OB = {"protocol": "vless", "tag": "proxy",
           "settings": {"vnext": [{"address": "de.example.com", "port": 443}]},
           "streamSettings": {"network": "tcp", "security": "reality"}}
CFG = {
    "outbounds": [
        {"tag": "proxy", "protocol": "vless"},
        {"tag": "direct", "protocol": "freedom"},
    ],
    "routing": {"rules": [{"protocol": ["bittorrent"], "outboundTag": "direct"}]},
}


# ── чистые функции ─────────────────────────────────────────────
def test_build_rule_only_documented_fields():
    rule = svc.build_rule("ab12", ["vless-tcp-in", "vless-ws-in"],
                          {"domain": ["doubleclick.net", "domain:ads.example"],
                           "ip": [], "protocol": ["http", "tls"],
                           "port": "443", "network": "tcp"})
    assert rule["outboundTag"] == "bridge-ab12"
    assert rule["ruleTag"] == "nai-bridge-ab12"
    assert rule["inboundTag"] == ["vless-tcp-in", "vless-ws-in"]
    assert rule["domain"] == ["doubleclick.net", "domain:ads.example"]
    assert rule["protocol"] == ["http", "tls"]
    assert rule["port"] == "443"
    assert rule["network"] == "tcp"
    assert "ip" not in rule and "type" not in rule      # нет недокументированного


def test_build_rule_empty_matchers_routes_all_inbound():
    rule = svc.build_rule("ab12", ["in-1"], {})
    assert set(rule.keys()) == {"outboundTag", "ruleTag", "inboundTag"}


def test_apply_is_idempotent_and_keeps_default_outbound():
    ob = svc.build_outbound(BASE_OB, "ab12")
    rule = svc.build_rule("ab12", ["in-1"], {})
    once = svc.apply_bridge_to_config(CFG, ob, rule)
    twice = svc.apply_bridge_to_config(once, svc.build_outbound(BASE_OB, "ab12"), rule)
    tags = [o["tag"] for o in twice["outbounds"]]
    assert tags.count("bridge-ab12") == 1            # не плодит дубликаты
    assert tags[0] == "proxy"                        # дефолтный первый outbound на месте
    rtags = [r.get("ruleTag") for r in twice["routing"]["rules"]]
    assert rtags[0] == "nai-bridge-ab12" and rtags.count("nai-bridge-ab12") == 1
    # правило моста — первым, битторрент-правило сохранилось
    assert len(twice["routing"]["rules"]) == 2


def test_strip_removes_only_bridge_bits():
    ob = svc.build_outbound(BASE_OB, "ab12")
    rule = svc.build_rule("ab12", ["in-1"], {})
    cfg = svc.apply_bridge_to_config(CFG, ob, rule)
    stripped = svc.strip_bridge_from_config(cfg, "ab12")
    assert [o["tag"] for o in stripped["outbounds"]] == ["proxy", "direct"]
    assert stripped["routing"]["rules"] == CFG["routing"]["rules"]


def test_pick_exit_outbound_by_address_then_fallback():
    obs = [{"tag": "direct", "protocol": "freedom"},
           BASE_OB,
           {"protocol": "trojan", "tag": "t",
            "settings": {"vnext": [{"address": "other.example.com", "port": 443}]}}]
    ob, matched = svc.pick_exit_outbound(obs, "de.example.com")
    assert matched and ob["protocol"] == "vless"
    ob2, matched2 = svc.pick_exit_outbound(obs, "missing.example.com")
    assert not matched2 and ob2["protocol"] == "vless"   # первый проксёвый


# ── служебный пользователь ─────────────────────────────────────
def test_ensure_service_user_creates_unlimited():
    created = {}

    class FakeClient:
        async def get_user_by_username(self, username):
            from app.services.remnawave_client import RemnavaveError
            raise RemnavaveError(404, "not found")

        async def list_internal_squads(self):
            return [{"uuid": "sq1"}, {"uuid": "sq2"}]

        async def create_user(self, body):
            created.update(body)
            return {"uuid": "u1", "shortUuid": "short-1", **body}

    user = asyncio.run(svc.ensure_service_user(FakeClient()))
    assert user["shortUuid"] == "short-1"
    assert created["username"] == svc.SERVICE_USERNAME
    assert created["trafficLimitBytes"] == 0                    # безлимит
    assert created["trafficLimitStrategy"] == "NO_RESET"
    assert created["expireAt"].startswith("2099")               # бессрочно
    assert created["activeInternalSquads"] == ["sq1", "sq2"]    # все сквады


def test_ensure_service_user_reuses_existing():
    class FakeClient:
        async def get_user_by_username(self, username):
            return {"uuid": "u1", "shortUuid": "short-1"}

    user = asyncio.run(svc.ensure_service_user(FakeClient()))
    assert user["uuid"] == "u1"


# ── оркестрация create/delete ──────────────────────────────────
class _FakePanel:
    """In-memory Remnawave для create/delete моста."""
    def __init__(self):
        self.base_url = "http://panel"
        self.profiles = {"p1": dict(CFG), "p2": dict(CFG)}
        self.user = None

    async def get_user_by_username(self, username):
        if self.user:
            return self.user
        from app.services.remnawave_client import RemnavaveError
        raise RemnavaveError(404, "nf")

    async def list_internal_squads(self):
        return [{"uuid": "sq1"}]

    async def create_user(self, body):
        self.user = {"uuid": "u1", "shortUuid": "short-1"}
        return self.user

    async def get_config_profile(self, uuid):
        return {"uuid": uuid, "config": self.profiles[uuid]}

    async def update_config_profile(self, uuid, config):
        self.profiles[uuid] = config
        return {"uuid": uuid, "config": config}


def test_create_and_delete_bridge(monkeypatch):
    async def fake_fetch(panel_url, short_uuid):
        assert short_uuid == "short-1"
        return [BASE_OB]

    monkeypatch.setattr(svc, "fetch_subscription_outbounds", fake_fetch)
    panel = _FakePanel()
    aid = f"acc-{_uuid.uuid4().hex[:8]}"

    rec = asyncio.run(svc.create_bridge(
        panel, name="EU→DE", exit_node={"uuid": "n1", "name": "DE", "address": "de.example.com"},
        inbound_tags=["in-1"], profile_uuids=["p1", "p2"],
        matchers={"domain": ["ads.example"], "ip": [], "protocol": [], "port": "", "network": ""},
        account_id=aid))

    assert rec["outbound_matched"] is True
    assert rec["applied_profiles"] == ["p1", "p2"]
    for p in ("p1", "p2"):
        tags = [o["tag"] for o in panel.profiles[p]["outbounds"]]
        assert f"bridge-{rec['id']}" in tags
        assert panel.profiles[p]["routing"]["rules"][0]["ruleTag"] == f"nai-bridge-{rec['id']}"
        assert panel.profiles[p]["routing"]["rules"][0]["domain"] == ["ads.example"]
    # запись в хранилище
    assert svc.get_bridge(rec["id"], aid)["name"] == "EU→DE"

    out = asyncio.run(svc.delete_bridge(panel, rec["id"], account_id=aid))
    assert out["ok"] and out["profile_errors"] == []
    for p in ("p1", "p2"):
        assert [o["tag"] for o in panel.profiles[p]["outbounds"]] == ["proxy", "direct"]
    assert svc.get_bridge(rec["id"], aid) is None


# ── роуты ──────────────────────────────────────────────────────
def test_routes_list_empty_and_404():
    a = _auth()
    r = client.get("/api/bridges", headers=a)
    assert r.status_code == 200 and r.json()["bridges"] == []
    r = client.delete("/api/bridges/nope", headers=a)
    # без настроенной панели панель не найдётся раньше, чем мост
    assert r.status_code in (400, 404)
