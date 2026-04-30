"""Pool CRUD + async-flow endpoints (M2-15 scope).

The heavy module of M2: sync CRUD + three async flows — provision,
drain, and cascade delete. Drain and delete are the ones that matter
for operators running a live broker; they're what keep a shipped pool
from turning into a rehabilitation project.

Patterns established in M2-14 apply:
- Every handler returns APIResponse[...] (handlers never shape errors).
- Sort keys go through a per-resource allow-list (SQL-injection guard).
- 404/400/409 via HTTPException with a `code`/`message` detail dict.
- 422 happens automatically from Pydantic validation.

Caller-commits-before-202 is the rule for async handlers (M2-13 +
M4-10): the DB must reflect the task's UPID before the response
returns, otherwise the task_tracker worker reads pre-commit state on
its next tick. The two pool endpoints here use BackgroundTasks for
multi-step fan-out (provision N desktops, destroy a pool's worth of
desktops); per-desktop UPID-tracked endpoints in api/desktops.py use
the M4-10 record_desktop_task path instead.
"""
from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from fastapi import BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.router import admin_router
from app.database import async_session_factory, get_db_session
from app.models import (
    Cluster,
    Desktop,
    DesktopStatus,
    Pool,
    PoolStatus,
    Session as SessionModel,
    SessionStatus,
    Template,
)
from app.providers.base import VMRef
from app.providers.exceptions import ProviderError, ProviderNotFoundError
from app.schemas import (
    APIResponse,
    DrainAccepted,
    PaginationParams,
    PoolCapacityDetail,
    PoolCreate,
    PoolDeleteAccepted,
    PoolRead,
    PoolReadDetailed,
    PoolUpdate,
    ProvisionAccepted,
    ProvisionRequest,
)
from app.schemas.desktop import DesktopRead
from app.services.audit_service import log_business_event
from app.services.vmid_allocator import validate_pool_range


logger = logging.getLogger(__name__)


_POOL_SORTABLE = frozenset(
    {"name", "created_at", "updated_at", "status", "vmid_range_start"}
)

# Fields that cannot change after creation. PUT with any of these → 400.
# `pool_type` / `template_id` / `cluster_id` would invalidate VMID-range
# math; `name_prefix` is baked into the names of existing desktops;
# `pve_pool_id` would orphan tags on already-cloned VMs.
_POOL_IMMUTABLE_FIELDS = frozenset({
    "name", "pool_type", "template_id", "cluster_id",
    "vmid_range_start", "vmid_range_end", "name_prefix", "pve_pool_id",
})


# ── Active-session helper ─────────────────────────────────────


_ACTIVE_SESSION_STATUSES = (SessionStatus.CONNECTING, SessionStatus.ACTIVE)


async def _count_active_sessions_for_pool(
    session: AsyncSession, pool_id: UUID,
) -> int:
    result = await session.scalar(
        select(func.count(SessionModel.id))
        .join(Desktop, SessionModel.desktop_id == Desktop.id)
        .where(
            Desktop.pool_id == pool_id,
            SessionModel.status.in_(_ACTIVE_SESSION_STATUSES),
        )
    )
    return int(result or 0)


async def _compute_capacity(
    session: AsyncSession, pool: Pool,
) -> PoolCapacityDetail:
    """Single grouped-count query for per-status desktop counts.

    `deleting`-state desktops are intentionally counted toward
    `total_desktops` — their VMIDs aren't actually free until destroy
    completes on the provider side, so treating them as occupying
    slots is correct.
    """
    result = await session.execute(
        select(Desktop.status, func.count(Desktop.id))
        .where(Desktop.pool_id == pool.id)
        .group_by(Desktop.status)
    )
    counts = {status.value: 0 for status in DesktopStatus}
    total_rows = 0
    for status, count in result.all():
        key = status.value if hasattr(status, "value") else str(status)
        counts[key] = int(count)
        total_rows += int(count)

    range_capacity = pool.vmid_range_end - pool.vmid_range_start + 1
    return PoolCapacityDetail(
        range_capacity=range_capacity,
        total_desktops=total_rows,
        free_slots=range_capacity - total_rows,
        **counts,
    )


# ── List / Create / Read / Update ─────────────────────────────


@admin_router.get(
    "/pools", response_model=APIResponse[list[PoolRead]],
)
async def list_pools(
    pagination: PaginationParams = Depends(),
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[list[PoolRead]]:
    sort_key = pagination.sort or "name"
    if sort_key not in _POOL_SORTABLE:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_REQUEST",
                "message": f"sort must be one of {sorted(_POOL_SORTABLE)}",
            },
        )
    col = getattr(Pool, sort_key)
    stmt = (
        select(Pool)
        .order_by(col.asc() if pagination.order == "asc" else col.desc())
        .limit(pagination.limit)
        .offset(pagination.offset)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return APIResponse(data=[PoolRead.model_validate(p) for p in rows])


@admin_router.post(
    "/pools",
    status_code=201,
    response_model=APIResponse[PoolRead],
)
async def create_pool(
    body: PoolCreate,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[PoolRead]:
    cluster = await session.get(Cluster, body.cluster_id)
    if cluster is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "NOT_FOUND",
                "message": f"cluster {body.cluster_id} not found",
            },
        )
    template = await session.get(Template, body.template_id)
    if template is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "NOT_FOUND",
                "message": f"template {body.template_id} not found",
            },
        )
    if template.cluster_id != cluster.id:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_REQUEST",
                "message": "template.cluster_id must match the pool's cluster_id",
            },
        )

    provider = request.app.state.providers.get(cluster.id)
    if provider is None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_REQUEST",
                "message": (
                    f"cluster '{cluster.name}' has no active provider "
                    f"(status={cluster.status}); cannot validate VMID range"
                ),
            },
        )

    # Raises VMIDRangeConflict → 409 via M2-11 handler.
    await validate_pool_range(
        session=session,
        provider=provider,
        vmid_range_start=body.vmid_range_start,
        vmid_range_end=body.vmid_range_end,
    )

    existing = (
        await session.execute(
            select(Pool).where(Pool.name == body.name)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CONFLICT",
                "message": f"pool '{body.name}' already exists",
            },
        )

    # PVE-native pool creation is deliberately NOT implemented. If
    # body.pve_pool_id references a PVE pool that doesn't exist, the
    # first clone into the pool will fail with a clear Proxmox error.
    # Operator runbook: `pvesh create /pools --poolid <id>`.

    pool = Pool(
        name=body.name,
        display_name=body.display_name,
        description=body.description,
        pool_type=body.pool_type,
        template_id=body.template_id,
        cluster_id=body.cluster_id,
        min_spare=body.min_spare,
        max_size=body.max_size,
        vmid_range_start=body.vmid_range_start,
        vmid_range_end=body.vmid_range_end,
        name_prefix=body.name_prefix,
        target_nodes=body.target_nodes,
        target_storage=body.target_storage,
        cpu_cores=body.cpu_cores,
        memory_mb=body.memory_mb,
        pve_pool_id=body.pve_pool_id,
        provider_config=body.provider_config or {},
        auto_logoff_min=body.auto_logoff_min,
        delete_on_logoff=body.delete_on_logoff,
        refresh_on_logoff=body.refresh_on_logoff,
        status=PoolStatus.ACTIVE,
    )
    session.add(pool)
    await session.commit()
    await session.refresh(pool)
    return APIResponse(data=PoolRead.model_validate(pool))


@admin_router.get(
    "/pools/{pool_id}",
    response_model=APIResponse[PoolReadDetailed],
)
async def get_pool(
    pool_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[PoolReadDetailed]:
    pool = await session.get(Pool, pool_id)
    if pool is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "pool not found"},
        )

    capacity = await _compute_capacity(session, pool)

    desktops = (
        await session.execute(
            select(Desktop)
            .where(Desktop.pool_id == pool_id)
            .order_by(Desktop.pve_vmid)
        )
    ).scalars().all()

    base = PoolRead.model_validate(pool).model_dump()
    return APIResponse(
        data=PoolReadDetailed(
            **base,
            capacity=capacity,
            desktops=[DesktopRead.model_validate(d) for d in desktops],
        )
    )


@admin_router.put(
    "/pools/{pool_id}",
    response_model=APIResponse[PoolRead],
)
async def update_pool(
    pool_id: UUID,
    body: PoolUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[PoolRead]:
    pool = await session.get(Pool, pool_id)
    if pool is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "pool not found"},
        )

    update_data = body.model_dump(exclude_unset=True)

    submitted_immutable = sorted(set(update_data) & _POOL_IMMUTABLE_FIELDS)
    if submitted_immutable:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_REQUEST",
                "message": (
                    f"fields {submitted_immutable} are immutable after pool "
                    "creation; delete and recreate the pool to change these"
                ),
            },
        )

    # `status` is not settable via PUT. Drain + delete have their own
    # endpoints so the side-effects (audit lines, active-session guard,
    # background shim scheduling) fire in one place.
    if "status" in update_data:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_REQUEST",
                "message": (
                    "status is not settable via PUT — use POST /pools/{id}/drain "
                    "or DELETE /pools/{id} for lifecycle transitions"
                ),
            },
        )

    if "max_size" in update_data:
        new_max = update_data["max_size"]
        range_capacity = pool.vmid_range_end - pool.vmid_range_start + 1
        if new_max > range_capacity:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "INVALID_REQUEST",
                    "message": (
                        f"max_size {new_max} exceeds VMID range capacity "
                        f"{range_capacity}"
                    ),
                },
            )
        current_count = await session.scalar(
            select(func.count(Desktop.id)).where(Desktop.pool_id == pool_id)
        )
        current_count = int(current_count or 0)
        if new_max < current_count:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "INVALID_REQUEST",
                    "message": (
                        f"max_size {new_max} is below current desktop count "
                        f"{current_count}; destroy desktops first or drain the pool"
                    ),
                },
            )

    for field, value in update_data.items():
        setattr(pool, field, value)
    await session.commit()
    await session.refresh(pool)
    return APIResponse(data=PoolRead.model_validate(pool))


# ── Async: delete cascade ─────────────────────────────────────


@admin_router.delete(
    "/pools/{pool_id}",
    status_code=202,
    response_model=APIResponse[PoolDeleteAccepted],
)
async def delete_pool(
    pool_id: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[PoolDeleteAccepted]:
    pool = await session.get(Pool, pool_id)
    if pool is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "pool not found"},
        )

    # Idempotent-ish: re-hitting DELETE on a pool already in `deleting`
    # state returns 202 with the current remaining count instead of
    # 409ing. Operators may need this after a partial-failure shim
    # leaves some desktops in `error` — they destroy those manually,
    # then re-issue DELETE /pools/{id} to re-run the cascade.
    if pool.status == PoolStatus.DELETING:
        remaining = await session.scalar(
            select(func.count(Desktop.id)).where(Desktop.pool_id == pool_id)
        )
        remaining = int(remaining or 0)
        if remaining == 0:
            # Previous cascade succeeded on all desktops but failed to
            # remove the pool row (or is currently mid-cascade with zero
            # desktops to begin with). Re-schedule the shim so the pool
            # row gets cleaned up.
            background_tasks.add_task(_delete_pool_shim, pool_id)
        else:
            background_tasks.add_task(_delete_pool_shim, pool_id)
        return APIResponse(
            data=PoolDeleteAccepted(
                pool_id=pool_id,
                message=(
                    f"pool is already being deleted; {remaining} desktop(s) "
                    "remain"
                ),
                desktops_to_destroy=remaining,
            )
        )

    active_count = await _count_active_sessions_for_pool(session, pool_id)
    if active_count > 0:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CONFLICT",
                "message": (
                    f"pool has {active_count} active session(s); drain the "
                    "pool and wait for sessions to end first"
                ),
            },
        )

    pool.status = PoolStatus.DELETING

    desktop_count = await session.scalar(
        select(func.count(Desktop.id)).where(Desktop.pool_id == pool_id)
    )
    desktop_count = int(desktop_count or 0)

    await log_business_event(
        session=session,
        actor=request.state.user.username,
        action="pool.delete.started",
        resource_type="pool",
        resource_id=pool_id,
        details={
            "pool_name": pool.name,
            "desktops_to_destroy": desktop_count,
        },
        client_ip=request.client.host if request.client else None,
    )
    # CRITICAL: commit before scheduling — BackgroundTasks runs after
    # the response, and the shim opens its own session.
    await session.commit()

    background_tasks.add_task(_delete_pool_shim, pool_id)

    return APIResponse(
        data=PoolDeleteAccepted(
            pool_id=pool_id,
            message=(
                f"pool deletion started; {desktop_count} desktop(s) will be "
                "destroyed before the pool row is removed"
            ),
            desktops_to_destroy=desktop_count,
        )
    )


async def _delete_pool_shim(pool_id: UUID) -> None:
    """Background cascade: destroy every desktop, then delete the pool.

    - Parallel destroys via asyncio.gather(return_exceptions=True) — one
      failure shouldn't abort the others.
    - Pool row only deleted when ALL destroys succeed; any failure
      leaves the pool in `deleting` state with the failed desktops in
      `error` state for operator inspection. Operator can `DELETE` the
      error desktops manually (M2-13) then re-issue `DELETE /pools/{id}`
      to re-run.
    - No silent auto-cleanup of partial failures — it would mask real
      Proxmox issues. M4's health-check worker may add a stale-state
      alerter.
    """
    from app.main import app  # deferred: avoid circular

    async with async_session_factory() as session:
        pool = await session.get(Pool, pool_id)
        if pool is None:
            logger.error(
                "pool %s vanished before delete shim ran", pool_id,
            )
            return
        cluster_id = pool.cluster_id
        desktops = (
            await session.execute(
                select(Desktop).where(Desktop.pool_id == pool_id)
            )
        ).scalars().all()
        desktop_ids = [d.id for d in desktops]

    provider = app.state.providers.get(cluster_id)
    if provider is None:
        logger.error(
            "no provider for cluster %s; cannot delete pool %s",
            cluster_id, pool_id,
        )
        # Leave pool in 'deleting'; operator fixes cluster, re-issues
        # DELETE /pools/{id} to resume.
        return

    async def _destroy_one(desktop_id: UUID) -> bool:
        async with async_session_factory() as s:
            desktop = await s.get(Desktop, desktop_id)
            if desktop is None:
                return True  # already gone; treat as success

            ref = VMRef(
                provider_type=provider.provider_type,
                data={"node": desktop.pve_node, "vmid": desktop.pve_vmid},
            )
            try:
                vm_status = await provider.get_vm_status(ref)
                if vm_status.power_state == "running":
                    stop_handle = await provider.stop_vm(ref)
                    await provider.wait_for_task(
                        stop_handle, timeout_seconds=30,
                    )
                destroy_handle = await provider.destroy_vm(ref)
                await provider.wait_for_task(
                    destroy_handle, timeout_seconds=300,
                )
            except ProviderNotFoundError:
                # VM already gone on provider; proceed to delete the row.
                logger.info(
                    "desktop %s VM already gone; removing row",
                    desktop.name,
                )
            except ProviderError as exc:
                logger.warning(
                    "desktop %s destroy failed in pool-delete cascade: %s: %s",
                    desktop.name, type(exc).__name__, exc,
                )
                desktop.status = DesktopStatus.ERROR
                desktop.error_message = (
                    f"pool delete cascade: {type(exc).__name__}: {exc}"
                )
                await s.commit()
                return False

            await s.delete(desktop)
            await s.commit()
            return True

    results = await asyncio.gather(
        *(_destroy_one(did) for did in desktop_ids),
        return_exceptions=True,
    )
    successes = sum(1 for r in results if r is True)
    failures = len(results) - successes

    if failures > 0:
        logger.error(
            "pool %s delete cascade: %d of %d desktops failed; "
            "pool row will NOT be removed",
            pool_id, failures, len(results),
        )
        return

    async with async_session_factory() as session:
        pool = await session.get(Pool, pool_id)
        if pool is None:
            return  # concurrent deletion; fine

        await log_business_event(
            session=session,
            actor="system",
            action="pool.delete.completed",
            resource_type="pool",
            resource_id=pool_id,
            details={
                "pool_name": pool.name,
                "desktops_destroyed": successes,
            },
        )
        await session.delete(pool)
        await session.commit()

    logger.info(
        "pool %s deleted (%d desktops destroyed)", pool_id, successes,
    )


# ── Async: provision ──────────────────────────────────────────


@admin_router.post(
    "/pools/{pool_id}/provision",
    status_code=202,
    response_model=APIResponse[ProvisionAccepted],
)
async def provision_pool(
    pool_id: UUID,
    body: ProvisionRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[ProvisionAccepted]:
    pool = await session.get(
        Pool, pool_id, options=[selectinload(Pool.template)],
    )
    if pool is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "pool not found"},
        )
    if pool.status != PoolStatus.ACTIVE:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CONFLICT",
                "message": (
                    f"pool status is '{pool.status.value}'; must be 'active' "
                    "to provision"
                ),
            },
        )

    # Capacity check. `deleting` desktops still occupy slots (their
    # VMIDs aren't free yet) so they count against max_size.
    current_count = await session.scalar(
        select(func.count(Desktop.id)).where(Desktop.pool_id == pool_id)
    )
    current_count = int(current_count or 0)
    available_slots = pool.max_size - current_count
    if body.count > available_slots:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CONFLICT",
                "message": (
                    f"requested {body.count} desktops but only "
                    f"{available_slots} slot(s) available "
                    f"(max_size={pool.max_size}, current={current_count})"
                ),
            },
        )

    provider = request.app.state.providers.get(pool.cluster_id)
    if provider is None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_REQUEST",
                "message": "cluster has no active provider; cannot provision",
            },
        )

    # No state change on the pool row itself; the shim does all the
    # writing. But commit anyway — M2-13 caller-commits-before-202
    # discipline, and a no-op commit is cheap.
    await session.commit()

    background_tasks.add_task(_provision_shim, pool_id, body.count)

    return APIResponse(
        data=ProvisionAccepted(
            pool_id=pool_id,
            count_requested=body.count,
            message=(
                "provisioning started; poll GET /pools/{id} for progress"
            ),
        )
    )


async def _provision_shim(pool_id: UUID, count: int) -> None:
    """Parallel provision. One session per concurrent task.

    provision_desktop acquires its own advisory lock for VMID allocation;
    concurrent instances race for the lock one-at-a-time so there's no
    risk of VMID collisions. Failed provisions leave `error`-state
    Desktop rows per M2-07's S-B3 contract; return_exceptions=True
    keeps one failure from aborting the others.
    """
    from app.main import app  # deferred
    from app.services.provisioner import provision_desktop

    async with async_session_factory() as bootstrap:
        pool = await bootstrap.get(
            Pool, pool_id, options=[selectinload(Pool.template)],
        )
        if pool is None:
            logger.error(
                "pool %s vanished before provision shim ran", pool_id,
            )
            return
        cluster_id = pool.cluster_id

    provider = app.state.providers.get(cluster_id)
    if provider is None:
        logger.error(
            "no provider for cluster %s; cannot provision pool %s",
            cluster_id, pool_id,
        )
        return

    async def _one() -> None:
        async with async_session_factory() as s:
            p = await s.get(
                Pool, pool_id, options=[selectinload(Pool.template)],
            )
            if p is None or p.template is None:
                return
            await provision_desktop(
                session=s,
                provider=provider,
                pool=p,
                template=p.template,
                assigned_user=None,
            )
            await s.commit()

    await asyncio.gather(
        *(_one() for _ in range(count)),
        return_exceptions=True,
    )


# ── Sync: drain ───────────────────────────────────────────────


@admin_router.post(
    "/pools/{pool_id}/drain",
    status_code=202,
    response_model=APIResponse[DrainAccepted],
)
async def drain_pool(
    pool_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[DrainAccepted]:
    pool = await session.get(Pool, pool_id)
    if pool is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "pool not found"},
        )
    if pool.status == PoolStatus.DRAINING:
        active_count = await _count_active_sessions_for_pool(session, pool_id)
        return APIResponse(
            data=DrainAccepted(
                pool_id=pool_id,
                message="pool already draining",
                active_sessions=active_count,
            )
        )
    if pool.status != PoolStatus.ACTIVE:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CONFLICT",
                "message": (
                    f"pool status is '{pool.status.value}'; can only drain "
                    "'active' pools"
                ),
            },
        )

    pool.status = PoolStatus.DRAINING

    await log_business_event(
        session=session,
        actor=request.state.user.username,
        action="pool.drain.started",
        resource_type="pool",
        resource_id=pool_id,
        details={"pool_name": pool.name},
        client_ip=request.client.host if request.client else None,
    )
    await session.commit()

    active_count = await _count_active_sessions_for_pool(session, pool_id)

    return APIResponse(
        data=DrainAccepted(
            pool_id=pool_id,
            message=(
                f"pool draining; {active_count} active session(s) will "
                "finish naturally"
            ),
            active_sessions=active_count,
        )
    )
