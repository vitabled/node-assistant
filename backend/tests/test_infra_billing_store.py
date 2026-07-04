"""Unit tests for services/infra_billing_store.py — encrypted token vault,
CRUD, no-PIN settings, and per-account database isolation."""
import asyncio
import uuid

from app.services import accounts
from app.services import infra_billing_store as store


def _account():
    return accounts.create_account(f"bill-{uuid.uuid4().hex[:8]}", "pw")["id"]


def _run(aid, coro):
    """Run an async store call with `aid` published on the ContextVar (the store
    resolves its per-account DB from it; the value propagates into to_thread)."""
    token = accounts.current_account.set(aid)
    try:
        return asyncio.run(coro())
    finally:
        accounts.current_account.reset(token)


def test_api_token_vault_encrypts_and_masks():
    aid = _account()

    async def flow():
        tid = await store.create_api_token("sel", "selectel", "super-secret-value")
        listed = await store.api_tokens()
        secret = await store.get_api_token_secret(tid)
        return tid, listed, secret

    tid, listed, secret = _run(aid, flow)
    assert secret == "super-secret-value"           # decrypts back
    row = next(t for t in listed if t["id"] == tid)
    assert "super-secret-value" not in row["masked"]  # never leaked
    assert row["masked"].startswith("super")          # short prefix kept


def test_payments_crud():
    aid = _account()

    async def flow():
        pid = await store.create_payment(amount=100.0, type="topup", note="p1")
        after_create = await store.payments()
        await store.delete_payment(pid)
        after_delete = await store.payments()
        return after_create, after_delete

    created, deleted = _run(aid, flow)
    assert any(p["note"] == "p1" for p in created)
    assert deleted == []


def test_settings_have_no_pin_field():
    aid = _account()
    s = _run(aid, store.get_settings)
    assert "pinSet" not in s
    assert s["baseCurrency"] == "RUB"


def test_db_is_isolated_per_account():
    a, b = _account(), _account()
    _run(a, lambda: store.create_payment(amount=5.0, note="only-A"))
    a_pays = _run(a, store.payments)
    b_pays = _run(b, store.payments)
    assert any(p["note"] == "only-A" for p in a_pays)
    assert b_pays == []
