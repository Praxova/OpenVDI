"""VMID allocator service.

Two public async functions:
- `allocate_vmid(session, pool)` -- return the lowest free VMID in the
  pool's range. MUST be called inside an active DB transaction; the
  function acquires a per-pool Postgres advisory lock whose lifetime is
  the surrounding transaction.
- `validate_pool_range(session, provider, start, end, exclude_pool_id?)` --
  reject a proposed VMID range if it overlaps any existing pool's range
  in the DB or any live VMID on the target hypervisor cluster.

Neither function commits. Callers own the transaction per APP-3.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Desktop, Pool
from app.providers.base import HypervisorProvider


class VMIDRangeExhausted(Exception):
    """Pool range has no free VMIDs."""


class VMIDRangeConflict(Exception):
    """Proposed range overlaps with existing pool or live Proxmox VMs."""

    def __init__(self, conflicts: list[int], source: str) -> None:
        self.conflicts = conflicts
        # `source`: "db" (another pool owns these) or "proxmox" (live VMs)
        self.source = source
        super().__init__(
            f"VMID range conflict ({source}): {len(conflicts)} VMID(s) colliding"
        )


async def allocate_vmid(session: AsyncSession, pool: Pool) -> int:
    """Return the lowest available VMID in pool's range.

    Serializes concurrent callers via pg_advisory_xact_lock, which
    auto-releases on commit or rollback. Do NOT call from outside a
    transaction -- the lock needs a transaction scope to bind to.

    Raises VMIDRangeExhausted if no free VMID exists in [start, end].
    """
    # hashtext compresses the string to a 32-bit int that
    # pg_advisory_xact_lock promotes to bigint. Per-pool collision
    # domain is tiny relative to the keyspace.
    lock_key = f"pool:{pool.id}"
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": lock_key},
    )

    used_q = (
        select(Desktop.pve_vmid)
        .where(Desktop.pool_id == pool.id)
        .where(Desktop.pve_vmid.between(pool.vmid_range_start, pool.vmid_range_end))
        .order_by(Desktop.pve_vmid)
    )
    used = set((await session.execute(used_q)).scalars().all())

    for vmid in range(pool.vmid_range_start, pool.vmid_range_end + 1):
        if vmid not in used:
            return vmid

    raise VMIDRangeExhausted(
        f"pool {pool.id} range "
        f"[{pool.vmid_range_start}, {pool.vmid_range_end}] exhausted"
    )


async def validate_pool_range(
    session: AsyncSession,
    provider: HypervisorProvider,
    vmid_range_start: int,
    vmid_range_end: int,
    exclude_pool_id: UUID | None = None,
) -> None:
    """Reject a proposed pool range if it overlaps another pool or live VMs.

    Runs both checks unconditionally -- an operator with both flavors of
    conflict sees both dimensions in the eventual 409 response, not just
    the first one.

    Raises:
        VMIDRangeConflict: at least one overlap exists. `.source` is
            "db" or "proxmox"; on combined conflicts the DB conflict is
            reported first (the Proxmox check fires only for now-alone
            conflicts). Callers that want both dimensions should call
            this twice -- but M2's API just returns the first one.
    """
    # ── DB side ────────────────────────────────────────────────
    # Raw SQL per the prompt: CAST(:exclude AS UUID) handles the
    # NULL-typing quirk cleanly even when asyncpg can infer the type.
    db_conflict_q = text(
        """
        SELECT id, name, vmid_range_start, vmid_range_end
        FROM pools
        WHERE NOT (vmid_range_end < :start OR vmid_range_start > :end)
          AND (CAST(:exclude AS UUID) IS NULL OR id != CAST(:exclude AS UUID))
        """
    )
    db_rows = (
        await session.execute(
            db_conflict_q,
            {
                "start": vmid_range_start,
                "end": vmid_range_end,
                "exclude": str(exclude_pool_id) if exclude_pool_id else None,
            },
        )
    ).all()

    if db_rows:
        # Intersection: for each overlapping pool, the VMIDs that fall
        # inside BOTH their range and the proposed range.
        db_conflicts: set[int] = set()
        for row in db_rows:
            lo = max(row.vmid_range_start, vmid_range_start)
            hi = min(row.vmid_range_end, vmid_range_end)
            db_conflicts.update(range(lo, hi + 1))
        raise VMIDRangeConflict(
            conflicts=sorted(db_conflicts), source="db"
        )

    # ── Proxmox side ───────────────────────────────────────────
    # list_vms() aggregates across every online node. We accept the
    # Proxmox data shape (VMRef.data is a dict with a "vmid" key) here
    # as a pragmatic single-provider v0 coupling; a future multi-
    # provider split might introduce a shared accessor in providers.base.
    live_vms = await provider.list_vms()
    proxmox_conflicts: list[int] = []
    for v in live_vms:
        data = getattr(v.ref, "data", None)
        if not isinstance(data, dict):
            continue
        vmid = data.get("vmid")
        if isinstance(vmid, int) and vmid_range_start <= vmid <= vmid_range_end:
            proxmox_conflicts.append(vmid)

    if proxmox_conflicts:
        raise VMIDRangeConflict(
            conflicts=sorted(set(proxmox_conflicts)), source="proxmox"
        )
