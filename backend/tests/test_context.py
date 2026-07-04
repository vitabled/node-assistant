"""Pins the invariant the whole isolation design rests on: the `current_account`
ContextVar set in a request handler propagates into the deploy pipeline
(asyncio.create_task) and into threaded sqlite calls (asyncio.to_thread), so
per-account storage resolves correctly off-request."""
import asyncio
import uuid

from app.services import accounts, storage


def test_current_account_propagates_into_create_task_and_to_thread():
    async def scenario():
        acc = accounts.create_account(f"ctx-{uuid.uuid4().hex[:8]}", "pw")
        accounts.current_account.set(acc["id"])
        storage.save_settings({"marker": "ctx-A"})

        # create_task copies the current context → child resolves the same account.
        async def child():
            return storage.load_settings()
        via_task = await asyncio.create_task(child())
        assert via_task.get("marker") == "ctx-A"

        # to_thread copies the context too → threaded read resolves it as well.
        def blocking():
            return storage.load_settings().get("marker")
        assert await asyncio.to_thread(blocking) == "ctx-A"

    asyncio.run(scenario())


def test_data_dir_rejects_traversal():
    import pytest
    for bad in ["../evil", "a/b", "a\\b", ".."]:
        with pytest.raises(ValueError):
            accounts.data_dir(bad)
