"""Worker framework.

A `Worker` is a class with `name`, `interval_seconds`, and an
`async tick(app)` method. The `WorkerRunner` spawns one asyncio.Task
per worker; each task runs a leader-election outer loop and a
periodic-tick inner loop.

Leader election uses Postgres advisory locks held on dedicated
per-worker connections. The lock auto-releases when the connection
drops (broker death, network failure), allowing a follower broker to
take over on its next retry. See docs/prompts/m4-planning-seed.md
W11/W12 and docs/deploy.md → Multi-Broker for the full posture.

Workers are added to the WORKERS list in `app/workers/__init__.py`
as they ship across M4-08 through M4-13. M4-07 ships only the
framework + an EchoWorker smoke.
"""
from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import AsyncIterator, ClassVar, Final

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.database import engine

logger = logging.getLogger(__name__)


# Per-worker leader-election retry interval. When a follower fails to
# acquire the lock, it sleeps this long before trying again. 30s is the
# operational sweet spot: short enough that leader handoff after a
# crash takes ≤30s; long enough that idle followers don't pummel
# Postgres or the connection pool.
LEADER_RETRY_INTERVAL_SECONDS: Final[float] = 30.0

# After this many consecutive tick failures, escalate the per-tick log
# level from WARNING to ERROR. The worker keeps ticking — we never
# crash on tick errors per W3.
FAILURE_STREAK_ERROR_THRESHOLD: Final[int] = 5


class Worker(ABC):
    """Base class for periodic background workers.

    Subclasses set `name` and `interval_seconds` as class attributes
    and implement `tick(self, app)`. The runner enforces leader
    election; `tick` runs only on the leader broker, periodically.

    Workers are stateless across tick invocations (W12). All progress
    lives in the database; a leader handoff mid-cycle is recoverable
    because the next tick reads state from DB, not memory.

    Per W4: tick is responsible for opening its own AsyncSession via
    `app.database.async_session_factory`. It must NOT depend on
    FastAPI's `Depends(get_db_session)` (which only works in request
    scope).
    """

    name: ClassVar[str]
    interval_seconds: ClassVar[float]

    @abstractmethod
    async def tick(self, app: FastAPI) -> None:
        """One iteration. Open a session, do work, commit, return.

        Exceptions propagate to the WorkerRunner which logs them and
        continues the loop. Do NOT swallow exceptions inside tick;
        the runner's per-tick logging is the audit trail.

        `asyncio.CancelledError` is allowed to propagate — it's the
        signal that the lifespan is shutting down.
        """


class WorkerRunner:
    """Spawn-and-supervise N workers. One asyncio.Task per worker;
    each task runs the leader-election + tick loop independently.

    Lifecycle:
      - `start()` spawns the per-worker tasks.
      - `stop()` cancels all tasks and awaits their completion.
        Per-task cancellation drops the lock-holder connection, which
        auto-releases the advisory lock so a follower broker can take
        over.

    Construction is cheap (no work). Work begins on `start()`.
    """

    def __init__(self, app: FastAPI, workers: list[Worker]):
        self._app = app
        self._workers = workers
        self._tasks: list[asyncio.Task[None]] = []
        self._stopping = False

    async def start(self) -> None:
        """Spawn one task per worker. Returns immediately."""
        if self._tasks:
            raise RuntimeError("WorkerRunner already started")
        for worker in self._workers:
            task = asyncio.create_task(
                self._run_worker(worker),
                name=f"worker:{worker.name}",
            )
            self._tasks.append(task)
        logger.info(
            "worker runner started",
            extra={"workers": [w.name for w in self._workers]},
        )

    async def stop(self) -> None:
        """Cancel + await every worker. Idempotent."""
        if self._stopping:
            return
        self._stopping = True
        for task in self._tasks:
            task.cancel()
        # gather with return_exceptions so one task's CancelledError
        # doesn't mask another's. We log+swallow; the lifespan caller
        # doesn't need the exceptions.
        results = await asyncio.gather(*self._tasks, return_exceptions=True)
        for worker, result in zip(self._workers, results):
            if isinstance(result, Exception) and not isinstance(
                result, asyncio.CancelledError
            ):
                logger.warning(
                    "worker exited with unexpected exception",
                    extra={"worker": worker.name, "error": repr(result)},
                )
        logger.info("worker runner stopped")

    # ── Per-worker outer loop ───────────────────────────────

    async def _run_worker(self, worker: Worker) -> None:
        """Outer loop: keep trying to lead. On any tick-loop exit
        (lost lock, connection dropped, exception escaped), retry
        leader acquisition after the retry interval.
        """
        logger.info("worker started", extra={"worker": worker.name})
        try:
            while True:
                try:
                    await self._lead(worker)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "worker leader loop died unexpectedly",
                        extra={"worker": worker.name},
                    )
                # Sleep before retrying leader acquisition. Whether we
                # never got the lock or held it briefly and dropped,
                # the retry interval is the same — we don't want to
                # hammer the DB.
                await asyncio.sleep(LEADER_RETRY_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            logger.info(
                "worker stopping (cancelled)",
                extra={"worker": worker.name},
            )
            raise

    async def _lead(self, worker: Worker) -> None:
        """Try to acquire the advisory lock; if successful, run the
        tick loop until the lock is lost or the task is cancelled.
        """
        async with _acquire_leader_lock(worker.name) as (got_lock, lock_conn):
            if not got_lock:
                logger.debug(
                    "not leader (another broker holds the lock)",
                    extra={"worker": worker.name},
                )
                return
            logger.info(
                "became leader",
                extra={
                    "worker": worker.name,
                    "interval": worker.interval_seconds,
                },
            )
            await self._tick_loop(worker, lock_conn)

    # ── Per-worker inner loop ───────────────────────────────

    async def _tick_loop(
        self, worker: Worker, lock_conn: AsyncConnection,
    ) -> None:
        """Inner loop: tick, sleep, repeat. Exits on:
          - asyncio.CancelledError (shutdown)
          - lock connection death (heartbeat failure → caller re-acquires)
        """
        failure_streak = 0
        while True:
            # Heartbeat the lock connection. If it's dead, the lock has
            # already auto-released; we shouldn't run the tick because
            # another broker may now be leader.
            try:
                await lock_conn.execute(text("SELECT 1"))
            except Exception as exc:
                logger.warning(
                    "lock connection dropped, abdicating leadership",
                    extra={"worker": worker.name, "error": repr(exc)},
                )
                return  # outer loop will retry acquisition

            # Tick.
            tick_start = time.monotonic()
            try:
                await worker.tick(self._app)
            except asyncio.CancelledError:
                raise
            except Exception:
                failure_streak += 1
                level = (
                    logging.ERROR
                    if failure_streak >= FAILURE_STREAK_ERROR_THRESHOLD
                    else logging.WARNING
                )
                logger.log(
                    level,
                    "tick failed",
                    extra={
                        "worker": worker.name,
                        "streak": failure_streak,
                    },
                    exc_info=True,
                )
            else:
                if failure_streak > 0:
                    logger.info(
                        "worker recovered after %d failures",
                        failure_streak,
                        extra={"worker": worker.name},
                    )
                failure_streak = 0
                logger.debug(
                    "tick complete",
                    extra={
                        "worker": worker.name,
                        "duration_ms": int(
                            (time.monotonic() - tick_start) * 1000
                        ),
                    },
                )

            # Sleep until next tick. CancelledError can fire here too
            # (during shutdown); let it propagate.
            await asyncio.sleep(worker.interval_seconds)


@asynccontextmanager
async def _acquire_leader_lock(
    worker_name: str,
) -> AsyncIterator[tuple[bool, AsyncConnection]]:
    """Open a dedicated DB connection and try to acquire the worker's
    advisory lock on it. Yields (got_lock, conn). On exit:
      - Calls `pg_advisory_unlock` explicitly. This is the path we
        rely on for normal leadership release.
      - Returns the connection to the SQLAlchemy pool. Note: this
        does NOT terminate the underlying asyncpg session, so
        Postgres's "advisory locks auto-release on session end" only
        kicks in on real TCP-level termination (broker process crash,
        network drop, `engine.dispose()`). The explicit unlock is
        therefore load-bearing for the normal-shutdown path; for the
        crash-failover path the TCP teardown does the work.

    Lock key is `hashtext('openvdi-worker:<name>')`, computed
    server-side for cross-language determinism. Two brokers asking
    for the same key on different connections both get a deterministic
    answer: exactly one wins.
    """
    conn = await engine.connect()
    try:
        result = await conn.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:name))"),
            {"name": f"openvdi-worker:{worker_name}"},
        )
        got_lock = bool(result.scalar())
        try:
            yield got_lock, conn
        finally:
            if got_lock:
                # Best-effort explicit unlock. The connection close
                # below also releases the lock; this just makes the
                # release immediate instead of "next garbage collection
                # of the connection."
                try:
                    await conn.execute(
                        text(
                            "SELECT pg_advisory_unlock(hashtext(:name))"
                        ),
                        {"name": f"openvdi-worker:{worker_name}"},
                    )
                except Exception:
                    # Connection may be dead (which is why we're
                    # exiting). Silently ignore — the lock has
                    # already auto-released.
                    pass
    finally:
        await conn.close()
