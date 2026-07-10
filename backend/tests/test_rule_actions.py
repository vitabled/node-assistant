"""Tests for rule action execution: dry-run plans, secret redaction, Telegram
send (mocked), and idempotent host toggling against a fake Remnawave client.

Async executors are driven via asyncio.run (repo convention — no pytest-asyncio)."""

import asyncio
import uuid

from app.services import accounts, rule_actions, rules_store, telegram


def _run(coro):
    return asyncio.run(coro)


def _new_account() -> str:
    acc = accounts.create_account(f"ra-{uuid.uuid4().hex[:8]}", "pw")
    return acc["id"]


# ── redactor ──────────────────────────────────────────────────
def test_redactor_masks_bot_token():
    tok = "123456789:AAExampleTokenValue_abcdefghijklmnopqrstuvwx"
    masked = telegram.redact(f"failed with {tok} oops")
    assert tok not in masked
    assert "redacted" in masked


def test_redactor_masks_explicit_secret():
    assert "supersecret" not in telegram.redact("boom supersecret", "supersecret")


# ── dry-run never executes ────────────────────────────────────
def test_dry_run_returns_plan_without_sending(monkeypatch):
    aid = _new_account()
    ref = rules_store.put_secret("123:realtoken_aaaaaaaaaaaaaaaaaaaaaaaaaaa", aid)

    called = {"n": 0}

    async def _boom(*a, **k):
        called["n"] += 1
        return {"ok": True}

    monkeypatch.setattr(telegram, "send_message", _boom)
    actions = [
        {
            "type": "telegram",
            "params": {"token_ref": ref, "chat_id": "42", "text": "$node down"},
        }
    ]
    plan = _run(
        rule_actions.execute_actions(actions, {"node": "de-1"}, aid, dry_run=True)
    )

    assert called["n"] == 0  # nothing sent
    assert plan[0]["executed"] is False
    assert plan[0]["dry_run"] is True
    assert plan[0]["plan"]["text"] == "de-1 down"  # placeholder rendered
    assert plan[0]["plan"]["bot_token"] == rules_store.MASK
    assert "token_ref" not in plan[0]["plan"]  # no secret ref leaked in plan


# ── telegram execute resolves the vault token ─────────────────
def test_telegram_execute_uses_vault_token(monkeypatch):
    aid = _new_account()
    ref = rules_store.put_secret("999:vaulttoken_bbbbbbbbbbbbbbbbbbbbbbbbbbb", aid)

    seen = {}

    async def _send(bot_token, chat_id, text):
        seen.update(bot_token=bot_token, chat_id=chat_id, text=text)
        return {"ok": True}

    monkeypatch.setattr(telegram, "send_message", _send)
    actions = [
        {
            "type": "telegram",
            "params": {"token_ref": ref, "chat_id": "7", "text": "hi $node"},
        }
    ]
    res = _run(
        rule_actions.execute_actions(actions, {"node": "fr-2"}, aid, dry_run=False)
    )

    assert res[0]["ok"] is True
    assert seen["bot_token"] == "999:vaulttoken_bbbbbbbbbbbbbbbbbbbbbbbbbbb"
    assert seen["text"] == "hi fr-2"


def test_telegram_missing_token_fails_soft():
    aid = _new_account()
    actions = [{"type": "telegram", "params": {"chat_id": "7", "text": "x"}}]
    res = _run(rule_actions.execute_actions(actions, {}, aid, dry_run=False))
    assert res[0]["ok"] is False


# ── host toggling is idempotent + node-scoped ─────────────────
class _FakeRW:
    def __init__(self, hosts):
        self._hosts = hosts
        self.disabled = None
        self.enabled = None

    async def list_hosts(self):
        return self._hosts

    async def bulk_disable_hosts(self, uuids):
        self.disabled = uuids
        return {}

    async def bulk_enable_hosts(self, uuids):
        self.enabled = uuids
        return {}


def test_hide_hosts_filters_by_node_and_skips_already_disabled(monkeypatch):
    aid = _new_account()
    fake = _FakeRW(
        [
            {"uuid": "h1", "isDisabled": False, "nodes": ["N1"]},  # on node → disable
            {
                "uuid": "h2",
                "isDisabled": True,
                "nodes": ["N1"],
            },  # already disabled → skip
            {"uuid": "h3", "isDisabled": False, "nodes": ["N2"]},  # other node → skip
        ]
    )
    monkeypatch.setattr(rule_actions, "_rw_client", lambda account_id: fake)
    actions = [{"type": "hide_hosts", "params": {"node_uuid": "N1"}}]
    res = _run(rule_actions.execute_actions(actions, {}, aid, dry_run=False))
    assert res[0]["ok"] is True
    assert fake.disabled == ["h1"]  # only the not-yet-disabled host on N1


def test_show_hosts_idempotent_noop(monkeypatch):
    aid = _new_account()
    fake = _FakeRW(
        [{"uuid": "h1", "isDisabled": False, "nodes": ["N1"]}]
    )  # already enabled
    monkeypatch.setattr(rule_actions, "_rw_client", lambda account_id: fake)
    res = _run(
        rule_actions.execute_actions(
            [{"type": "show_hosts", "params": {"node_uuid": "N1"}}],
            {},
            aid,
            dry_run=False,
        )
    )
    assert res[0]["affected"] == 0
    assert fake.enabled is None  # nothing toggled


def test_remnawave_not_configured_fails_soft():
    aid = _new_account()  # fresh account has no panel_url/api_token
    res = _run(
        rule_actions.execute_actions(
            [{"type": "node_disable", "params": {"node_uuid": "N1"}}],
            {},
            aid,
            dry_run=False,
        )
    )
    assert res[0]["executed"] is True
    assert res[0]["ok"] is False  # logged + recorded, never raised


_VALID_UUID = "11111111-2222-3333-4444-555555555555"


def test_hide_hosts_without_selector_refuses_all(monkeypatch):
    # No host_uuids / node_uuid / config_profile_uuid → must NOT hide every host.
    aid = _new_account()
    fake = _FakeRW([{"uuid": "h1", "isDisabled": False, "nodes": ["N1"]}])
    monkeypatch.setattr(rule_actions, "_rw_client", lambda account_id: fake)
    res = _run(
        rule_actions.execute_actions(
            [{"type": "hide_hosts", "params": {}}], {}, aid, dry_run=False
        )
    )
    assert res[0]["ok"] is False
    assert res[0]["affected"] == 0
    assert fake.disabled is None  # nothing toggled — panel not black-holed


def test_hide_hosts_by_config_profile(monkeypatch):
    aid = _new_account()
    fake = _FakeRW(
        [
            {
                "uuid": "h1",
                "isDisabled": False,
                "nodes": ["N1"],
                "inbound": {"configProfileUuid": "P1"},
            },
            {
                "uuid": "h2",
                "isDisabled": False,
                "nodes": ["N2"],
                "inbound": {"configProfileUuid": "P2"},
            },
        ]
    )
    monkeypatch.setattr(rule_actions, "_rw_client", lambda account_id: fake)
    res = _run(
        rule_actions.execute_actions(
            [{"type": "hide_hosts", "params": {"config_profile_uuid": "P1"}}],
            {},
            aid,
            dry_run=False,
        )
    )
    assert res[0]["ok"] is True
    assert fake.disabled == ["h1"]


def test_node_action_rejects_malformed_uuid(monkeypatch):
    # A hostile uuid (path-traversal shaped) must be refused BEFORE any client call.
    aid = _new_account()
    called = {"n": 0}

    class _Spy:
        async def disable_node(self, uuid):
            called["n"] += 1
            return {}

    monkeypatch.setattr(rule_actions, "_rw_client", lambda account_id: _Spy())
    res = _run(
        rule_actions.execute_actions(
            [{"type": "node_disable", "params": {"node_uuid": "../../etc/passwd"}}],
            {},
            aid,
            dry_run=False,
        )
    )
    assert res[0]["ok"] is False
    assert "формат" in res[0]["detail"]
    assert called["n"] == 0  # never reached the network


def test_node_action_accepts_valid_uuid(monkeypatch):
    aid = _new_account()
    seen = {}

    class _Spy:
        async def disable_node(self, uuid):
            seen["uuid"] = uuid
            return {}

    monkeypatch.setattr(rule_actions, "_rw_client", lambda account_id: _Spy())
    res = _run(
        rule_actions.execute_actions(
            [{"type": "node_disable", "params": {"node_uuid": _VALID_UUID}}],
            {},
            aid,
            dry_run=False,
        )
    )
    assert res[0]["ok"] is True
    assert seen["uuid"] == _VALID_UUID
