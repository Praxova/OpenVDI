"""ContextVar isolation across asyncio tasks."""
from __future__ import annotations

import asyncio

import pytest

from openvdi_admin._request_context import (
    clear_request_id,
    current_request_id,
    new_request_id,
)


@pytest.fixture(autouse=True)
def _isolate():
    """Make every test start with no request_id set, regardless of
    what previous tests left behind."""
    clear_request_id()
    yield
    clear_request_id()


class TestNewRequestId:
    async def test_returns_uuid_string(self):
        rid = new_request_id()
        assert isinstance(rid, str)
        assert len(rid) == 36  # UUID4 canonical form

    async def test_subsequent_calls_return_different_ids(self):
        a = new_request_id()
        b = new_request_id()
        assert a != b

    async def test_sets_value_in_context_var(self):
        rid = new_request_id()
        assert current_request_id() == rid


class TestCurrentRequestId:
    async def test_returns_none_when_unset(self):
        assert current_request_id() is None

    async def test_returns_what_was_set(self):
        rid = new_request_id()
        assert current_request_id() == rid


class TestClearRequestId:
    async def test_clears_to_none(self):
        new_request_id()
        clear_request_id()
        assert current_request_id() is None


class TestAsyncioIsolation:
    """ContextVar bugs are sneaky. These tests demonstrate isolation
    across concurrent asyncio tasks — the production guarantee the
    instrument_tool decorator depends on."""

    async def test_concurrent_tasks_have_distinct_ids(self):
        seen: list[tuple[str, str]] = []

        async def task(label: str) -> None:
            rid = new_request_id()
            # Yield so another task can run between set and read.
            await asyncio.sleep(0.01)
            # After the yield, this task still sees its own value.
            assert current_request_id() == rid
            seen.append((label, rid))

        await asyncio.gather(task("a"), task("b"), task("c"))

        assert len(seen) == 3
        # Three distinct UUIDs survived the interleaved execution.
        assert len({rid for _, rid in seen}) == 3

    async def test_subcoroutine_inherits_parent_context(self):
        """Within the same task, an awaited subcoroutine sees the
        parent's request_id. This is what the BrokerClient depends
        on — a tool sets the ID at the top, then deep `await`
        chains through the client read the same value."""
        rid = new_request_id()

        async def inner() -> str | None:
            return current_request_id()

        assert await inner() == rid
