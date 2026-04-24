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


class PoolInactive(Exception):
    """Pool is not in 'active' status; provisioning is refused."""


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


async def provision_desktop(
    *,
    session: AsyncSession,
    provider: HypervisorProvider,
    pool: Pool,
    template: Template,
    assigned_user: str | None = None,
) -> Desktop:
    """Provision one desktop for the given pool.

    See module docstring for session/commit semantics.
    `assigned_user` is set on the Desktop row when the broker has
    pre-assigned (persistent-pool new-user case); for warm-spare
    provisioning the argument stays None.

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

    # Step 1: allocate VMID + reserve Desktop row (flush, do not commit)
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
            # Step 7-alt: graceful shutdown → openvdi-base snapshot →
            # start again → wait agent
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
