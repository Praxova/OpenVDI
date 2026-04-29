"""PoolProvisionerWorker — warm-spare maintenance for non-persistent pools.

Iterates non-persistent pools every 30 seconds. For each pool below
min_spare and below max_size, picks ONE pool (greedy: largest gap)
and provisions ONE desktop via provision_desktop. Subsequent ticks
fill remaining gaps.

Operates only on non-persistent pools per W8 — persistent pools
clone-on-connect (M2 broker logic) and never have warm spares.

Cadence: 30 seconds (W5). Cluster gating: skip clusters not in
'active' status (W6). Pool gating: skip pools not in 'active' status.

The existing M2 admin endpoint POST /pools/{id}/provision is the
explicit-operator-nudge path; this worker is the auto path. Both
exist in M4 and don't conflict — provision_desktop's M2-06 advisory
lock serializes them.

One provision per tick globally — concurrent provisioning across
pools is M5+. Reasoning: bounded tick duration (~2 min worst case),
bounded Proxmox load (clones are real KVM ops against storage), and
self-healing on lock loss (in-flight 'provisioning' rows count toward
total so dual-leadership doesn't double-provision).
"""
from __future__ import annotations

import logging
from typing import ClassVar
from uuid import UUID

from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models import (
    Cluster,
    ClusterStatus,
    Desktop,
    DesktopStatus,
    Pool,
    PoolStatus,
    PoolType,
    Template,
)
from app.providers.base import HypervisorProvider
from app.services.provisioner import (
    PoolInactive,
    provision_desktop,
)
from app.services.vmid_allocator import VMIDRangeExhausted
from app.workers.base import Worker

logger = logging.getLogger(__name__)


class PoolProvisionerWorker(Worker):
    name: ClassVar[str] = "pool_provisioner"
    interval_seconds: ClassVar[float] = 30.0

    async def tick(self, app: FastAPI) -> None:
        async with async_session_factory() as db:
            target = await self._pick_neediest_pool(db)

        if target is None:
            return  # nothing to do

        pool, template, cluster = target
        provider = self._provider_for(app, cluster)
        if provider is None:
            logger.warning(
                "no provider on this broker for cluster — skipping pool",
                extra={
                    "pool": pool.name,
                    "cluster_id": str(cluster.id),
                },
            )
            return

        logger.info(
            "provisioning warm spare",
            extra={
                "pool": pool.name,
                "pool_id": str(pool.id),
                "cluster": cluster.name,
            },
        )

        try:
            async with async_session_factory() as db:
                # Re-load pool + template under the new session so
                # provision_desktop can flush/commit on its own. The
                # selection-session objects are detached and would
                # error on lazy-load.
                pool_fresh = await db.get(Pool, pool.id)
                template_fresh = await db.get(Template, template.id)
                if pool_fresh is None or template_fresh is None:
                    return  # pool/template deleted between selection and now
                desktop = await provision_desktop(
                    session=db,
                    provider=provider,
                    pool=pool_fresh,
                    template=template_fresh,
                    assigned_user=None,  # warm spare, no pre-assignment
                )
        except PoolInactive:
            # Pool flipped to non-active between selection and start.
            # Next tick re-selects.
            logger.info(
                "pool became inactive mid-tick",
                extra={"pool": pool.name},
            )
            return
        except VMIDRangeExhausted:
            # Pool is full at the VMID-range level even though our
            # capacity check thought there was room. Possible when admin
            # shrinks the range while desktops exist. Log loudly so
            # admin notices.
            logger.warning(
                "VMID range exhausted; cannot provision spare",
                extra={"pool": pool.name},
            )
            return

        # provision_desktop returns a Desktop in 'available' or 'error'.
        if desktop.status == DesktopStatus.ERROR:
            logger.warning(
                "warm spare provisioning failed",
                extra={
                    "pool": pool.name,
                    "desktop_id": str(desktop.id),
                    "vmid": desktop.pve_vmid,
                    "error": desktop.error_message,
                },
            )
            # Don't retry in this tick. The errored row counts toward
            # neither available nor total (per the EXCLUDE filter), so
            # the next tick will try again with a fresh VMID. Admin can
            # rebuild via M2-15's POST /desktops/{id}/rebuild.
        else:
            logger.info(
                "warm spare ready",
                extra={
                    "pool": pool.name,
                    "desktop_id": str(desktop.id),
                    "vmid": desktop.pve_vmid,
                    "name": desktop.name,
                },
            )

    # ── Pool selection ────────────────────────────────────────

    async def _pick_neediest_pool(
        self, db: AsyncSession,
    ) -> tuple[Pool, Template, Cluster] | None:
        """Find the non-persistent pool with the biggest
        (min_spare - available_count) gap that's also below max_size.
        Returns (pool, template, cluster) or None.

        Two-step selection: query candidates, then per-pool capacity
        check in Python. The alternative (one big aggregate query
        with HAVING) is denser but harder to read and harder to evolve
        when M5+ adds priority weights. v0 scale (≤10 pools) makes the
        per-pool round-trip negligible.
        """
        stmt = (
            select(Pool, Template, Cluster)
            .join(Template, Pool.template_id == Template.id)
            .join(Cluster, Pool.cluster_id == Cluster.id)
            .where(Pool.pool_type == PoolType.NONPERSISTENT)
            .where(Pool.status == PoolStatus.ACTIVE)
            .where(Cluster.status == ClusterStatus.ACTIVE.value)
            .order_by(Pool.name)  # deterministic tie-break
        )
        candidates = (await db.execute(stmt)).all()
        if not candidates:
            return None

        best: tuple[Pool, Template, Cluster] | None = None
        best_gap = 0
        for pool, template, cluster in candidates:
            available, total = await self._pool_capacity(db, pool.id)
            gap = pool.min_spare - available
            room = pool.max_size - total
            if gap <= 0 or room <= 0:
                continue
            # actionable_gap respects both min_spare and max_size.
            actionable_gap = min(gap, room)
            if actionable_gap > best_gap:
                best_gap = actionable_gap
                best = (pool, template, cluster)
        return best

    async def _pool_capacity(
        self, db: AsyncSession, pool_id: UUID,
    ) -> tuple[int, int]:
        """Returns (available_count, total_count) for the pool.

        - available_count: desktops with status='available'.
        - total_count: desktops with status NOT IN ('deleting', 'error').
          Note: 'provisioning' is INCLUDED in total — in-flight desktops
          count against max_size so we don't double-provision. 'error'
          rows are excluded so they don't permanently block provisioning
          (the next tick can replace them with fresh VMIDs).
        """
        available_stmt = (
            select(func.count())
            .select_from(Desktop)
            .where(Desktop.pool_id == pool_id)
            .where(Desktop.status == DesktopStatus.AVAILABLE)
        )
        total_stmt = (
            select(func.count())
            .select_from(Desktop)
            .where(Desktop.pool_id == pool_id)
            .where(Desktop.status.notin_([
                DesktopStatus.DELETING,
                DesktopStatus.ERROR,
            ]))
        )
        available = (await db.execute(available_stmt)).scalar() or 0
        total = (await db.execute(total_stmt)).scalar() or 0
        return int(available), int(total)

    @staticmethod
    def _provider_for(
        app: FastAPI, cluster: Cluster,
    ) -> HypervisorProvider | None:
        providers = getattr(app.state, "providers", None) or {}
        return providers.get(cluster.id)
