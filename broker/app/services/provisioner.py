"""Desktop provisioning service.

`provision_desktop` drives the full
    clone → configure (CPU/mem overrides + tags + description)
          → start → wait for guest agent
          → (non-persistent: shutdown → create openvdi-base snapshot
                           → start → wait for agent again)
          → available
flow for a single desktop. Returns a Desktop ORM row in either
`available`/`assigned` (success) or `error` state. The function never
raises on provider failure: the row carries the outcome.

Caller expectations:
- Pass an already-opened AsyncSession. The function commits at phase
  boundaries (row reservation, task-UPID persistence, final state)
  but never wraps the whole flow in one transaction — Proxmox task
  waits can take minutes and holding a Postgres transaction that long
  is abusive.
- Pass a live HypervisorProvider instance. The function never touches
  the Proxmox API directly.
- Pass the Pool and Template ORM rows already loaded into the session.
- On failure, the VM (if partially provisioned) is left in Proxmox for
  operator investigation (S-B3). No auto-retry, no auto-destroy.

BackgroundTasks glue pattern (implemented in the API layer, M2-15):

    async def _provision_shim(pool_id: UUID, count: int):
        async with async_session_factory() as session:
            pool = await session.get(Pool, pool_id)
            template = await session.get(Template, pool.template_id)
            provider = get_provider_for_cluster(pool.cluster_id)
            for _ in range(count):
                await provision_desktop(
                    session=session, provider=provider,
                    pool=pool, template=template,
                )

The shim owns the session. The provisioner owns the phases. Concurrency
across desktops is the caller's decision via asyncio.gather at the
shim layer.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
import time
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Desktop, DesktopStatus, Pool, PoolType, Template
from app.providers.base import (
    CloneRequest,
    HypervisorProvider,
    VMConfig,
    VMRef,
)
from app.providers.exceptions import ProviderError
from app.services.vmid_allocator import allocate_vmid

logger = logging.getLogger(__name__)

# Guest-agent liveness poll budget (Step 6 + Step 7-alt restart).
_AGENT_POLL_TIMEOUT_SECONDS = 90
_AGENT_POLL_INTERVAL_SECONDS = 2.0

# Seconds to wait after the guest agent first responds before taking the
# openvdi-base snapshot for non-persistent desktops. The agent on Windows
# responds early in the service chain -- well before the OS has reached
# steady state. Snapshotting at that point produces a half-initialized
# baseline that rollback-on-logoff can never recover.
#
# 60s is a conservative default that covers the observed Windows 11 boot
# window. A properly-ordered template (see session-tracking.md -> Template
# Requirements item 6) can tolerate a lower value; tune downward with
# evidence, not by hunch. Raising past 120s probably means the template
# itself has a problem and the value is papering over it.
#
# Applies to non-persistent provisioning ONLY. Persistent desktops are
# handed directly to a user after agent-up -- no snapshot, no quiesce.
_POST_BOOT_QUIESCE_SECONDS = 60


# ── Refresh-on-logoff cycle timeouts (M4-06) ──────────────────
# Graceful shutdown can take 2 minutes on Windows (services stopping,
# pagefile flush). Force-fallback after the timeout escalates to hard
# stop in the provider layer.
_REFRESH_GRACEFUL_SHUTDOWN_TIMEOUT = 120

# wait_for_task budgets per provider operation. Tuned conservatively;
# tighten if observed latencies stay low across a wider deployment.
_VM_STOP_TASK_TIMEOUT = 60       # Proxmox `stop` task is fast (process kill).
_VM_ROLLBACK_TASK_TIMEOUT = 300  # Snapshot rollback can be slow on LVM-thin.
_VM_START_TASK_TIMEOUT = 60      # Boot proper, not OS-up — that's _wait_for_agent.
_VM_DESTROY_TASK_TIMEOUT = 300   # Disk delete can be slow.

# Power-state polling after a stop/shutdown task completes. The Proxmox
# task can finish before power_state actually transitions; brief poll.
_VM_STOPPED_POLL_TIMEOUT = 30
_VM_STOPPED_POLL_INTERVAL = 1.0


class PoolInactive(Exception):
    """Pool is not in 'active' status; provisioning is refused."""


class DesktopNotFound(Exception):
    """The desktop_id passed to refresh_desktop / delete_desktop_on_logoff
    has no row. Callers (session_monitor in M4-08) should treat as a
    benign race — the desktop was destroyed between the dispatch and
    this call.
    """


class InvalidDesktopState(Exception):
    """Pool flags or desktop status disagree with the requested
    operation. Caller should NOT retry — the disagreement is structural
    (wrong pool type, conflicting flags, in-flight cycle elsewhere).
    """


# Tag slug regex: anything not in [a-z0-9_-] collapses to a dash.
_SLUG_RE = re.compile(r"[^a-z0-9_-]+")


def _slugify(value: str) -> str:
    """Lowercase, replace non [a-z0-9_-] with '-', collapse runs, strip edges.

    Pool names are pre-validated to match [a-z0-9_-] (see
    schemas/pool.py), so this is a no-op for pool.name. Usernames from
    AD are not constrained — slugification is lossy for dotted or
    non-ASCII logins, and the unmodified string is preserved in the VM
    description field for DR.
    """
    s = _SLUG_RE.sub("-", value.lower())
    s = re.sub(r"-+", "-", s).strip("-")
    return s


async def _wait_for_agent(
    provider: HypervisorProvider, ref: VMRef,
) -> None:
    """Poll agent_ping until it responds or the timeout elapses.

    Raises asyncio.TimeoutError on failure (caught by provision_desktop's
    outer handler → desktop marked error).
    """
    deadline = time.monotonic() + _AGENT_POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if await provider.agent_ping(ref):
            return
        await asyncio.sleep(_AGENT_POLL_INTERVAL_SECONDS)
    raise asyncio.TimeoutError(
        f"guest agent did not respond within {_AGENT_POLL_TIMEOUT_SECONDS}s"
    )


async def _wait_for_stopped(
    provider: HypervisorProvider, ref: VMRef,
) -> None:
    """Poll get_vm_status until power_state == 'stopped' or timeout.

    Used after `shutdown_vm` / `stop_vm` because Proxmox occasionally
    completes the task before the VM has fully transitioned (the KVM
    process is still releasing the disk lock for a fraction of a
    second). Subsequent operations like rollback or destroy reject
    while the lock is held.

    Raises asyncio.TimeoutError on failure (caught by the outer handler
    → desktop marked error per R3).
    """
    deadline = time.monotonic() + _VM_STOPPED_POLL_TIMEOUT
    while time.monotonic() < deadline:
        status = await provider.get_vm_status(ref)
        if status.power_state == "stopped":
            return
        await asyncio.sleep(_VM_STOPPED_POLL_INTERVAL)
    raise asyncio.TimeoutError(
        f"VM did not reach power_state=stopped within "
        f"{_VM_STOPPED_POLL_TIMEOUT}s"
    )


async def provision_desktop(
    *,
    session: AsyncSession,
    provider: HypervisorProvider,
    pool: Pool,
    template: Template,
    assigned_user: str | None = None,
    existing_desktop: Desktop | None = None,
) -> Desktop:
    """Provision one desktop for the given pool.

    See module docstring for session/commit semantics.
    `assigned_user` is set on the Desktop row when the broker has
    pre-assigned (persistent-pool new-user case); for warm-spare
    provisioning the argument stays None.

    `existing_desktop` is the rebuild path (M2-13): when the caller has
    an existing Desktop row whose VM was just destroyed and wants to
    reprovision into the same VMID / node / name, they pass the row
    here. Step 1 skips VMID allocation and row creation; the row's
    provisioning-state fields (status, error_message, power_state,
    provisioned_at, pve_task_upid, pve_task_kind) are reset, and
    assigned_user / assignment_type are set from the passed argument
    and pool type — same rules as the new-row path.

    Returns:
        Desktop in `available` / `assigned` (success) or `error` state.

    Raises:
        PoolInactive: pool.status is not 'active'.
        VMIDRangeExhausted: pool range is full (caller should pre-check).
        IntegrityError: unique-constraint violation (probable bug).
    """
    # Step 0: pre-flight
    if pool.status != "active":
        raise PoolInactive(
            f"pool {pool.name!r} status is {pool.status!r}, not 'active'"
        )

    is_persistent_pool = pool.pool_type == PoolType.PERSISTENT

    # Step 1: allocate VMID + reserve Desktop row (flush, do not commit).
    # The rebuild path passes existing_desktop to skip allocation and
    # reuse the prior VMID; both branches end with `desktop`, `vmid`,
    # `name`, `target_node` set for Step 2 onward.
    if existing_desktop is None:
        vmid = await allocate_vmid(session, pool)
        name = f"{pool.name_prefix}-{vmid - pool.vmid_range_start + 1:03d}"
        target_node = (
            pool.target_nodes.split(",")[0].strip()
            if pool.target_nodes
            else template.pve_node
        )
        desktop = Desktop(
            pool_id=pool.id,
            pve_vmid=vmid,
            pve_node=target_node,
            name=name,
            status=DesktopStatus.PROVISIONING,
            assigned_user=assigned_user,
            assignment_type=(
                "persistent" if (assigned_user and is_persistent_pool) else None
            ),
        )
        session.add(desktop)
        await session.flush()
        logger.info(
            "provisioner: reserved vmid=%d name=%s pool=%s",
            vmid, name, pool.name,
        )
    else:
        desktop = existing_desktop
        vmid = desktop.pve_vmid
        name = desktop.name
        target_node = desktop.pve_node
        desktop.status = DesktopStatus.PROVISIONING
        desktop.error_message = None
        desktop.power_state = "stopped"
        desktop.provisioned_at = None
        desktop.pve_task_upid = None
        desktop.pve_task_kind = None
        desktop.assigned_user = assigned_user
        desktop.assignment_type = (
            "persistent" if (assigned_user and is_persistent_pool) else None
        )
        await session.flush()
        logger.info(
            "provisioner: reusing vmid=%d name=%s pool=%s (rebuild path)",
            vmid, name, pool.name,
        )

    try:
        # Step 2: clone — persist UPID + commit before the long wait so
        # a crash leaves enough state for recovery (W-1).
        # Note: VMRef's data shape is Proxmox-specific (dict with "node"
        # and "vmid"). Acceptable v0 coupling; a future multi-provider
        # split may introduce a shared accessor in providers.base.
        src_ref = VMRef(
            provider_type="proxmox",
            data={"node": template.pve_node, "vmid": template.pve_vmid},
        )
        clone_req = CloneRequest(
            source_ref=src_ref,
            new_name=name,
            target_node=target_node,
            target_storage=pool.target_storage,
            target_pool=pool.pve_pool_id,
            description=(
                f"OpenVDI: pool={pool.name} assigned={assigned_user or 'none'}"
            ),
            provider_opts={"newid": vmid},
        )
        clone_handle = await provider.clone_vm(clone_req)
        # TaskHandle.data is a dict with "upid" for Proxmox (same
        # shape-coupling caveat as above).
        desktop.pve_task_upid = clone_handle.data["upid"]
        await session.commit()
        logger.info(
            "provisioner: clone dispatched vmid=%d upid=%s",
            vmid, desktop.pve_task_upid,
        )

        # Step 3: wait for clone
        await provider.wait_for_task(clone_handle, timeout_seconds=600)
        logger.info("provisioner: clone complete vmid=%d", vmid)

        # Step 4: configure — CPU/mem overrides + tags + description
        ref = VMRef(
            provider_type="proxmox",
            data={"node": target_node, "vmid": vmid},
        )
        config_changes = VMConfig()
        cpu_override = (
            pool.cpu_cores is not None
            and pool.cpu_cores != template.cpu_cores
        )
        mem_override = (
            pool.memory_mb is not None
            and pool.memory_mb != template.memory_mb
        )
        if cpu_override:
            config_changes = dataclasses.replace(
                config_changes, cpu_cores=pool.cpu_cores,
            )
        if mem_override:
            config_changes = dataclasses.replace(
                config_changes, memory_mb=pool.memory_mb,
            )

        # Tag vocabulary per docs/database-schema.md → VM Tagging
        # Convention. Kebab form only — Proxmox tag format is [a-z0-9_-].
        tag_set = {
            "openvdi-managed",
            f"openvdi-pool-{_slugify(pool.name)}",
            f"openvdi-type-{pool.pool_type.value}",
        }
        if assigned_user:
            tag_set.add(f"openvdi-user-{_slugify(assigned_user)}")

        # Human-readable DR fallback carries the unmodified username.
        description = (
            f"OpenVDI: pool={pool.name} type={pool.pool_type.value}"
            + (f" assigned={assigned_user}" if assigned_user else "")
        )

        config_changes = dataclasses.replace(
            config_changes,
            tags=frozenset(tag_set),
            description=description,
        )
        logger.info(
            "provisioner: configure vmid=%d cpu_override=%s mem_override=%s tags=%d",
            vmid, cpu_override, mem_override, len(tag_set),
        )
        await provider.wait_for_task(
            await provider.configure_vm(ref, config_changes),
            timeout_seconds=60,
        )

        # Step 5: start
        await provider.wait_for_task(
            await provider.start_vm(ref), timeout_seconds=60,
        )
        desktop.power_state = "running"
        logger.info("provisioner: VM started vmid=%d", vmid)

        # Step 6: wait for guest agent
        await _wait_for_agent(provider, ref)
        logger.info("provisioner: agent up vmid=%d", vmid)

        # Step 7 split by pool type
        if is_persistent_pool:
            final_status = (
                DesktopStatus.ASSIGNED
                if assigned_user
                else DesktopStatus.AVAILABLE
            )
        else:
            # Step 7-alt: quiesce → graceful shutdown → openvdi-base
            # snapshot → start again → wait agent.
            #
            # The quiesce exists because agent_ping=True fires long
            # before Windows is at steady state; shutting down mid-init
            # and snapshotting the result produces an unrollback-able
            # baseline. See m2-07a docs/prompt for the rationale.
            logger.info(
                "vmid=%d: post-boot quiesce (%ds) before openvdi-base snapshot",
                desktop.pve_vmid, _POST_BOOT_QUIESCE_SECONDS,
            )
            await asyncio.sleep(_POST_BOOT_QUIESCE_SECONDS)
            logger.info(
                "vmid=%d: quiesce complete, proceeding to shutdown",
                desktop.pve_vmid,
            )

            logger.info("provisioner: shutdown for snapshot vmid=%d", vmid)
            await provider.wait_for_task(
                await provider.shutdown_vm(
                    ref, timeout_seconds=120, force=True,
                ),
                timeout_seconds=180,
            )
            desktop.power_state = "stopped"

            await provider.wait_for_task(
                await provider.create_snapshot(
                    ref, "openvdi-base",
                    description="OpenVDI clean baseline",
                ),
                timeout_seconds=120,
            )
            logger.info(
                "provisioner: openvdi-base snapshot created vmid=%d", vmid,
            )

            await provider.wait_for_task(
                await provider.start_vm(ref), timeout_seconds=60,
            )
            desktop.power_state = "running"

            await _wait_for_agent(provider, ref)
            logger.info(
                "provisioner: agent up post-snapshot vmid=%d", vmid,
            )

            final_status = DesktopStatus.AVAILABLE

        desktop.status = final_status
        desktop.provisioned_at = datetime.now(timezone.utc)
        desktop.pve_task_upid = None
        await session.commit()
        logger.info(
            "provisioner: vmid=%d -> %s",
            vmid, final_status.value,
        )
        return desktop

    except (ProviderError, asyncio.TimeoutError, PoolInactive) as e:
        desktop.status = DesktopStatus.ERROR
        desktop.error_message = f"{type(e).__name__}: {e}"
        desktop.pve_task_upid = None
        await session.commit()
        logger.error(
            "provisioner: vmid=%d FAILED: %s: %s",
            vmid, type(e).__name__, e,
        )
        return desktop


# ── Refresh-on-logoff cycle (M4-06) ──────────────────────────────


async def refresh_desktop(
    *,
    session: AsyncSession,
    provider: HypervisorProvider,
    desktop_id,
) -> Desktop | None:
    """Drive the non-persistent refresh cycle (per R1 + session-tracking.md):
        graceful shutdown → wait for stopped → rollback openvdi-base
        → start → wait for agent → mark available + clear floating
        assignment.

    Returns the Desktop in 'available' (success) or 'error' (failure)
    state. May return None if the row vanished mid-cycle (e.g. an
    admin destroyed it concurrently). Caller must inspect
    desktop.status to distinguish success from failure.

    Per R3: on any step failure, the desktop is flipped to
    status='error' with error_message populated. assigned_user is
    cleared on the floating side regardless of success — the user
    has logged off the OS, so the assignment doesn't survive the
    cycle.

    Raises:
      DesktopNotFound: the desktop_id has no row.
      InvalidDesktopState: pool flags say refresh-on-logoff is disabled,
        the desktop is not in a recyclable state (currently 'deleting',
        'maintenance', or already 'provisioning'), or the pool is
        persistent.
    """
    # ── Step 0: load + validate ────────────────────────────────
    desktop = await session.get(Desktop, desktop_id)
    if desktop is None:
        raise DesktopNotFound(f"desktop {desktop_id} not found")

    pool = await session.get(Pool, desktop.pool_id)
    if pool is None:
        raise InvalidDesktopState(f"pool {desktop.pool_id} not found")
    if pool.pool_type != PoolType.NONPERSISTENT:
        raise InvalidDesktopState(
            f"refresh_desktop only applies to non-persistent pools "
            f"(pool {pool.name!r} is {pool.pool_type})"
        )
    if not pool.refresh_on_logoff:
        raise InvalidDesktopState(
            f"pool {pool.name!r} has refresh_on_logoff=false"
        )
    if desktop.status in (
        DesktopStatus.DELETING,
        DesktopStatus.MAINTENANCE,
        DesktopStatus.PROVISIONING,
    ):
        raise InvalidDesktopState(
            f"desktop {desktop.name!r} is in {desktop.status.value}; "
            f"refusing to refresh"
        )

    # ── Step 1: enter PROVISIONING (signal in-flight to other callers) ─
    desktop.status = DesktopStatus.PROVISIONING
    desktop.error_message = None
    desktop.pve_task_upid = None
    desktop.pve_task_kind = None
    await session.commit()

    ref = VMRef(
        provider_type=provider.provider_type,
        data={"node": desktop.pve_node, "vmid": desktop.pve_vmid},
    )

    try:
        # ── Step 2: shutdown if running ─────────────────────────
        # Skip if already stopped — Proxmox 400s on stop-already-stopped.
        status = await provider.get_vm_status(ref)
        if status.power_state == "running":
            shutdown_handle = await provider.shutdown_vm(
                ref,
                timeout_seconds=_REFRESH_GRACEFUL_SHUTDOWN_TIMEOUT,
                force=True,
            )
            await provider.wait_for_task(
                shutdown_handle, timeout_seconds=_VM_STOP_TASK_TIMEOUT,
            )
        # Wait for power_state == stopped regardless. The Proxmox task
        # can complete before the VM actually transitions; the VMM
        # sometimes holds the disk lock briefly post-task.
        await _wait_for_stopped(provider, ref)

        # ── Step 3: rollback to openvdi-base ────────────────────
        # rollback_snapshot is provider-layer idempotent; rolling back
        # to a snapshot that's already current is a fast no-op.
        rollback_handle = await provider.rollback_snapshot(
            ref, "openvdi-base",
        )
        await provider.wait_for_task(
            rollback_handle, timeout_seconds=_VM_ROLLBACK_TASK_TIMEOUT,
        )

        # ── Step 4: start ───────────────────────────────────────
        start_handle = await provider.start_vm(ref)
        await provider.wait_for_task(
            start_handle, timeout_seconds=_VM_START_TASK_TIMEOUT,
        )

        # ── Step 5: wait for guest agent ────────────────────────
        await _wait_for_agent(provider, ref)

        # ── Step 6: success → 'available' + clear floating assignment ─
        desktop.status = DesktopStatus.AVAILABLE
        desktop.power_state = "running"
        desktop.error_message = None
        # Clear floating assignment (per R1). Persistent assignments
        # never reach refresh_desktop (validation rejects persistent
        # pools); the explicit 'floating' guard documents intent and
        # survives a future widening of the caller surface.
        if desktop.assignment_type == "floating":
            desktop.assigned_user = None
            desktop.assignment_type = None
        # last_disconnected stamps the cycle; last_connected is left
        # as the most-recent prior connect time — a future user's
        # connect overwrites it.
        desktop.last_disconnected = datetime.now(timezone.utc)
        await session.commit()

        logger.info(
            "desktop refreshed",
            extra={
                "desktop_id": str(desktop.id),
                "vmid": desktop.pve_vmid,
                "pool": pool.name,
            },
        )
        return desktop

    except (ProviderError, asyncio.TimeoutError) as exc:
        # Per R3: any step failure → 'error' state. The floating
        # assignment is still cleared — the user has logged off, the
        # desktop should not appear assigned to them in admin views
        # even though it's broken.
        await session.rollback()
        # Reload after rollback (the in-memory session is dirty after
        # the Step 1 commit's writes were rolled back).
        desktop = await session.get(Desktop, desktop_id)
        if desktop is not None:
            desktop.status = DesktopStatus.ERROR
            desktop.error_message = (
                f"refresh failed: {type(exc).__name__}: {exc}"[:1024]
            )
            if desktop.assignment_type == "floating":
                desktop.assigned_user = None
                desktop.assignment_type = None
            await session.commit()
        logger.error(
            "desktop refresh failed",
            extra={
                "desktop_id": str(desktop_id),
                "vmid": desktop.pve_vmid if desktop else None,
                "error": str(exc),
            },
        )
        return desktop  # may be None if the row vanished mid-cycle


async def delete_desktop_on_logoff(
    *,
    session: AsyncSession,
    provider: HypervisorProvider,
    desktop_id,
) -> None:
    """Drive the delete-on-logoff cycle (per R2 + session-tracking.md):
        hard stop → wait for stopped → destroy VM → remove desktop row.

    Returns None on success (the row is gone). On provider failure at
    any step, the desktop row stays in 'error' state with
    error_message populated; admin recovery is `POST /desktops/{id}/
    rebuild` (M2-15) or `DELETE /desktops/{id}` (M2-15) to retry.

    The pool_provisioner worker (M4-09) creates a replacement on its
    next tick if total_count < max_size — that's the per-R2
    'replacement' flow. M4-06 doesn't touch it.

    Raises:
      DesktopNotFound: the desktop_id has no row.
      InvalidDesktopState: pool flags say delete-on-logoff is disabled,
        the desktop is in 'deleting' state already (concurrent
        caller), or the pool is persistent.
    """
    # ── Step 0: load + validate ────────────────────────────────
    desktop = await session.get(Desktop, desktop_id)
    if desktop is None:
        raise DesktopNotFound(f"desktop {desktop_id} not found")
    pool = await session.get(Pool, desktop.pool_id)
    if pool is None:
        raise InvalidDesktopState(f"pool {desktop.pool_id} not found")
    if pool.pool_type != PoolType.NONPERSISTENT:
        raise InvalidDesktopState(
            f"delete_desktop_on_logoff only applies to non-persistent pools"
        )
    if not pool.delete_on_logoff:
        raise InvalidDesktopState(
            f"pool {pool.name!r} has delete_on_logoff=false"
        )
    if desktop.status == DesktopStatus.DELETING:
        # Concurrent caller — bail without modifying. The other caller
        # owns the cycle.
        raise InvalidDesktopState(
            f"desktop {desktop.name!r} is already being deleted"
        )

    # ── Step 1: mark DELETING ─────────────────────────────────
    desktop.status = DesktopStatus.DELETING
    desktop.error_message = None
    desktop.pve_task_upid = None
    desktop.pve_task_kind = None
    await session.commit()

    ref = VMRef(
        provider_type=provider.provider_type,
        data={"node": desktop.pve_node, "vmid": desktop.pve_vmid},
    )

    try:
        # ── Step 2: hard stop if running ───────────────────────
        # Hard stop (vs graceful shutdown) — the user has already
        # logged off, the VM is going away, no value in coordinated
        # shutdown.
        status = await provider.get_vm_status(ref)
        if status.power_state == "running":
            stop_handle = await provider.stop_vm(ref)
            await provider.wait_for_task(
                stop_handle, timeout_seconds=_VM_STOP_TASK_TIMEOUT,
            )
        await _wait_for_stopped(provider, ref)

        # ── Step 3: destroy ────────────────────────────────────
        destroy_handle = await provider.destroy_vm(ref, purge=True)
        await provider.wait_for_task(
            destroy_handle, timeout_seconds=_VM_DESTROY_TASK_TIMEOUT,
        )

        # ── Step 4: remove DB row ──────────────────────────────
        # Sessions referencing this desktop have desktop_id SET NULL
        # via M2-15-fix-2's FK behavior — the rows survive for the
        # M3-07 sessions view (orphaned-desktop case).
        await session.delete(desktop)
        await session.commit()

        logger.info(
            "desktop deleted on logoff",
            extra={
                "desktop_id": str(desktop_id),
                "vmid": desktop.pve_vmid,
                "pool": pool.name,
            },
        )
        return None

    except (ProviderError, asyncio.TimeoutError) as exc:
        await session.rollback()
        desktop = await session.get(Desktop, desktop_id)
        if desktop is not None:
            desktop.status = DesktopStatus.ERROR
            desktop.error_message = (
                f"delete-on-logoff failed: {type(exc).__name__}: {exc}"[:1024]
            )
            await session.commit()
        logger.error(
            "desktop delete-on-logoff failed",
            extra={
                "desktop_id": str(desktop_id),
                "vmid": desktop.pve_vmid if desktop else None,
                "error": str(exc),
            },
        )
        return None
