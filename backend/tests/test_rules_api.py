"""HTTP tests for api/rules.py — account-gated CRUD (+ isolation + secret
masking), the dry-run `test` endpoint, and the HMAC-verified webhook receiver."""

import asyncio
import hashlib
import hmac
import json
import time
import uuid

from fastapi.testclient import TestClient

from app.config import settings
from app.services import accounts, rules_store, telegram
from app.api import rules as rules_api
from app.main import app

client = TestClient(app)

_TG = {"type": "xray_down", "params": {"minutes": 5}}


def _auth():
    login = f"ru-{uuid.uuid4().hex[:8]}"
    r = client.post("/api/auth/register", json={"login": login, "password": "pw-1"})
    body = r.json()
    return {"Authorization": f"Bearer {body['token']}"}, body["id"]


def _rule_body(**over):
    b = {
        "name": "notify",
        "enabled": False,
        "trigger": _TG,
        "conditions": [],
        "actions": [
            {
                "type": "telegram",
                "params": {
                    "bot_token": "123456:REALtoken_aaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "chat_id": "42",
                    "text": "down $node",
                },
            }
        ],
        "cooldown_sec": 300,
        "dry_run": False,
    }
    b.update(over)
    return b


# ── gating ────────────────────────────────────────────────────
def test_rules_routes_require_account():
    assert client.get("/api/rules").status_code == 401
    assert client.post("/api/rules", json=_rule_body()).status_code == 401


# ── CRUD + masking ────────────────────────────────────────────
def test_create_masks_bot_token_and_vaults_it():
    h, aid = _auth()
    r = client.post("/api/rules", headers=h, json=_rule_body())
    assert r.status_code == 201
    rule = r.json()
    params = rule["actions"][0]["params"]
    assert params["bot_token"] == rules_store.MASK  # masked in response
    ref = params["token_ref"]
    assert ref and rules_store.read_secret(ref, aid).startswith("123456:")  # vaulted

    # Plaintext never lands in rules.json.
    raw = (accounts.data_dir(aid) / "rules.json").read_text(encoding="utf-8")
    assert "REALtoken" not in raw
    assert ref in raw


def test_crud_lifecycle():
    h, _ = _auth()
    rid = client.post("/api/rules", headers=h, json=_rule_body()).json()["id"]
    # patch enabled
    p = client.patch(f"/api/rules/{rid}", headers=h, json={"enabled": True})
    assert p.status_code == 200 and p.json()["enabled"] is True
    assert client.get("/api/rules", headers=h).json()[0]["enabled"] is True
    # delete
    assert client.delete(f"/api/rules/{rid}", headers=h).status_code == 204
    assert client.get("/api/rules", headers=h).json() == []
    assert (
        client.patch(
            f"/api/rules/{rid}", headers=h, json={"enabled": False}
        ).status_code
        == 404
    )


def test_rules_isolated_between_accounts():
    a, _ = _auth()
    b, _ = _auth()
    client.post("/api/rules", headers=a, json=_rule_body(name="A-rule"))
    assert any(
        x["name"] == "A-rule" for x in client.get("/api/rules", headers=a).json()
    )
    assert client.get("/api/rules", headers=b).json() == []


def test_invalid_trigger_type_rejected():
    h, _ = _auth()
    r = client.post(
        "/api/rules",
        headers=h,
        json=_rule_body(trigger={"type": "bogus", "params": {}}),
    )
    assert r.status_code == 422


# ── dry-run test endpoint ─────────────────────────────────────
def test_test_endpoint_dry_runs_without_sending(monkeypatch):
    h, _ = _auth()
    sent = {"n": 0}

    async def _send(*a, **k):
        sent["n"] += 1
        return {"ok": True}

    monkeypatch.setattr(telegram, "send_message", _send)
    rid = client.post("/api/rules", headers=h, json=_rule_body(enabled=True)).json()[
        "id"
    ]
    r = client.post(f"/api/rules/{rid}/test", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["evaluation"]["should_fire"] is True  # fixture xray_down 6 min
    assert body["plan"][0]["dry_run"] is True
    assert body["plan"][0]["plan"]["bot_token"] == rules_store.MASK
    assert sent["n"] == 0  # nothing actually sent


def test_test_endpoint_404_for_missing_rule():
    h, _ = _auth()
    assert client.post("/api/rules/nope/test", headers=h).status_code == 404


def test_draft_test_endpoint_does_not_persist(monkeypatch):
    """POST /api/rules/test dry-runs a rule BODY without creating a rule or
    vaulting its token (fixes the orphan-on-cancel bug in the UI)."""
    h, aid = _auth()
    sent = {"n": 0}

    async def _send(*a, **k):
        sent["n"] += 1
        return {"ok": True}

    monkeypatch.setattr(telegram, "send_message", _send)
    r = client.post("/api/rules/test", headers=h, json=_rule_body(enabled=False))
    assert r.status_code == 200
    body = r.json()
    assert body["evaluation"]["should_fire"] is True  # fixture xray_down 6 min
    # The draft's plaintext token is stripped from the plan (masked to ""/MASK) —
    # never echoed back.
    assert body["plan"][0]["plan"]["bot_token"] in ("", rules_store.MASK)
    assert "REALtoken" not in json.dumps(body)
    assert sent["n"] == 0  # nothing sent
    assert client.get("/api/rules", headers=h).json() == []  # NO rule persisted
    # And no secret vaulted: the account's secrets db has no rows for this draft.
    raw = accounts.data_dir(aid) / "rules.json"
    assert not raw.exists() or raw.read_text(encoding="utf-8").strip() in ("[]", "")


# ── webhook receiver (HMAC) ───────────────────────────────────
def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_webhook_rejects_bad_signature(monkeypatch):
    monkeypatch.setattr(settings, "webhook_secret_header", "sekret")
    body = json.dumps(
        {"event": "node.connection_lost", "scope": "node", "data": {}}
    ).encode()
    r = client.post(
        "/api/webhooks/remnawave",
        content=body,
        headers={"X-Remnawave-Signature": "deadbeef"},
    )
    assert r.status_code == 401


def test_webhook_rejects_when_secret_unset(monkeypatch):
    monkeypatch.setattr(settings, "webhook_secret_header", "")
    body = b"{}"
    r = client.post(
        "/api/webhooks/remnawave",
        content=body,
        headers={"X-Remnawave-Signature": _sign("x", body)},
    )
    assert r.status_code == 401


def test_webhook_valid_signature_runs_matching_rule(monkeypatch):
    monkeypatch.setattr(settings, "webhook_secret_header", "sekret")
    calls = []

    async def _send(bot_token, chat_id, text):
        calls.append((chat_id, text))
        return {"ok": True}

    monkeypatch.setattr(telegram, "send_message", _send)

    h, _ = _auth()
    chat = uuid.uuid4().hex[:10]  # unique so we can find our own fire amid all accounts
    rule = _rule_body(
        enabled=True,
        trigger={"type": "webhook", "params": {"event": "node.connection_lost"}},
        actions=[
            {
                "type": "telegram",
                "params": {
                    "bot_token": "123456:REALtoken_wwwwwwwwwwwwwwwwwwwwwwwwwwww",
                    "chat_id": chat,
                    "text": "lost $node",
                },
            }
        ],
    )
    client.post("/api/rules", headers=h, json=rule)

    body = json.dumps(
        {"event": "node.connection_lost", "scope": "node", "data": {"nodeName": "de-9"}}
    ).encode()
    r = client.post(
        "/api/webhooks/remnawave",
        content=body,
        headers={"X-Remnawave-Signature": _sign("sekret", body)},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert (chat, "lost de-9") in calls  # our rule fired, placeholder rendered


def test_webhook_rejects_stale_timestamp(monkeypatch):
    # A signed body whose (HMAC-covered) timestamp is far in the past = replay.
    monkeypatch.setattr(settings, "webhook_secret_header", "sekret")
    body = json.dumps(
        {"event": "node.connection_lost", "scope": "node", "timestamp": 1, "data": {}}
    ).encode()
    r = client.post(
        "/api/webhooks/remnawave",
        content=body,
        headers={"X-Remnawave-Signature": _sign("sekret", body)},
    )
    assert r.status_code == 401


def test_webhook_accepts_fresh_timestamp(monkeypatch):
    monkeypatch.setattr(settings, "webhook_secret_header", "sekret")
    body = json.dumps(
        {
            "event": "node.connection_lost",
            "scope": "node",
            "timestamp": int(time.time()),
            "data": {},
        }
    ).encode()
    r = client.post(
        "/api/webhooks/remnawave",
        content=body,
        headers={"X-Remnawave-Signature": _sign("sekret", body)},
    )
    assert r.status_code == 200


# ── secret GC on update ───────────────────────────────────────
def test_update_removing_telegram_action_gcs_secret():
    h, aid = _auth()
    rid = client.post("/api/rules", headers=h, json=_rule_body()).json()["id"]
    ref = client.get("/api/rules", headers=h).json()[0]["actions"][0]["params"][
        "token_ref"
    ]
    assert rules_store.read_secret(ref, aid) is not None  # vaulted on create
    # Replace the actions list with a non-telegram action → old token_ref orphaned.
    client.patch(
        f"/api/rules/{rid}",
        headers=h,
        json={"actions": [{"type": "hide_hosts", "params": {"node_uuid": "N1"}}]},
    )
    assert rules_store.read_secret(ref, aid) is None  # GC'd, no longer referenced


# ── loop: per-node fire + per-node cooldown ───────────────────
def test_loop_fires_per_down_node_and_cooldown_is_per_node(monkeypatch):
    sent = []

    async def _send(bot_token, chat_id, text):
        sent.append(text)
        return {"ok": True}

    async def _incidents(days, checker_id):
        return [
            {"name": "de-1", "stableId": "s1", "ongoing": True, "durationSec": 600},
            {"name": "fr-2", "stableId": "s2", "ongoing": True, "durationSec": 600},
        ]

    monkeypatch.setattr(telegram, "send_message", _send)
    monkeypatch.setattr(rules_api.metrics_store, "get_incidents", _incidents)
    monkeypatch.setattr(rules_api, "_filter_by_account", lambda inc, aid: inc)

    h, aid = _auth()
    client.post("/api/rules", headers=h, json=_rule_body(enabled=True))

    now = 1_000_000
    asyncio.run(rules_api._run_account_scheduled(aid, now))
    assert sorted(sent) == ["down de-1", "down fr-2"]  # BOTH down nodes fired

    # Immediate re-run inside cooldown → neither node re-fires.
    sent.clear()
    asyncio.run(rules_api._run_account_scheduled(aid, now + 10))
    assert sent == []


def test_webhook_non_matching_event_does_not_fire(monkeypatch):
    monkeypatch.setattr(settings, "webhook_secret_header", "sekret")
    calls = []

    async def _send(bot_token, chat_id, text):
        calls.append(chat_id)
        return {"ok": True}

    monkeypatch.setattr(telegram, "send_message", _send)

    h, _ = _auth()
    chat = uuid.uuid4().hex[:10]
    client.post(
        "/api/rules",
        headers=h,
        json=_rule_body(
            enabled=True,
            trigger={"type": "webhook", "params": {"event": "node.connection_lost"}},
            actions=[
                {
                    "type": "telegram",
                    "params": {
                        "bot_token": "1:REALtoken_zzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",
                        "chat_id": chat,
                        "text": "x",
                    },
                }
            ],
        ),
    )
    # A different event → our rule must NOT fire.
    body = json.dumps(
        {"event": "node.connection_restored", "scope": "node", "data": {}}
    ).encode()
    client.post(
        "/api/webhooks/remnawave",
        content=body,
        headers={"X-Remnawave-Signature": _sign("sekret", body)},
    )
    assert chat not in calls
