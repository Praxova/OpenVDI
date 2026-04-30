"""TaskTrackerWorker — observe and complete in-flight provider tasks.

Polls every 5 seconds for desktops with `pve_task_upid IS NOT NULL`.
For each, calls `provider.get_task_status(handle)` (non-blocking) to
check current state. Tasks still running stay in flight; tasks
completed get dispatched to `_apply_task_success` (DESTROY removes
the row, REBUILD chains into provision_desktop, power transitions
update power_state) or `_mark_task_error` (sets desktop.status=error
+ error_message).

Replaces M2's BackgroundTasks-driven `poll_desktop_task` per W7. The
desktop row is the source of truth: any row with pve_task_upid set
is in-flight, regardless of which broker (or no broker) was previously
polling it. Survives broker restart by design — the worker's first
tick discovers everything.

Cadence: 5 seconds (W5). Cluster gating: skip clusters not in
'active' status (W6) — the broker may have lost the provider on
config change; we'll resume next tick when it's back.
"""
from __future__ import annotations

import logging
from typing import ClassVar

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models import Cluster, ClusterStatus, Desktop, Pool
from app.providers.base import HypervisorProvider, TaskHandle
from app.providers.exceptions import ProviderError
from app.services.task_tracker import (
    DesktopTaskKind,
    _apply_task_success,
    _mark_task_error,
)
from app.workers.base import Worker

logger = logging.getLogger(__name__)


class TaskTrackerWorker(Worker):
    name: ClassVar[str] = "task_tracker"
    interval_seconds: ClassVar[float] = 5.0

    async def tick(self, app: FastAPI) -> None:
        async with async_session_factory() as db:
            in_flight = await self._fetch_in_flight(db)

        for desktop, cluster in in_flight:
            try:
                await self._poll_one(app, desktop, cluster)
            except Exception:
                logger.exception(
                    "task_tracker failed for desktop",
                    extra={
                        "desktop_id": str(desktop.id),
                        "vmid": desktop.pve_vmid,
                        "kind": desktop.pve_task_kind,
                    },
                )

    async def _fetch_in_flight(
        self, db: AsyncSession,
    ) -> list[tuple[Desktop, Cluster]]:
        """Find every desktop with a UPID set, joined to its cluster
        for provider lookup. Skip clusters not in 'active' status (W6)
        — their providers may be unavailable; we'll resume next tick
        when the cluster is back.
        """
        stmt = (
            select(Desktop, Cluster)
            .join(Pool, Desktop.pool_id == Pool.id)
            .join(Cluster, Pool.cluster_id == Cluster.id)
            .where(Desktop.pve_task_upid.isnot(None))
            .where(Cluster.status == ClusterStatus.ACTIVE.value)
        )
        result = await db.execute(stmt)
        return list(result.all())

    async def _poll_one(
        self,
        app: FastAPI,
        desktop: Desktop,
        cluster: Cluster,
    ) -> None:
        """Check one desktop's task status; dispatch to success/error
        handler if completed."""
        provider = self._provider_for(app, cluster)
        if provider is None:
            logger.debug(
                "no provider for cluster on this broker",
                extra={
                    "desktop_id": str(desktop.id),
                    "cluster_id": str(cluster.id),
                },
            )
            return

        # Validate kind before doing any provider work — defends against
        # rows with corrupted pve_task_kind. Same defensive posture as
        # M2's resume_inflight_tasks had.
        try:
            kind = DesktopTaskKind(desktop.pve_task_kind)
        except (ValueError, TypeError):
            logger.warning(
                "desktop has UPID but invalid kind — marking error",
                extra={
                    "desktop_id": str(desktop.id),
                    "kind": desktop.pve_task_kind,
                },
            )
            await _mark_task_error(
                desktop.id,
                # PROVISION as the label only — _mark_task_error doesn't
                # branch on kind itself, just uses it in the message.
                DesktopTaskKind.PROVISION,
                f"unknown task kind {desktop.pve_task_kind!r}",
            )
            return

        handle = TaskHandle(
            provider_type=provider.provider_type,
            data={"node": desktop.pve_node, "upid": desktop.pve_task_upid},
        )

        try:
            status = await provider.get_task_status(handle)
        except ProviderError as exc:
            # Any provider failure here means we couldn't determine
            # status; treat as a task failure. More aggressive than M2's
            # wait_for_task path (which distinguished timeout from task
            # error), but operationally equivalent — admin sees the
            # desktop in error state and can rebuild.
            logger.warning(
                "task status check failed",
                extra={
                    "desktop_id": str(desktop.id),
                    "vmid": desktop.pve_vmid,
                    "kind": kind.value,
                    "error": str(exc),
                },
            )
            await _mark_task_error(
                desktop.id, kind, f"{type(exc).__name__}: {exc}",
            )
            return

        if status.state == "running":
            return  # still in flight; check again next tick

        # Task is stopped. status.success is True/False per the M2 contract.
        if status.success:
            logger.info(
                "task completed",
                extra={
                    "desktop_id": str(desktop.id),
                    "vmid": desktop.pve_vmid,
                    "kind": kind.value,
                },
            )
            # _apply_task_success branches on kind. For REBUILD it
            # chains into provision_desktop (long; ties up the worker
            # tick — same long-tick analysis as M4-09's pool_provisioner).
            await _apply_task_success(
                desktop.id, kind, status, lambda: provider,
            )
        else:
            logger.info(
                "task failed",
                extra={
                    "desktop_id": str(desktop.id),
                    "vmid": desktop.pve_vmid,
                    "kind": kind.value,
                    "error": status.error_message,
                },
            )
            await _mark_task_error(
                desktop.id,
                kind,
                status.error_message
                or "task reported failure with no message",
            )

    @staticmethod
    def _provider_for(
        app: FastAPI, cluster: Cluster,
    ) -> HypervisorProvider | None:
        providers = getattr(app.state, "providers", None) or {}
        return providers.get(cluster.id)
