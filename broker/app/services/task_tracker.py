"""Per-task background worker for async provider operations.

When an endpoint kicks off a long-running provider op (destroy, rebuild,
power transition) and returns 202, `start_desktop_task` records the UPID
on the Desktop row and schedules `poll_desktop_task` via FastAPI's
`BackgroundTasks`. The background worker polls `provider.wait_for_task`
to completion, updates the Desktop row based on the outcome, and clears
the task fields.

At broker startup, `resume_inflight_tasks` scans for Desktop rows with
non-null `pve_task_upid` — tasks in flight from before the restart —
and spawns pollers for each against the already-constructed providers
in `app.state.providers`.

DB-is-source-of-truth (W-1). If `pve_task_upid IS NOT NULL`, something
is in flight; the `pve_task_kind` says what completion means. No tasks
table, no per-step progress tracking. One UPID + one kind per row.

M2 does NOT build the 5-second polling daemon described in
`docs/session-tracking.md` → *Task Tracker Background Worker*. That's
M4. The per-task worker here is a subset that's additive/subtractive
from the daemon design.

Caller contract for `start_desktop_task`: the caller must commit the
DB transaction BEFORE returning the 202 response. FastAPI runs
`BackgroundTasks` after the response is sent; if the UPID is still in
an open transaction, the worker's own session won't see it.
"""
from __future__ import annotations

import asyncio
import enum
import logging
from typing import TYPE_CHECKING, Callable
from uuid import UUID

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import async_session_factory
from app.models import Desktop, DesktopStatus, Pool
from app.providers.base import HypervisorProvider, TaskHandle, TaskStatus
from app.providers.exceptions import (
    ProviderError,
    ProviderTaskError,
    ProviderTimeoutError,
)
from app.services.audit_service import log_business_event

if TYPE_CHECKING:
    from fastapi import FastAPI


logger = logging.getLogger(__name__)


class DesktopTaskKind(str, enum.Enum):
    """What operation produced the in-flight task.

    The string value is what lands in `desktop.pve_task_kind`; the
    completion handler branches on kind to decide what the Desktop row
    should look like post-completion.
    """

    PROVISION = "provision"   # clone → configure → start → (snapshot) → available
    DESTROY = "destroy"       # stop + DELETE → drop DB row
    REBUILD = "rebuild"       # destroy leg; provision leg chains after success
    START = "start"
    SHUTDOWN = "shutdown"
    STOP = "stop"


# Per-kind timeout budgets. Provision and rebuild can be minutes on
# LVM-thin; destroy is usually quick; power transitions are seconds.
# No caller-side override in M2 (scope guardrail).
_KIND_TIMEOUTS: dict[DesktopTaskKind, int] = {
    DesktopTaskKind.PROVISION: 900,
    DesktopTaskKind.DESTROY:   300,
    DesktopTaskKind.REBUILD:   1200,
    DesktopTaskKind.START:      60,
    DesktopTaskKind.SHUTDOWN:  180,
    DesktopTaskKind.STOP:       30,
}


def _timeout_for_kind(kind: DesktopTaskKind) -> int:
    return _KIND_TIMEOUTS.get(kind, 600)


# ── Public surface ────────────────────────────────────────────

async def start_desktop_task(
    *,
    session: AsyncSession,
    desktop: Desktop,
    kind: DesktopTaskKind,
    task_handle: TaskHandle,
    background_tasks: BackgroundTasks,
) -> None:
    """Record a task UPID on a Desktop and schedule a background poll.

    Caller contract:
        1. Issue the provider call yourself; get a TaskHandle.
        2. Call this function.
        3. Commit the session.
        4. Return the 202 response.

    FastAPI runs BackgroundTasks after the response is sent — which is
    after step 3. If step 3 is skipped or reordered, the worker runs
    before the UPID is visible and nothing happens useful.

    The function does NOT commit. It does NOT issue the provider call.
    It only persists the UPID / kind and enqueues the poll worker.
    """
    desktop.pve_task_upid = task_handle.data["upid"]
    desktop.pve_task_kind = kind.value

    # Capture values now — the worker opens its own session and cannot
    # read from the caller's session after this function returns.
    # desktop.pool must have been selectinloaded by the caller;
    # accessing cluster_id via the relationship triggers lazy-load
    # otherwise, which is a noload with our default relationship config.
    cluster_id = desktop.pool.cluster_id
    desktop_id = desktop.id

    def _provider_factory() -> HypervisorProvider:
        # Deferred import avoids the circular app.main → services.
        from app.main import app  # noqa: WPS433
        provider = app.state.providers.get(cluster_id)
        if provider is None:
            raise RuntimeError(
                f"no provider for cluster {cluster_id} at task-poll time"
            )
        return provider

    background_tasks.add_task(
        poll_desktop_task, desktop_id, kind, _provider_factory,
    )


async def poll_desktop_task(
    desktop_id: UUID,
    kind: DesktopTaskKind,
    provider_factory: Callable[[], HypervisorProvider],
) -> None:
    """Background worker — poll a single task to completion.

    Opens its own DB session. Drops it for the duration of the poll
    (Proxmox task waits can be minutes; holding a connection for that
    long starves the pool). Re-opens to write the outcome.
    """
    logger.info(
        "polling %s task for desktop %s", kind.value, desktop_id,
    )

    # Read the current UPID + node. If the desktop row is gone or the
    # UPID has been cleared already, there's nothing to poll.
    async with async_session_factory() as session:
        desktop = await session.get(Desktop, desktop_id)
        if desktop is None:
            logger.warning(
                "desktop %s vanished before task completion", desktop_id,
            )
            return
        if desktop.pve_task_upid is None:
            logger.info(
                "desktop %s has no UPID; another worker handled it",
                desktop_id,
            )
            return
        upid = desktop.pve_task_upid
        node = desktop.pve_node

    try:
        provider = provider_factory()
    except RuntimeError as exc:
        logger.error(
            "cannot resolve provider for desktop %s: %s", desktop_id, exc,
        )
        await _mark_task_error(
            desktop_id, kind, f"provider unavailable: {exc}",
        )
        return

    handle = TaskHandle(
        provider_type=provider.provider_type,
        data={"node": node, "upid": upid},
    )

    timeout = _timeout_for_kind(kind)
    try:
        status = await provider.wait_for_task(handle, timeout_seconds=timeout)
    except ProviderTimeoutError as exc:
        logger.warning("task timeout for desktop %s: %s", desktop_id, exc)
        await _mark_task_error(desktop_id, kind, f"task timeout: {exc}")
        return
    except ProviderTaskError as exc:
        logger.warning("task failed for desktop %s: %s", desktop_id, exc)
        await _mark_task_error(desktop_id, kind, str(exc))
        return
    except ProviderError as exc:
        logger.exception(
            "unexpected provider error polling desktop %s", desktop_id,
        )
        await _mark_task_error(
            desktop_id, kind, f"{type(exc).__name__}: {exc}",
        )
        return

    await _apply_task_success(desktop_id, kind, status, provider_factory)


async def resume_inflight_tasks(app: "FastAPI") -> None:
    """Startup hook. Spawn a poller for every Desktop row with a UPID.

    Called from main.py's lifespan after providers are constructed and
    after the initial cluster pings are spawned. Fires-and-forgets
    into app.state.task_tracker_tasks; does NOT block lifespan on task
    completion.
    """
    async with async_session_factory() as session:
        orphans = (
            await session.execute(
                select(Desktop)
                .where(Desktop.pve_task_upid.isnot(None))
                .options(selectinload(Desktop.pool))
            )
        ).scalars().all()

    if not orphans:
        logger.info("no in-flight tasks to resume")
        return

    logger.info(
        "resuming %d in-flight task(s) from prior broker run",
        len(orphans),
    )

    for desktop in orphans:
        if desktop.pve_task_kind is None:
            logger.warning(
                "desktop %s has UPID %s but no kind — marking error for "
                "operator review",
                desktop.id, desktop.pve_task_upid,
            )
            # Kind is unknown; use PROVISION for the error-message label
            # only. _mark_task_error doesn't branch on kind itself.
            await _mark_task_error(
                desktop.id,
                DesktopTaskKind.PROVISION,
                "task kind unknown after broker restart — operator cleanup required",
            )
            continue

        try:
            kind = DesktopTaskKind(desktop.pve_task_kind)
        except ValueError:
            logger.warning(
                "desktop %s has unknown kind %r — marking error",
                desktop.id, desktop.pve_task_kind,
            )
            await _mark_task_error(
                desktop.id,
                DesktopTaskKind.PROVISION,
                f"unknown task kind {desktop.pve_task_kind!r} after broker restart",
            )
            continue

        cluster_id = desktop.pool.cluster_id

        # Default-arg idiom binds cluster_id per-iteration; without it
        # every closure would close over the loop variable and see the
        # last iteration's value.
        def _factory(cid: UUID = cluster_id) -> HypervisorProvider:
            provider = app.state.providers.get(cid)
            if provider is None:
                raise RuntimeError(f"no provider for cluster {cid}")
            return provider

        task = asyncio.create_task(
            poll_desktop_task(desktop.id, kind, _factory)
        )
        app.state.task_tracker_tasks.add(task)
        task.add_done_callback(app.state.task_tracker_tasks.discard)


# ── Completion handlers ───────────────────────────────────────

async def _apply_task_success(
    desktop_id: UUID,
    kind: DesktopTaskKind,
    status: TaskStatus,
    provider_factory: Callable[[], HypervisorProvider],
) -> None:
    """Happy-path row update for a completed task.

    The provider_factory is passed in so the REBUILD second leg (which
    invokes the provisioner) can look up the provider without re-going
    through app.state. Only REBUILD actually needs it; other kinds
    ignore the arg.
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
            # Destroy leg succeeded. Stash what we need for the provision
            # leg, clear the task fields, commit, then re-provision.
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
    """Transition the Desktop row to `error` with a descriptive message."""
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
