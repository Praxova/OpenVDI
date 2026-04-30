"""HealthCheckerWorker — cluster health + cross-broker config sync.

Every 60 seconds, iterates non-maintenance clusters:
  1. Sync providers across brokers (W13 + extensions): construct new
     clusters, reconstruct updated ones, dispose deleted ones.
  2. Ping each cluster; flip status active ↔ offline.
  3. Gather node + storage health (log-only in v0).
  4. Reconcile VM inventory against DB (log-only in v0).
  5. Detect stuck-provisioning desktops (>10 min in PROVISIONING) →
     flip to ERROR.

Per W6, this worker is the only one that writes to cluster.status
(post-startup; M2's boot-time ping is the one-shot at t=0, kept as a
parallel mechanism per W9).

V0 scope: log-only for storage capacity + inventory orphans. M5+
candidates: admin alerts UI, auto-recovery for orphan VMs, LISTEN/
NOTIFY for instant cross-broker sync, env-tunable storage threshold.

Multi-broker safe: leader-elected per W11. The non-leader brokers
still construct providers locally (each broker needs its own provider
instance for read paths), so the sync logic runs on every broker —
not just the leader. The leader-elected status-flip writes are the
only DB-write-from-tick path; provider sync is local state only.

Wait — actually that's wrong. The full tick (status writes + stuck-
provisioning writes) only runs on the leader. Provider sync is a
side effect of the tick that runs alongside the writes. Followers
that need to sync providers separately would require a different
trigger (e.g. Postgres LISTEN/NOTIFY). For v0 the polling-on-leader
+ ~60s window is acceptable. Followers picking up leadership later
will sync on their first tick.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import ClassVar

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models import (
    Cluster,
    ClusterStatus,
    Desktop,
    DesktopStatus,
    Pool,
)
from app.providers.base import HypervisorProvider
from app.providers.exceptions import ProviderError
from app.services.cluster_service import (
    construct_provider,
    ping_and_update_status,
)
from app.workers.base import Worker

logger = logging.getLogger(__name__)


# Module constants.
STUCK_PROVISIONING_MINUTES = 10
STORAGE_LOW_PERCENT_THRESHOLD = 20.0


class HealthCheckerWorker(Worker):
    name: ClassVar[str] = "health_checker"
    interval_seconds: ClassVar[float] = 60.0

    async def tick(self, app: FastAPI) -> None:
        # 1. Sync providers across brokers (W13 + extensions).
        async with async_session_factory() as db:
            clusters = await self._load_clusters(db)
        await self._sync_providers(app, clusters)

        # 2-4. Per-cluster health gathering. Per-cluster failures
        # don't abort the tick.
        for cluster in clusters:
            try:
                await self._process_cluster(app, cluster)
            except Exception:
                logger.exception(
                    "health_checker failed for cluster",
                    extra={
                        "cluster_id": str(cluster.id),
                        "cluster": cluster.name,
                    },
                )

        # 5. DB-only stuck-provisioning detection.
        try:
            await self._check_stuck_provisioning()
        except Exception:
            logger.exception("stuck-provisioning check failed")

    # ── Step 1: Provider sync (W13 extended) ──────────────────

    async def _load_clusters(self, db: AsyncSession) -> list[Cluster]:
        """All clusters except 'maintenance' — admin-disabled clusters
        don't get pinged. PENDING (just-registered, awaiting first
        ping) and OFFLINE (last ping failed) are pinged so they can
        flip to ACTIVE on success.
        """
        stmt = select(Cluster).where(
            Cluster.status.in_([
                ClusterStatus.PENDING.value,
                ClusterStatus.ACTIVE.value,
                ClusterStatus.OFFLINE.value,
            ])
        )
        return list((await db.execute(stmt)).scalars().all())

    async def _sync_providers(
        self, app: FastAPI, clusters: list[Cluster],
    ) -> None:
        """Reconcile app.state.providers against the DB cluster list.

        Three cases:
          - Not in app.state.providers → construct fresh.
          - In app.state.providers + cluster.updated_at newer than the
            broker's construction time → reconstruct (W13 update path).
          - In app.state.providers + same-or-older update time → no-op.

        Plus: any cluster_id in app.state.providers whose row is no
        longer in the DB (deleted post-startup) → close + remove.
        """
        # Defensive — lifespan populates this; a hot-reloaded module
        # may have skipped that.
        if not hasattr(app.state, "provider_constructed_at"):
            app.state.provider_constructed_at = {}

        cluster_ids_in_db = {c.id for c in clusters}

        # Drop providers for clusters no longer in the DB.
        for stale_id in list(app.state.providers.keys()):
            if stale_id in cluster_ids_in_db:
                continue
            old = app.state.providers.pop(stale_id, None)
            app.state.provider_constructed_at.pop(stale_id, None)
            if old is None:
                continue
            try:
                await old.close()
            except Exception:
                logger.warning(
                    "error closing provider for deleted cluster",
                    extra={"cluster_id": str(stale_id)},
                )
            logger.info(
                "provider removed (cluster deleted)",
                extra={"cluster_id": str(stale_id)},
            )

        # Construct or reconstruct as needed.
        for cluster in clusters:
            constructed_at = app.state.provider_constructed_at.get(
                cluster.id,
            )
            # cluster.updated_at is timezone-aware (timestamptz).
            # constructed_at is also tz-aware (we set it via
            # datetime.now(timezone.utc)).
            needs_construct = (
                constructed_at is None
                or cluster.updated_at > constructed_at
            )
            if not needs_construct:
                continue

            old = app.state.providers.pop(cluster.id, None)
            if old is not None:
                try:
                    await old.close()
                except Exception:
                    logger.warning(
                        "error closing old provider during reconstruction",
                        extra={
                            "cluster_id": str(cluster.id),
                            "cluster": cluster.name,
                        },
                    )

            try:
                new_provider = await construct_provider(cluster)
            except Exception as exc:
                # construct_provider can raise on unknown
                # provider_type, Fernet decrypt failure, or
                # constructor validation. Log + keep going; the next
                # tick retries.
                logger.warning(
                    "failed to construct provider during sync",
                    extra={
                        "cluster_id": str(cluster.id),
                        "cluster": cluster.name,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
                continue

            app.state.providers[cluster.id] = new_provider
            app.state.provider_constructed_at[cluster.id] = (
                datetime.now(timezone.utc)
            )
            logger.info(
                "provider constructed (sync)",
                extra={
                    "cluster_id": str(cluster.id),
                    "cluster": cluster.name,
                    "reason": "new" if old is None else "updated",
                },
            )

    # ── Step 2-4: Per-cluster processing ──────────────────────

    async def _process_cluster(
        self, app: FastAPI, cluster: Cluster,
    ) -> None:
        """Ping + status flip + node/storage/orphan checks for one
        cluster."""
        provider = app.state.providers.get(cluster.id)
        if provider is None:
            # Construction failed in step 1; skip and let next tick retry.
            return

        # Ping (the M2 helper is the canonical writer of cluster.status
        # per W6). It returns the new status enum and writes the row.
        new_status = await ping_and_update_status(cluster.id, provider)
        if new_status != ClusterStatus.ACTIVE:
            # Cluster offline / pending — skip data-gathering steps;
            # they'd just generate noise.
            return

        # Node status (log-only).
        try:
            await self._check_nodes(cluster, provider)
        except ProviderError as exc:
            logger.warning(
                "list_nodes failed",
                extra={"cluster": cluster.name, "error": str(exc)},
            )

        # Storage capacity (log-only).
        try:
            await self._check_storage(cluster, provider)
        except ProviderError as exc:
            logger.warning(
                "list_storage failed",
                extra={"cluster": cluster.name, "error": str(exc)},
            )

        # Orphan reconciliation (log-only).
        try:
            await self._reconcile_inventory(cluster, provider)
        except ProviderError as exc:
            logger.warning(
                "list_vms failed",
                extra={"cluster": cluster.name, "error": str(exc)},
            )

    async def _check_nodes(
        self, cluster: Cluster, provider: HypervisorProvider,
    ) -> None:
        """Iterate cluster nodes; log non-online ones. v0: log only."""
        nodes = await provider.list_nodes()
        for node in nodes:
            if node.status != "online":
                logger.warning(
                    "node not online",
                    extra={
                        "cluster": cluster.name,
                        "node": node.node,
                        "status": node.status,
                    },
                )

    async def _check_storage(
        self, cluster: Cluster, provider: HypervisorProvider,
    ) -> None:
        """Iterate per-node storage; log low-capacity ones. v0: log only."""
        nodes = await provider.list_nodes()
        for node in nodes:
            if node.status != "online":
                continue
            try:
                storages = await provider.list_storage(node.node)
            except ProviderError as exc:
                logger.warning(
                    "list_storage(node) failed",
                    extra={
                        "cluster": cluster.name,
                        "node": node.node,
                        "error": str(exc),
                    },
                )
                continue
            for s in storages:
                if s.total_bytes <= 0:
                    continue
                free_bytes = s.total_bytes - s.used_bytes
                free_pct = (free_bytes / s.total_bytes) * 100
                if free_pct < STORAGE_LOW_PERCENT_THRESHOLD:
                    logger.warning(
                        "storage below capacity threshold",
                        extra={
                            "cluster": cluster.name,
                            "node": node.node,
                            "storage": s.name,
                            "free_pct": round(free_pct, 1),
                            "free_gb": round(free_bytes / (1024**3), 1),
                            "total_gb": round(s.total_bytes / (1024**3), 1),
                        },
                    )

    async def _reconcile_inventory(
        self, cluster: Cluster, provider: HypervisorProvider,
    ) -> None:
        """Cross-check provider VM list against DB desktops. v0: log only.

        Two directions:
          - DB rows whose VMs don't exist in provider (admin destroyed
            via qm directly, or provider state out of sync).
          - Provider VMs tagged 'openvdi-managed' that have no DB row
            (broker DB lost; manually-created VMs that copied the tag).

        VM identity in v0 is (node, vmid). The Proxmox provider
        packages this into VMRef.data as a dict.
        """
        all_vms = await provider.list_vms()

        # Build provider-side identity set. VMRef.data is a dict for
        # the Proxmox provider per app/providers/proxmox/types.py.
        provider_vmids: set[tuple[str, int]] = set()
        for vm in all_vms:
            data = vm.ref.data
            if isinstance(data, dict) and "node" in data and "vmid" in data:
                provider_vmids.add((data["node"], int(data["vmid"])))

        # DB-side: desktops in this cluster, excluding in-flight states
        # (PROVISIONING / DELETING) — those are mid-cycle and would
        # generate false positives.
        async with async_session_factory() as db:
            stmt = (
                select(Desktop)
                .join(Pool, Desktop.pool_id == Pool.id)
                .where(Pool.cluster_id == cluster.id)
                .where(Desktop.status.notin_([
                    DesktopStatus.DELETING,
                    DesktopStatus.PROVISIONING,
                ]))
            )
            db_desktops = list((await db.execute(stmt)).scalars().all())

        db_vmids = {(d.pve_node, d.pve_vmid) for d in db_desktops}

        # Direction 1: DB → Provider.
        for d in db_desktops:
            if (d.pve_node, d.pve_vmid) not in provider_vmids:
                logger.warning(
                    "DB desktop has no matching VM in provider",
                    extra={
                        "cluster": cluster.name,
                        "desktop_id": str(d.id),
                        "vmid": d.pve_vmid,
                        "node": d.pve_node,
                    },
                )

        # Direction 2: Provider → DB. Filter to openvdi-managed tag —
        # we only care about VMs the broker would have created.
        for vm in all_vms:
            if "openvdi-managed" not in vm.tags:
                continue
            data = vm.ref.data
            if not (
                isinstance(data, dict)
                and "node" in data
                and "vmid" in data
            ):
                continue
            ident = (data["node"], int(data["vmid"]))
            if ident not in db_vmids:
                # Avoid `extra={"name": ...}` — `name` is a reserved
                # attribute on LogRecord (the logger name); using it
                # raises "Attempt to overwrite 'name'" at log time.
                logger.warning(
                    "openvdi-tagged VM in provider has no DB row",
                    extra={
                        "cluster": cluster.name,
                        "vmid": ident[1],
                        "node": ident[0],
                        "vm_name": vm.name,
                    },
                )

    # ── Step 5: Stuck-provisioning detection ──────────────────

    async def _check_stuck_provisioning(self) -> None:
        """Find desktops in PROVISIONING for >STUCK_PROVISIONING_MINUTES
        → flip to ERROR.

        These accumulate when a worker dies mid-provision (broker
        crash, leadership handoff, forced kill). The DB row is the
        source of truth; this safety net forces admin attention on
        the orphan.

        Idempotent — once flipped to ERROR, the row no longer matches
        the predicate. Recovery is admin-driven via M2-15's
        POST /desktops/{id}/rebuild.
        """
        threshold = datetime.now(timezone.utc) - timedelta(
            minutes=STUCK_PROVISIONING_MINUTES,
        )
        async with async_session_factory() as db:
            stmt = select(Desktop).where(
                Desktop.status == DesktopStatus.PROVISIONING,
                Desktop.updated_at < threshold,
            )
            stuck = list((await db.execute(stmt)).scalars().all())
            if not stuck:
                return
            for desktop in stuck:
                desktop.status = DesktopStatus.ERROR
                desktop.error_message = (
                    f"stuck in 'provisioning' for >"
                    f"{STUCK_PROVISIONING_MINUTES} minutes since "
                    f"{desktop.updated_at}; possible broker crash "
                    f"during provisioning. Use POST /desktops/{{id}}"
                    f"/rebuild to recover."
                )
                # task_tracker (M4-10) won't poll a row in 'error'
                # state, but clearing UPID/kind prevents a future
                # status-change from re-arming a stale task reference.
                desktop.pve_task_upid = None
                desktop.pve_task_kind = None
                logger.warning(
                    "desktop stuck in provisioning — flipped to error",
                    extra={
                        "desktop_id": str(desktop.id),
                        "vmid": desktop.pve_vmid,
                        "stuck_since": str(desktop.updated_at),
                    },
                )
            await db.commit()
