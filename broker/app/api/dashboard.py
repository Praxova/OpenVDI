"""Admin dashboard endpoints (M2-17).

Two read-only views:
  GET /dashboard/summary    aggregate counts across the whole broker
  GET /dashboard/capacity   per-pool capacity breakdown

Both run on every request — no caching in M2 (premature). Indexes on
status columns make the COUNTs fast at M2 scale (hundreds of desktops).

Notable scope guardrails (see m2-17 prompt):
- No per-cluster live node health rolled into summary (would require
  cross-network calls per cluster — flaky and slow).
- No historical / time-series rollups — those belong in audit-log
  query land, not the "current state" dashboard.
"""
from __future__ import annotations

import logging

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.pools import _compute_capacity
from app.api.router import admin_router
from app.database import get_db_session
from app.models import (
    Cluster,
    Desktop,
    Pool,
    Session as SessionModel,
    SessionStatus,
)
from app.schemas import (
    APIResponse,
    CapacitySummary,
    DashboardSummary,
    PoolCapacityWithName,
    PoolSummaryCounts,
    ResourceStatusCounts,
    SessionSummaryCounts,
)


logger = logging.getLogger(__name__)


def _key(value: object) -> str:
    """Group-by key → string. Enum values land as Enum instances when
    `values_callable` is set on the SQLAlchemy column; raw strings come
    through as-is when the column is plain VARCHAR (e.g. cluster.status).
    """
    return value.value if hasattr(value, "value") else str(value)


@admin_router.get(
    "/dashboard/summary",
    response_model=APIResponse[DashboardSummary],
)
async def dashboard_summary(
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[DashboardSummary]:
    cluster_rows = (
        await session.execute(
            select(Cluster.status, func.count(Cluster.id))
            .group_by(Cluster.status)
        )
    ).all()
    pool_status_rows = (
        await session.execute(
            select(Pool.status, func.count(Pool.id))
            .group_by(Pool.status)
        )
    ).all()
    pool_type_rows = (
        await session.execute(
            select(Pool.pool_type, func.count(Pool.id))
            .group_by(Pool.pool_type)
        )
    ).all()
    desktop_rows = (
        await session.execute(
            select(Desktop.status, func.count(Desktop.id))
            .group_by(Desktop.status)
        )
    ).all()
    session_rows = (
        await session.execute(
            select(SessionModel.status, func.count(SessionModel.id))
            .group_by(SessionModel.status)
        )
    ).all()

    total_range = await session.scalar(
        select(
            func.coalesce(
                func.sum(Pool.vmid_range_end - Pool.vmid_range_start + 1),
                0,
            )
        )
    )

    cluster_by_status = {_key(s): int(c) for s, c in cluster_rows}
    pool_by_status = {_key(s): int(c) for s, c in pool_status_rows}
    pool_by_type = {_key(t): int(c) for t, c in pool_type_rows}
    desktop_by_status = {_key(s): int(c) for s, c in desktop_rows}
    session_by_status = {_key(s): int(c) for s, c in session_rows}

    total_desktops = sum(desktop_by_status.values())

    return APIResponse(
        data=DashboardSummary(
            clusters=ResourceStatusCounts(
                total=sum(cluster_by_status.values()),
                by_status=cluster_by_status,
            ),
            pools=PoolSummaryCounts(
                total=sum(pool_by_status.values()),
                by_status=pool_by_status,
                by_type=pool_by_type,
            ),
            desktops=ResourceStatusCounts(
                total=total_desktops,
                by_status=desktop_by_status,
            ),
            sessions=SessionSummaryCounts(
                total=sum(session_by_status.values()),
                active=session_by_status.get(SessionStatus.ACTIVE.value, 0),
                connecting=session_by_status.get(
                    SessionStatus.CONNECTING.value, 0,
                ),
                disconnected=session_by_status.get(
                    SessionStatus.DISCONNECTED.value, 0,
                ),
                ended=session_by_status.get(SessionStatus.ENDED.value, 0),
            ),
            capacity=CapacitySummary(
                total_vmid_slots=int(total_range or 0),
                total_desktops=total_desktops,
            ),
        )
    )


@admin_router.get(
    "/dashboard/capacity",
    response_model=APIResponse[list[PoolCapacityWithName]],
)
async def dashboard_capacity(
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[list[PoolCapacityWithName]]:
    """Per-pool capacity breakdown.

    Calls `_compute_capacity` once per pool — N+1 by design. The
    function is M2-15's shared truth for capacity math; restructuring
    into a single aggregate query here would either duplicate the
    logic or grow a premature abstraction. At M2 scale (pools in the
    dozens) the cost is negligible. If a customer install ever grows
    enough to feel it, the optimization belongs alongside the cache
    layer that whole-summary caching would also live in — not in a
    forked capacity-math implementation.
    """
    pools = (
        await session.execute(
            select(Pool).order_by(Pool.display_name)
        )
    ).scalars().all()

    rows: list[PoolCapacityWithName] = []
    for pool in pools:
        capacity = await _compute_capacity(session, pool)
        rows.append(
            PoolCapacityWithName(
                pool_id=pool.id,
                pool_name=pool.name,
                pool_display_name=pool.display_name,
                pool_status=pool.status,
                pool_type=pool.pool_type,
                **capacity.model_dump(),
            )
        )
    return APIResponse(data=rows)
