"""Tests for AuditRetentionWorker.

Mocks asyncio.sleep + random.uniform to bypass the 0-2h jitter delay
(otherwise the first-tick test would actually sleep). The DELETE
query runs against a real Postgres test DB via tests/_db.py.

The session-factory monkeypatch follows the M4-08 pattern: the
worker's own async_session_factory() calls bind to the test
connection so its commits participate in the outer transaction
(savepoint-nested), rolled back at teardown.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog
from app.workers.audit_retention import AuditRetentionWorker

# Re-export the transactional db_session fixture (M4-06).
from tests._db import db_session  # noqa: F401


class _TestSessionFactory:
    def __init__(self, connection) -> None:
        self._connection = connection

    def __call__(self) -> AsyncSession:
        return AsyncSession(
            bind=self._connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )


@pytest_asyncio.fixture
async def patch_session_factory(db_session, monkeypatch):
    """Patch the worker's async_session_factory so its prune commit
    participates in the test's outer transaction."""
    factory = _TestSessionFactory(db_session.bind)
    monkeypatch.setattr(
        "app.workers.audit_retention.async_session_factory", factory,
    )
    return factory


# ── Fixture builders ────────────────────────────────────────


async def _make_audit_row(
    db,
    *,
    timestamp: datetime,
    actor: str = "alice",
    action: str = "test",
):
    row = AuditLog(
        actor=actor,
        action=action,
        resource_type="desktop",
        resource_id=uuid4(),
        client_ip=None,
    )
    db.add(row)
    await db.flush()
    # Override the server-default timestamp. No onupdate trigger on
    # this column, so the explicit assignment persists.
    row.timestamp = timestamp
    await db.commit()
    return row


def _patch_settings(monkeypatch, *, retention_days: int):
    fake = MagicMock()
    fake.openvdi_audit_retention_days = retention_days
    monkeypatch.setattr(
        "app.workers.audit_retention.get_settings", lambda: fake,
    )


@pytest.fixture
def mock_jitter(monkeypatch):
    """Patch random.uniform → 0 (no delay) and asyncio.sleep → no-op,
    so tests that don't care about the jitter don't actually sleep."""
    monkeypatch.setattr(
        "app.workers.audit_retention.random.uniform",
        lambda lo, hi: 0,
    )
    monkeypatch.setattr(
        "app.workers.audit_retention.asyncio.sleep", AsyncMock(),
    )


@pytest.fixture
def app() -> FastAPI:
    return FastAPI()


# ── Tests ────────────────────────────────────────────────────


async def test_old_rows_deleted(
    db_session, patch_session_factory, mock_jitter, app, monkeypatch,
):
    """Rows older than retention_days are deleted; recent rows survive."""
    _patch_settings(monkeypatch, retention_days=30)

    now = datetime.now(timezone.utc)
    old = await _make_audit_row(
        db_session, timestamp=now - timedelta(days=60),
    )
    recent = await _make_audit_row(
        db_session, timestamp=now - timedelta(days=10),
    )
    just_over = await _make_audit_row(
        db_session, timestamp=now - timedelta(days=31),
    )

    worker = AuditRetentionWorker()
    await worker.tick(app)

    # Use SQL-level lookup (bypass the session's identity map, which
    # may still have references to deleted rows).
    from sqlalchemy import text
    surviving_ids = {
        row[0]
        for row in (
            await db_session.execute(
                text(
                    "SELECT id FROM audit_log "
                    "WHERE id IN (:a, :b, :c)"
                ),
                {"a": old.id, "b": recent.id, "c": just_over.id},
            )
        )
    }
    assert old.id not in surviving_ids
    assert just_over.id not in surviving_ids
    assert recent.id in surviving_ids


async def test_no_old_rows_no_op(
    db_session, patch_session_factory, mock_jitter, app, monkeypatch,
):
    """Empty result set → no-op, no error."""
    _patch_settings(monkeypatch, retention_days=90)

    now = datetime.now(timezone.utc)
    recent = await _make_audit_row(
        db_session, timestamp=now - timedelta(days=5),
    )

    worker = AuditRetentionWorker()
    await worker.tick(app)

    surviving = await db_session.get(AuditLog, recent.id)
    assert surviving is not None


async def test_first_tick_applies_jitter_subsequent_does_not(
    db_session, patch_session_factory, app, monkeypatch,
):
    """First tick sleeps with the jitter value; subsequent ticks don't."""
    sleep_calls: list[float] = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr(
        "app.workers.audit_retention.asyncio.sleep", fake_sleep,
    )
    monkeypatch.setattr(
        "app.workers.audit_retention.random.uniform",
        lambda lo, hi: 1234.5,
    )
    _patch_settings(monkeypatch, retention_days=90)

    worker = AuditRetentionWorker()
    await worker.tick(app)
    assert 1234.5 in sleep_calls
    assert worker._first_tick_complete is True

    sleep_calls.clear()
    await worker.tick(app)
    assert sleep_calls == []


async def test_jitter_within_2_hour_bound(
    db_session, patch_session_factory, app, monkeypatch,
):
    """The first-tick jitter is uniform over [0, 7200] seconds."""
    captured_bounds: list[tuple] = []

    def capture_uniform(lo, hi):
        captured_bounds.append((lo, hi))
        return 100.0  # arbitrary; not the test target

    monkeypatch.setattr(
        "app.workers.audit_retention.random.uniform", capture_uniform,
    )
    monkeypatch.setattr(
        "app.workers.audit_retention.asyncio.sleep", AsyncMock(),
    )
    _patch_settings(monkeypatch, retention_days=90)

    worker = AuditRetentionWorker()
    await worker.tick(app)
    assert captured_bounds == [(0, 7200)]


async def test_retention_days_read_from_settings(
    db_session, patch_session_factory, mock_jitter, app, monkeypatch,
):
    """retention_days comes from Settings, not hardcoded — change the
    setting → change which rows survive."""
    _patch_settings(monkeypatch, retention_days=7)  # 1 week

    now = datetime.now(timezone.utc)
    over_a_week = await _make_audit_row(
        db_session, timestamp=now - timedelta(days=10),
    )
    under_a_week = await _make_audit_row(
        db_session, timestamp=now - timedelta(days=5),
    )

    worker = AuditRetentionWorker()
    await worker.tick(app)

    from sqlalchemy import text
    surviving_ids = {
        row[0]
        for row in (
            await db_session.execute(
                text(
                    "SELECT id FROM audit_log WHERE id IN (:a, :b)"
                ),
                {"a": over_a_week.id, "b": under_a_week.id},
            )
        )
    }
    assert over_a_week.id not in surviving_ids   # 10d > 7d retention
    assert under_a_week.id in surviving_ids       # 5d < 7d retention


async def test_logs_rows_deleted(
    db_session, patch_session_factory, mock_jitter, app, monkeypatch,
    caplog,
):
    """Prune-complete log line carries rows_deleted count."""
    _patch_settings(monkeypatch, retention_days=30)

    now = datetime.now(timezone.utc)
    for _ in range(3):
        await _make_audit_row(
            db_session, timestamp=now - timedelta(days=60),
        )

    worker = AuditRetentionWorker()
    with caplog.at_level(logging.INFO):
        await worker.tick(app)

    prune_logs = [
        r for r in caplog.records if "prune complete" in r.message
    ]
    assert len(prune_logs) == 1
    # Per the worker's extra={...} contract — caller extras land as
    # attributes on the LogRecord.
    assert prune_logs[0].rows_deleted == 3


async def test_idempotent_repeat_invocations(
    db_session, patch_session_factory, mock_jitter, app, monkeypatch,
):
    """Second invocation with no new old rows → 0 deletions, no error."""
    _patch_settings(monkeypatch, retention_days=30)

    now = datetime.now(timezone.utc)
    await _make_audit_row(
        db_session, timestamp=now - timedelta(days=60),
    )

    worker = AuditRetentionWorker()
    await worker.tick(app)
    # Second call → nothing matches; should be a clean no-op.
    await worker.tick(app)
