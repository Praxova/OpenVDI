"""Desktop CRUD + async-flow endpoints (M2-15 scope).

Sync paths: list, detail (with active session + opportunistic live
power), assign, unassign. Async paths: power, rebuild, destroy — all
return 202 and route through M2-13's task tracker.

M2-13 invariant enforced at the schema boundary: `pve_task_upid` /
`pve_task_kind` never appear in responses. Operators poll `status` /
`error_message` for task progress.
"""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.router import admin_router
from app.database import get_db_session
from app.models import (
    Desktop,
    DesktopStatus,
    PoolType,
    Session as SessionModel,
    SessionStatus,
)
from app.providers.base import VMRef
from app.providers.exceptions import ProviderError
from app.schemas import (
    APIResponse,
    DesktopAssignRequest,
    DesktopRead,
    DesktopReadDetailed,
    PaginationParams,
    SessionRead,
    TaskAccepted,
)
from app.services.audit_service import log_business_event
from app.services.task_tracker import DesktopTaskKind, start_desktop_task


logger = logging.getLogger(__name__)


_DESKTOP_SORTABLE = frozenset(
    {"name", "pve_vmid", "status", "created_at", "last_connected"}
)

# Power actions mapped to the task-tracker kind the completion handler
# should run. reboot has no dedicated kind — it's observable as "should
# be running after" which is exactly what START does.
_POWER_ACTION_TO_KIND: dict[str, DesktopTaskKind] = {
    "start":    DesktopTaskKind.START,
    "stop":     DesktopTaskKind.STOP,
    "shutdown": DesktopTaskKind.SHUTDOWN,
    "reboot":   DesktopTaskKind.START,
}

_ACTIVE_SESSION_STATUSES = (SessionStatus.CONNECTING, SessionStatus.ACTIVE)


async def _count_active_sessions(
    session: AsyncSession, desktop_id: UUID,
) -> int:
    result = await session.scalar(
        select(func.count(SessionModel.id)).where(
            SessionModel.desktop_id == desktop_id,
            SessionModel.status.in_(_ACTIVE_SESSION_STATUSES),
        )
    )
    return int(result or 0)


# ── List / Detail ─────────────────────────────────────────────


@admin_router.get(
    "/desktops", response_model=APIResponse[list[DesktopRead]],
)
async def list_desktops(
    pool_id: UUID | None = Query(None),
    status: DesktopStatus | None = Query(None),
    assigned_user: str | None = Query(None),
    pagination: PaginationParams = Depends(),
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[list[DesktopRead]]:
    sort_key = pagination.sort or "pve_vmid"
    if sort_key not in _DESKTOP_SORTABLE:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_REQUEST",
                "message": f"sort must be one of {sorted(_DESKTOP_SORTABLE)}",
            },
        )

    stmt = select(Desktop)
    if pool_id is not None:
        stmt = stmt.where(Desktop.pool_id == pool_id)
    if status is not None:
        stmt = stmt.where(Desktop.status == status)
    if assigned_user:
        stmt = stmt.where(Desktop.assigned_user == assigned_user)

    col = getattr(Desktop, sort_key)
    stmt = (
        stmt
        .order_by(col.asc() if pagination.order == "asc" else col.desc())
        .limit(pagination.limit)
        .offset(pagination.offset)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return APIResponse(data=[DesktopRead.model_validate(d) for d in rows])


@admin_router.get(
    "/desktops/{desktop_id}",
    response_model=APIResponse[DesktopReadDetailed],
)
async def get_desktop(
    desktop_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[DesktopReadDetailed]:
    desktop = await session.get(
        Desktop, desktop_id,
        options=[selectinload(Desktop.pool)],
    )
    if desktop is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "desktop not found"},
        )

    active_session = (
        await session.execute(
            select(SessionModel)
            .where(
                SessionModel.desktop_id == desktop_id,
                SessionModel.status.in_(_ACTIVE_SESSION_STATUSES),
            )
            .order_by(SessionModel.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    live_power_state = desktop.power_state
    provider = request.app.state.providers.get(desktop.pool.cluster_id)
    if provider is not None:
        try:
            ref = VMRef(
                provider_type=provider.provider_type,
                data={"node": desktop.pve_node, "vmid": desktop.pve_vmid},
            )
            vm_status = await provider.get_vm_status(ref)
            live_power_state = vm_status.power_state
            if desktop.power_state != vm_status.power_state:
                # Opportunistic reconcile. Best-effort: a commit failure
                # here must not fail the read — the live state already
                # landed in the response.
                desktop.power_state = vm_status.power_state
                try:
                    await session.commit()
                except Exception as exc:
                    logger.warning(
                        "opportunistic power reconcile commit failed for "
                        "desktop %s: %s", desktop.name, exc,
                    )
                    await session.rollback()
        except ProviderError as exc:
            logger.info(
                "live power fetch failed for desktop %s: %s: %s",
                desktop.name, type(exc).__name__, exc,
            )

    base = DesktopRead.model_validate(desktop).model_dump()
    return APIResponse(
        data=DesktopReadDetailed(
            **base,
            active_session=(
                SessionRead.model_validate(active_session)
                if active_session is not None else None
            ),
            live_power_state=live_power_state,
        )
    )


# ── Assignment ────────────────────────────────────────────────


@admin_router.post(
    "/desktops/{desktop_id}/assign",
    response_model=APIResponse[DesktopRead],
)
async def assign_desktop(
    desktop_id: UUID,
    body: DesktopAssignRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[DesktopRead]:
    desktop = await session.get(
        Desktop, desktop_id, options=[selectinload(Desktop.pool)],
    )
    if desktop is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "desktop not found"},
        )

    if (
        desktop.assigned_user is not None
        and desktop.assigned_user != body.username
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CONFLICT",
                "message": (
                    f"desktop is already assigned to "
                    f"'{desktop.assigned_user}'; unassign first or "
                    "reassign via rebuild"
                ),
            },
        )

    # Per-user-per-pool invariant: a user holds at most one desktop in
    # a given pool. Manual assignment must respect it (the connect-flow
    # broker service enforces the same rule).
    existing = (
        await session.execute(
            select(Desktop).where(
                Desktop.pool_id == desktop.pool_id,
                Desktop.assigned_user == body.username,
                Desktop.id != desktop_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CONFLICT",
                "message": (
                    f"user '{body.username}' is already assigned to "
                    f"'{existing.name}' in this pool; unassign that "
                    "desktop first"
                ),
            },
        )

    desktop.assigned_user = body.username
    desktop.assignment_type = (
        "persistent" if desktop.pool.pool_type == PoolType.PERSISTENT
        else "floating"
    )
    if desktop.status == DesktopStatus.AVAILABLE:
        desktop.status = DesktopStatus.ASSIGNED

    await log_business_event(
        session=session,
        actor=request.state.user.username,
        action="desktop.assign",
        resource_type="desktop",
        resource_id=desktop_id,
        details={
            "assigned_to": body.username,
            "assignment_type": desktop.assignment_type,
        },
        client_ip=request.client.host if request.client else None,
    )
    await session.commit()
    await session.refresh(desktop)
    return APIResponse(data=DesktopRead.model_validate(desktop))


@admin_router.post(
    "/desktops/{desktop_id}/unassign",
    response_model=APIResponse[DesktopRead],
)
async def unassign_desktop(
    desktop_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[DesktopRead]:
    desktop = await session.get(Desktop, desktop_id)
    if desktop is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "desktop not found"},
        )

    if desktop.assigned_user is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CONFLICT",
                "message": "desktop is not assigned",
            },
        )

    active = await _count_active_sessions(session, desktop_id)
    if active > 0:
        # M4 adds force-disconnect; M2 keeps the safety posture.
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CONFLICT",
                "message": "desktop has an active session; end the session first",
            },
        )

    previous_user = desktop.assigned_user
    desktop.assigned_user = None
    desktop.assignment_type = None
    if desktop.status == DesktopStatus.ASSIGNED:
        desktop.status = DesktopStatus.AVAILABLE

    await log_business_event(
        session=session,
        actor=request.state.user.username,
        action="desktop.unassign",
        resource_type="desktop",
        resource_id=desktop_id,
        details={"previous_user": previous_user},
        client_ip=request.client.host if request.client else None,
    )
    await session.commit()
    await session.refresh(desktop)
    return APIResponse(data=DesktopRead.model_validate(desktop))


# ── Async: power ──────────────────────────────────────────────


@admin_router.post(
    "/desktops/{desktop_id}/power/{action}",
    status_code=202,
    response_model=APIResponse[TaskAccepted],
)
async def desktop_power(
    desktop_id: UUID,
    action: str,
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[TaskAccepted]:
    if action not in _POWER_ACTION_TO_KIND:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_REQUEST",
                "message": (
                    f"action must be one of {sorted(_POWER_ACTION_TO_KIND)}"
                ),
            },
        )

    desktop = await session.get(
        Desktop, desktop_id, options=[selectinload(Desktop.pool)],
    )
    if desktop is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "desktop not found"},
        )

    if desktop.pve_task_upid is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CONFLICT",
                "message": (
                    f"desktop already has a '{desktop.pve_task_kind}' task "
                    "in flight"
                ),
            },
        )

    provider = request.app.state.providers.get(desktop.pool.cluster_id)
    if provider is None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_REQUEST",
                "message": "cluster has no active provider",
            },
        )

    ref = VMRef(
        provider_type=provider.provider_type,
        data={"node": desktop.pve_node, "vmid": desktop.pve_vmid},
    )
    if action == "start":
        handle = await provider.start_vm(ref)
    elif action == "stop":
        handle = await provider.stop_vm(ref)
    elif action == "shutdown":
        handle = await provider.shutdown_vm(ref, timeout_seconds=120, force=True)
    else:  # reboot
        handle = await provider.reboot_vm(ref)

    kind = _POWER_ACTION_TO_KIND[action]
    await start_desktop_task(
        session=session,
        desktop=desktop,
        kind=kind,
        task_handle=handle,
        background_tasks=background_tasks,
    )
    # CRITICAL: commit before returning — start_desktop_task writes the
    # UPID to the row and schedules the poller, which runs after the
    # response. Pre-commit, the poller opens its own session and can't
    # see the UPID.
    await session.commit()

    return APIResponse(
        data=TaskAccepted(
            desktop_id=desktop_id,
            action=action,
            message=(
                f"{action} task accepted; poll GET /desktops/{{id}} for "
                "progress"
            ),
        )
    )


# ── Async: rebuild ────────────────────────────────────────────


@admin_router.post(
    "/desktops/{desktop_id}/rebuild",
    status_code=202,
    response_model=APIResponse[TaskAccepted],
)
async def rebuild_desktop(
    desktop_id: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[TaskAccepted]:
    desktop = await session.get(
        Desktop, desktop_id, options=[selectinload(Desktop.pool)],
    )
    if desktop is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "desktop not found"},
        )
    if desktop.pve_task_upid is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CONFLICT",
                "message": (
                    f"desktop already has a '{desktop.pve_task_kind}' task "
                    "in flight"
                ),
            },
        )

    active = await _count_active_sessions(session, desktop_id)
    if active > 0:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CONFLICT",
                "message": (
                    "desktop has an active session; end the session before "
                    "rebuilding"
                ),
            },
        )

    provider = request.app.state.providers.get(desktop.pool.cluster_id)
    if provider is None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_REQUEST",
                "message": "cluster has no active provider",
            },
        )

    ref = VMRef(
        provider_type=provider.provider_type,
        data={"node": desktop.pve_node, "vmid": desktop.pve_vmid},
    )

    # Ensure VM is stopped before destroy. Sync wait — this block runs
    # during the HTTP request so the caller gets a clear 5xx if stop
    # fails (vs. a silent failure inside the background shim).
    vm_status = await provider.get_vm_status(ref)
    if vm_status.power_state == "running":
        stop_handle = await provider.stop_vm(ref)
        await provider.wait_for_task(stop_handle, timeout_seconds=30)

    destroy_handle = await provider.destroy_vm(ref)

    desktop.status = DesktopStatus.DELETING
    await start_desktop_task(
        session=session,
        desktop=desktop,
        kind=DesktopTaskKind.REBUILD,
        task_handle=destroy_handle,
        background_tasks=background_tasks,
    )
    await session.commit()

    return APIResponse(
        data=TaskAccepted(
            desktop_id=desktop_id,
            action="rebuild",
            message=(
                "rebuild started; the desktop will be re-provisioned after "
                "destroy completes"
            ),
        )
    )


# ── Async: destroy ────────────────────────────────────────────


@admin_router.delete(
    "/desktops/{desktop_id}",
    status_code=202,
    response_model=APIResponse[TaskAccepted],
)
async def destroy_desktop(
    desktop_id: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[TaskAccepted]:
    desktop = await session.get(
        Desktop, desktop_id, options=[selectinload(Desktop.pool)],
    )
    if desktop is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "desktop not found"},
        )
    if desktop.pve_task_upid is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CONFLICT",
                "message": (
                    f"desktop already has a '{desktop.pve_task_kind}' task "
                    "in flight"
                ),
            },
        )

    active = await _count_active_sessions(session, desktop_id)
    if active > 0:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CONFLICT",
                "message": (
                    "desktop has an active session; end the session before "
                    "destroying"
                ),
            },
        )

    provider = request.app.state.providers.get(desktop.pool.cluster_id)
    if provider is None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_REQUEST",
                "message": "cluster has no active provider",
            },
        )

    ref = VMRef(
        provider_type=provider.provider_type,
        data={"node": desktop.pve_node, "vmid": desktop.pve_vmid},
    )

    vm_status = await provider.get_vm_status(ref)
    if vm_status.power_state == "running":
        stop_handle = await provider.stop_vm(ref)
        await provider.wait_for_task(stop_handle, timeout_seconds=30)

    destroy_handle = await provider.destroy_vm(ref)

    desktop.status = DesktopStatus.DELETING
    await start_desktop_task(
        session=session,
        desktop=desktop,
        kind=DesktopTaskKind.DESTROY,
        task_handle=destroy_handle,
        background_tasks=background_tasks,
    )
    await session.commit()

    return APIResponse(
        data=TaskAccepted(
            desktop_id=desktop_id,
            action="destroy",
            message=(
                "destroy started; row will be removed after VM destruction "
                "completes"
            ),
        )
    )
