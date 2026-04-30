"""Per-desktop task tracking helpers.

Stripped down from M2's BackgroundTasks-driven design (M4-10). The
M4 task_tracker worker (app/workers/task_tracker.py) discovers
in-flight tasks via DB poll and drives completion. This module
provides the helpers consumed by both the API layer and the worker:

  - DesktopTaskKind: enum of operation types (carried in
    desktop.pve_task_kind).
  - record_desktop_task(): replaces M2's start_desktop_task. Pure
    DB write — sets the UPID and kind on a Desktop row. The worker
    finds the row on its next tick.
  - _apply_task_success(): completion handler invoked by the worker.
  - _mark_task_error(): error handler invoked by the worker.

Dropped in M4-10 (still in git history):
  - start_desktop_task: replaced by record_desktop_task.
  - poll_desktop_task: replaced by the worker.
  - resume_inflight_tasks: unnecessary — the worker's first tick
    discovers everything.
  - _KIND_TIMEOUTS / _timeout_for_kind: unused with non-blocking polling.

DB-is-source-of-truth (W-1). If `pve_task_upid IS NOT NULL`, something
is in flight; the `pve_task_kind` says what completion means. No tasks
table, no per-step progress tracking. One UPID + one kind per row.

Caller contract for `record_desktop_task`: the caller must commit
the DB transaction BEFORE returning the 202 response. The worker
reads from a fresh session and won't see uncommitted writes.
"""
from __future__ import annotations

import enum
import logging
from typing import Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import async_session_factory
from app.models import Desktop, DesktopStatus, Pool
from app.providers.base import HypervisorProvider, TaskHandle, TaskStatus
from app.services.audit_service import log_business_event


logger = logging.getLogger(__name__)


class DesktopTaskKind(str, enum.Enum):
    """What operation produced the in-flight task.

    The string value is what lands in `desktop.pve_task_kind`; the
    completion handler branches on kind to decide what the Desktop
    row should look like post-completion.
    """

    PROVISION = "provision"   # clone → configure → start → (snapshot) → available
    DESTROY = "destroy"       # stop + DELETE → drop DB row
    REBUILD = "rebuild"       # destroy leg; provision leg chains after success
    START = "start"
    SHUTDOWN = "shutdown"
    STOP = "stop"


# ── Public surface ────────────────────────────────────────────


async def record_desktop_task(
    *,
    session: AsyncSession,
    desktop: Desktop,
    kind: DesktopTaskKind,
    task_handle: TaskHandle,
) -> None:
    """Record a task UPID + kind on a Desktop row.

    The task_tracker worker (app/workers/task_tracker.py) discovers
    this row on its next tick (≤5s) and polls completion via
    provider.get_task_status. On task completion (or failure), the
    worker dispatches to _apply_task_success / _mark_task_error in
    this module.

    Caller contract:
        1. Issue the provider call yourself; get a TaskHandle.
        2. Call this function.
        3. Commit the session.
        4. Return the 202 response.

    Step 3 (commit-before-return) is required: the worker reads
    from a fresh session and doesn't see uncommitted writes.
    """
    desktop.pve_task_upid = task_handle.data["upid"]
    desktop.pve_task_kind = kind.value


# ── Completion handlers (called by the worker) ────────────────


async def _apply_task_success(
    desktop_id: UUID,
    kind: DesktopTaskKind,
    status: TaskStatus,
    provider_factory: Callable[[], HypervisorProvider],
) -> None:
    """Happy-path row update for a completed task.

    The provider_factory is passed in so the REBUILD second leg
    (which invokes the provisioner) can look up the provider without
    re-going through app.state. Only REBUILD actually needs it; other
    kinds ignore the arg.
    """
    async with async_session_factory() as session:
        desktop = await session.get(Desktop, desktop_id)
        if desktop is None:
            return

        if kind == DesktopTaskKind.DESTROY:
            # VM is gone on the provider. Drop the row.
            await log_business_event(
                session=session, actor="system",
                action="desktop.destroy.completed",
                resource_type="desktop", resource_id=desktop.id,
                details={"vmid": desktop.pve_vmid, "name": desktop.name},
            )
            await session.delete(desktop)
            await session.commit()
            return

        if kind == DesktopTaskKind.REBUILD:
            # Destroy leg succeeded. Stash what we need for the
            # provision leg, clear the task fields, commit, then
            # re-provision.
            desktop.status = DesktopStatus.PROVISIONING
            desktop.pve_task_upid = None
            desktop.pve_task_kind = None
            pool_id = desktop.pool_id
            # Persistent assignment survives rebuild; floating does not.
            assigned_user = (
                desktop.assigned_user
                if desktop.assignment_type == "persistent"
                else None
            )
            await session.commit()

            # Fetch provider for the second leg.
            try:
                provider = provider_factory()
            except Exception as exc:
                await _mark_task_error(
                    desktop_id, kind, f"provider lookup: {exc}",
                )
                return

            from app.services.provisioner import provision_desktop

            async with async_session_factory() as leg2_session:
                pool = (
                    await leg2_session.execute(
                        select(Pool)
                        .where(Pool.id == pool_id)
                        .options(selectinload(Pool.template))
                    )
                ).scalar_one()
                existing = await leg2_session.get(Desktop, desktop_id)
                await provision_desktop(
                    session=leg2_session,
                    provider=provider,
                    pool=pool,
                    template=pool.template,
                    assigned_user=assigned_user,
                    existing_desktop=existing,
                )
            return

        if kind == DesktopTaskKind.PROVISION:
            # M2-07's provisioner owns the final state; this branch is
            # defensive in case someone wires PROVISION through here.
            desktop.status = DesktopStatus.AVAILABLE
            desktop.pve_task_upid = None
            desktop.pve_task_kind = None
            await session.commit()
            return

        # Power-state transitions.
        if kind == DesktopTaskKind.START:
            desktop.power_state = "running"
        elif kind in (DesktopTaskKind.SHUTDOWN, DesktopTaskKind.STOP):
            desktop.power_state = "stopped"

        desktop.pve_task_upid = None
        desktop.pve_task_kind = None
        await session.commit()


async def _mark_task_error(
    desktop_id: UUID, kind: DesktopTaskKind, message: str,
) -> None:
    """Transition the Desktop row to `error` with a descriptive
    message."""
    async with async_session_factory() as session:
        desktop = await session.get(Desktop, desktop_id)
        if desktop is None:
            return
        desktop.status = DesktopStatus.ERROR
        desktop.error_message = f"{kind.value} failed: {message}"
        desktop.pve_task_upid = None
        desktop.pve_task_kind = None
        await log_business_event(
            session=session, actor="system",
            action=f"desktop.{kind.value}.failed",
            resource_type="desktop", resource_id=desktop.id,
            details={"error": message},
        )
        await session.commit()
