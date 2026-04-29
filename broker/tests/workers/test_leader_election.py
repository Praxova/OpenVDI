"""Leader-election tests. Simulates two brokers competing for the same
advisory lock by opening two engine connections from within one test
process. Real Postgres required.

Each test uses a unique worker-name string so test order doesn't matter
and they don't pollute one another — Postgres advisory locks are scoped
by (numeric) key + connection, but if test names collided across
parallel test runs the assertions would race.
"""
from __future__ import annotations

import asyncio

from app.workers.base import _acquire_leader_lock


async def test_lock_acquired_when_free():
    """First acquirer gets the lock."""
    async with _acquire_leader_lock("test-worker-A") as (got_lock, _conn):
        assert got_lock is True


async def test_second_acquirer_does_not_get_lock():
    """While one connection holds the lock, a second connection's
    pg_try_advisory_lock returns False."""
    async with _acquire_leader_lock("test-worker-B") as (first_got, _conn1):
        assert first_got is True
        async with _acquire_leader_lock("test-worker-B") as (
            second_got, _conn2,
        ):
            assert second_got is False


async def test_lock_released_on_context_exit():
    """After the first context exits, a new acquirer can get the lock."""
    async with _acquire_leader_lock("test-worker-C") as (first_got, _):
        assert first_got is True
    # First context done — lock should be free again.
    async with _acquire_leader_lock("test-worker-C") as (second_got, _):
        assert second_got is True


async def test_different_worker_names_are_independent():
    """Two workers with different names can both acquire their own
    locks simultaneously."""
    async with _acquire_leader_lock("test-worker-D1") as (got_d1, _):
        async with _acquire_leader_lock("test-worker-D2") as (got_d2, _):
            assert got_d1 is True
            assert got_d2 is True


async def test_concurrent_acquirers_serialize():
    """Two coroutines racing for the same lock: exactly one wins."""
    results: list[bool] = []
    barrier = asyncio.Event()

    async def attempt():
        await barrier.wait()
        async with _acquire_leader_lock("test-worker-F") as (got, _):
            results.append(got)
            # Hold briefly so the other side has a real chance to
            # observe contention.
            await asyncio.sleep(0.05)

    tasks = [asyncio.create_task(attempt()) for _ in range(2)]
    barrier.set()
    await asyncio.gather(*tasks)
    # Exactly one True, one False.
    assert sorted(results) == [False, True]
