"""Tests for WorkerRunner mechanics: tick scheduling, exception
handling, failure-streak escalation, graceful shutdown.

Uses a FakeWorker subclass with controllable tick behavior. Real
Postgres still required (the runner opens a lock-holder connection),
but the FakeWorker doesn't touch the DB itself.
"""
from __future__ import annotations

import asyncio
import logging

import pytest
from fastapi import FastAPI

from app.workers.base import (
    FAILURE_STREAK_ERROR_THRESHOLD,
    Worker,
    WorkerRunner,
)


# ── FakeWorker fixture ──────────────────────────────────────


class FakeWorker(Worker):
    """A worker whose tick behavior is controlled per-test via
    instance attributes. Each tick records a count and either
    succeeds or raises depending on `raise_on_tick`."""

    name = "fake"
    interval_seconds = 0.05  # 50ms — fast enough for test loops

    def __init__(self) -> None:
        self.tick_count = 0
        self.raise_on_tick: Exception | None = None
        self.tick_event = asyncio.Event()

    async def tick(self, app: FastAPI) -> None:
        self.tick_count += 1
        self.tick_event.set()
        if self.raise_on_tick is not None:
            raise self.raise_on_tick


@pytest.fixture
def app() -> FastAPI:
    """Minimal FastAPI app instance — just a state container."""
    return FastAPI()


# ── Tests ───────────────────────────────────────────────────


async def test_runner_runs_tick_periodically(app):
    """Runner ticks the worker every interval_seconds."""
    worker = FakeWorker()
    runner = WorkerRunner(app, [worker])
    await runner.start()
    try:
        # Wait for at least 3 ticks. With 50ms interval, this should
        # take ~150ms.
        for _ in range(3):
            worker.tick_event.clear()
            await asyncio.wait_for(worker.tick_event.wait(), timeout=2.0)
    finally:
        await runner.stop()
    assert worker.tick_count >= 3


async def test_runner_continues_after_tick_exception(app, caplog):
    """A worker that raises on every tick keeps ticking; the runner
    logs each failure but never crashes."""
    worker = FakeWorker()
    worker.raise_on_tick = RuntimeError("boom")
    runner = WorkerRunner(app, [worker])
    with caplog.at_level(logging.WARNING):
        await runner.start()
        try:
            for _ in range(3):
                worker.tick_event.clear()
                await asyncio.wait_for(worker.tick_event.wait(), timeout=2.0)
        finally:
            await runner.stop()
    assert worker.tick_count >= 3
    warning_records = [
        r for r in caplog.records
        if r.levelname == "WARNING" and "tick failed" in r.message
    ]
    assert len(warning_records) >= 1


async def test_runner_escalates_after_5_failures(app, caplog):
    """After FAILURE_STREAK_ERROR_THRESHOLD consecutive failures, the
    log level escalates to ERROR."""
    worker = FakeWorker()
    worker.raise_on_tick = RuntimeError("boom")
    runner = WorkerRunner(app, [worker])
    with caplog.at_level(logging.ERROR):
        await runner.start()
        try:
            while worker.tick_count < FAILURE_STREAK_ERROR_THRESHOLD + 1:
                worker.tick_event.clear()
                await asyncio.wait_for(worker.tick_event.wait(), timeout=2.0)
        finally:
            await runner.stop()
    error_records = [
        r for r in caplog.records
        if r.levelname == "ERROR" and "tick failed" in r.message
    ]
    assert len(error_records) >= 1


async def test_runner_resets_streak_on_success(app, caplog):
    """A successful tick resets the failure streak; a 'recovered' INFO
    log fires after the success."""
    worker = FakeWorker()
    runner = WorkerRunner(app, [worker])
    await runner.start()
    try:
        # Fail 3 times.
        worker.raise_on_tick = RuntimeError("boom")
        while worker.tick_count < 3:
            worker.tick_event.clear()
            await asyncio.wait_for(worker.tick_event.wait(), timeout=2.0)
        # Then succeed once.
        worker.raise_on_tick = None
        with caplog.at_level(logging.INFO):
            initial_count = worker.tick_count
            while worker.tick_count <= initial_count:
                worker.tick_event.clear()
                await asyncio.wait_for(worker.tick_event.wait(), timeout=2.0)
    finally:
        await runner.stop()
    recovery = [r for r in caplog.records if "recovered" in r.message]
    assert len(recovery) >= 1


async def test_runner_stop_cancels_workers(app):
    """stop() cancels all worker tasks. After stop, no further ticks."""
    worker = FakeWorker()
    runner = WorkerRunner(app, [worker])
    await runner.start()
    worker.tick_event.clear()
    await asyncio.wait_for(worker.tick_event.wait(), timeout=2.0)
    await runner.stop()
    snapshot = worker.tick_count
    await asyncio.sleep(0.2)
    assert worker.tick_count == snapshot


async def test_runner_stop_idempotent(app):
    """Calling stop twice is safe (second call is a no-op)."""
    worker = FakeWorker()
    runner = WorkerRunner(app, [worker])
    await runner.start()
    await runner.stop()
    await runner.stop()  # should not raise


async def test_runner_start_twice_raises(app):
    """Calling start twice on the same runner raises (programmer error)."""
    worker = FakeWorker()
    runner = WorkerRunner(app, [worker])
    await runner.start()
    try:
        with pytest.raises(RuntimeError, match="already started"):
            await runner.start()
    finally:
        await runner.stop()


async def test_runner_handles_zero_workers(app):
    """An empty workers list is allowed (e.g. M5+ flag-disable path)."""
    runner = WorkerRunner(app, [])
    await runner.start()
    await runner.stop()
