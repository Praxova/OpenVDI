"""AuditRetentionWorker — daily prune of audit_log rows.

Runs once per 24 hours. On the first tick after acquiring leadership,
sleeps a uniform-random 0-7200 seconds (per X4 jitter requirement) to
avoid synchronized prune events across brokers that booted together
(systemd / Docker / k8s rolling deploy at midnight, leadership
transfer immediately running tick on lock acquire). Subsequent ticks
run on the regular 24h cadence.

Deletes audit_log rows where timestamp < now() - retention_days.
Single bulk DELETE — no batching needed at v0 audit-log volume; the
idx_audit_timestamp index on audit_log makes the WHERE-clause
selection fast. Idempotent.

Per X3, X4. Final operational worker for M4.
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import ClassVar

from fastapi import FastAPI
from sqlalchemy import delete

from app.config import get_settings
from app.database import async_session_factory
from app.models import AuditLog
from app.workers.base import Worker

logger = logging.getLogger(__name__)


# First-tick jitter cap. Spread of 0-2 hours per X4. Subsequent ticks
# fire on the regular 24h cadence with no jitter — a fixed leader sees
# a stable schedule.
_FIRST_TICK_JITTER_MAX_SECONDS = 2 * 60 * 60


class AuditRetentionWorker(Worker):
    name: ClassVar[str] = "audit_retention"
    interval_seconds: ClassVar[float] = 24 * 60 * 60   # 24 hours

    def __init__(self) -> None:
        # First-tick jitter is per-leader-tenure: when this broker
        # acquires leadership, the next tick gets jittered. Leadership
        # transfer to another broker creates that broker's own fresh
        # worker instance with its own first-tick delay.
        self._first_tick_complete = False

    async def tick(self, app: FastAPI) -> None:
        # First-tick jitter (X4). Skip on subsequent ticks — the 24h
        # cadence already separates them.
        if not self._first_tick_complete:
            jitter_seconds = random.uniform(
                0, _FIRST_TICK_JITTER_MAX_SECONDS,
            )
            logger.info(
                "audit_retention first-tick jitter delay",
                extra={"jitter_seconds": round(jitter_seconds, 1)},
            )
            await asyncio.sleep(jitter_seconds)
            self._first_tick_complete = True

        await self._prune()

    async def _prune(self) -> None:
        """Run the bulk DELETE. Idempotent; returns nothing."""
        retention_days = get_settings().openvdi_audit_retention_days
        threshold = (
            datetime.now(timezone.utc) - timedelta(days=retention_days)
        )

        async with async_session_factory() as db:
            stmt = delete(AuditLog).where(AuditLog.timestamp < threshold)
            result = await db.execute(stmt)
            await db.commit()

        # SQLAlchemy returns None on some dialects when no rows matched;
        # coerce defensively so the log line always shows an int.
        rows_deleted = result.rowcount or 0
        logger.info(
            "audit retention prune complete",
            extra={
                "retention_days": retention_days,
                "threshold": threshold.isoformat(),
                "rows_deleted": rows_deleted,
            },
        )
