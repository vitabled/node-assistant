"""Unit tests for services/storage.py — per-account file namespacing, resolution
from the current_account ContextVar, and its propagation into child tasks/threads
(the invariant the deploy pipeline relies on)."""
import asyncio
import uuid

import pytest

from app.services import accounts, storage


def _account():
    return accounts.create_account(f"st-{uuid.uuid4().hex[:8]}", "pw")["id"]


def test_requires_an_account_in_context():
    token = accounts.current_account.set(None)
    try:
        with pytest.raises(RuntimeError):
            storage.load_settings()
    finally:
        accounts.current_account.reset(token)


def test_explicit_account_id_isolates_reads_and_writes():
    a, b = _account(), _account()
    storage.save_settings({"k": "A"}, account_id=a)
    storage.save_settings({"k": "B"}, account_id=b)
    assert storage.load_settings(account_id=a) == {"k": "A"}
    assert storage.load_settings(account_id=b) == {"k": "B"}


def test_templates_and_traffic_rules_are_per_account():
    a, b = _account(), _account()
    storage.save_templates([{"id": "t1"}], account_id=a)
    storage.save_traffic_rules([{"id": "r1"}], account_id=a)
    assert storage.load_templates(account_id=a) == [{"id": "t1"}]
    assert storage.load_templates(account_id=b) == []
    assert storage.load_traffic_rules(account_id=a) == [{"id": "r1"}]
    assert storage.load_traffic_rules(account_id=b) == []


def test_context_var_resolution_without_explicit_id():
    a = _account()
    token = accounts.current_account.set(a)
    try:
        storage.save_settings({"via": "ctx"})
        assert storage.load_settings() == {"via": "ctx"}
    finally:
        accounts.current_account.reset(token)


def test_current_account_propagates_into_create_task_and_to_thread():
    async def scenario():
        aid = _account()
        accounts.current_account.set(aid)
        storage.save_settings({"marker": "ctx-A"})

        async def child():
            return storage.load_settings()
        assert (await asyncio.create_task(child())).get("marker") == "ctx-A"

        def blocking():
            return storage.load_settings().get("marker")
        assert await asyncio.to_thread(blocking) == "ctx-A"

    asyncio.run(scenario())
