"""Pool admin tools.

Eight tools spanning pool CRUD plus the two long-running async ops
(`provision`, `drain`). Provision and drain wrap the broker's 202-
Accepted endpoints with the polling pattern in `_polling.py` so
agents see one synchronous-looking call per T6.

`get_pool_summary` is a thin synthesis tool: it reads `GET /pools/
{id}` once and returns a flat health snapshot. It's not an intent
tool (no orchestration of multiple resources) — it just packages
one read into a more-useful shape (per F2 in m5-planning-seed).
"""
from __future__ import annotations

import logging
from typing import Any

from openvdi_admin.server import mcp
from openvdi_admin.tools._common import (
    dry_run_envelope,
    get_broker_client,
    require_writable,
)
from openvdi_admin.tools._polling import (
    pool_drain_terminal,
    pool_provision_terminal,
    wait_for_pool_terminal_state,
)


logger = logging.getLogger(__name__)


# Default polling timeouts per T6.
_PROVISION_TIMEOUT_SECONDS = 300
_DRAIN_TIMEOUT_SECONDS = 600


@mcp.tool()
async def openvdi_list_pools(
    cluster_id: str | None = None,
    template_id: str | None = None,
    pool_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List desktop pools.

    Args:
        cluster_id: Filter by cluster (server-side).
        template_id: Filter by template.
        pool_type: 'persistent' or 'nonpersistent'.
        status: 'active', 'disabled', 'provisioning', 'error',
            'draining', 'deleting'.
        limit: Max results (1-200, default 50).
        offset: Pagination offset.
    """
    client = get_broker_client()
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    for k, v in (
        ("cluster_id", cluster_id),
        ("template_id", template_id),
        ("pool_type", pool_type),
        ("status", status),
    ):
        if v is not None:
            params[k] = v
    return await client.get("/api/v1/pools", params=params)


@mcp.tool()
async def openvdi_get_pool(pool_id: str) -> dict[str, Any]:
    """Get full details for a single pool — metadata, capacity
    counts, and the list of desktops in the pool.

    Args:
        pool_id: UUID of the pool.
    """
    client = get_broker_client()
    return await client.get(f"/api/v1/pools/{pool_id}")


@mcp.tool()
async def openvdi_get_pool_summary(pool_id: str) -> dict[str, Any]:
    """Compact pool health summary. Synthesizes a flat snapshot from
    the full pool record so agents asking 'is this pool healthy?'
    get one structured answer.

    Returns:
        Dict with id, name, status, pool_type, capacity counts (per
        the broker's PoolCapacityDetail), min_spare/max_size config,
        and an `issues` list flagging anomalies (error desktops,
        available below min_spare, draining).
    """
    client = get_broker_client()
    pool = await client.get(f"/api/v1/pools/{pool_id}")

    capacity_raw = pool.get("capacity") or {}
    capacity = {
        "total": capacity_raw.get("total_desktops", 0),
        "available": capacity_raw.get("available", 0),
        "assigned": capacity_raw.get("assigned", 0),
        "connected": capacity_raw.get("connected", 0),
        "disconnected": capacity_raw.get("disconnected", 0),
        "provisioning": capacity_raw.get("provisioning", 0),
        "error": capacity_raw.get("error", 0),
        "deleting": capacity_raw.get("deleting", 0),
        "free_slots": capacity_raw.get("free_slots", 0),
    }

    issues: list[str] = []
    if capacity["error"] > 0:
        issues.append(
            f"{capacity['error']} desktop(s) in error state",
        )
    min_spare = pool.get("min_spare", 0)
    if (
        pool.get("pool_type") == "nonpersistent"
        and capacity["available"] < min_spare
    ):
        issues.append(
            f"available ({capacity['available']}) below "
            f"min_spare ({min_spare})",
        )
    if pool.get("status") == "draining":
        issues.append("pool is draining; new connections rejected")
    if pool.get("status") == "error":
        issues.append("pool is in error state")

    return {
        "id": pool["id"],
        "name": pool["name"],
        "display_name": pool.get("display_name"),
        "status": pool["status"],
        "pool_type": pool["pool_type"],
        "capacity": capacity,
        "min_spare": min_spare,
        "max_size": pool.get("max_size"),
        "issues": issues,
    }


@mcp.tool()
async def openvdi_create_pool(
    name: str,
    display_name: str,
    pool_type: str,
    template_id: str,
    cluster_id: str,
    vmid_range_start: int,
    vmid_range_end: int,
    name_prefix: str,
    min_spare: int = 1,
    max_size: int = 10,
    description: str | None = None,
    target_nodes: str | None = None,
    cpu_cores: int | None = None,
    memory_mb: int | None = None,
    auto_logoff_min: int = 0,
    delete_on_logoff: bool = False,
    refresh_on_logoff: bool = True,
    pve_pool_id: str | None = None,
) -> dict[str, Any]:
    """Create a desktop pool.

    The broker validates: VMID range doesn't overlap with existing
    pools; range scan against Proxmox finds no pre-existing VMs;
    template exists; cluster reachable.

    No desktops are created on pool creation — call
    openvdi_provision_pool to provision warm spares.

    Args:
        name: URL-safe slug, [a-z0-9_-]. Used in tag values.
        display_name: Human-readable name shown in UI.
        pool_type: 'persistent' or 'nonpersistent'.
        template_id: UUID of the source template.
        cluster_id: UUID of the cluster.
        vmid_range_start, vmid_range_end: Inclusive VMID range
            for desktops in this pool.
        name_prefix: VM name prefix (e.g. 'ENG' produces
            'ENG-001', 'ENG-002').
        min_spare: Warm-spare target (nonpersistent only).
        max_size: Max desktops in the pool.
        description: Free-form description.
        target_nodes: Optional comma-separated node names.
        cpu_cores, memory_mb: Override template defaults; null
            inherits from template.
        auto_logoff_min: Idle-disconnect threshold; 0 = disabled.
        delete_on_logoff: Destroy desktop on logoff (nonpersistent).
        refresh_on_logoff: Rollback to openvdi-base on logoff
            (nonpersistent). Default True.
        pve_pool_id: Proxmox-side organizational pool id. Optional.

    Raises:
        BrokerError(CONFLICT): VMID range overlap or pre-existing
            VMs in the range.
        BrokerError(NOT_FOUND): template or cluster doesn't exist.
    """
    require_writable("openvdi_create_pool")
    client = get_broker_client()
    body: dict[str, Any] = {
        "name": name,
        "display_name": display_name,
        "pool_type": pool_type,
        "template_id": template_id,
        "cluster_id": cluster_id,
        "vmid_range_start": vmid_range_start,
        "vmid_range_end": vmid_range_end,
        "name_prefix": name_prefix,
        "min_spare": min_spare,
        "max_size": max_size,
        "auto_logoff_min": auto_logoff_min,
        "delete_on_logoff": delete_on_logoff,
        "refresh_on_logoff": refresh_on_logoff,
    }
    for field, value in (
        ("description", description),
        ("target_nodes", target_nodes),
        ("cpu_cores", cpu_cores),
        ("memory_mb", memory_mb),
        ("pve_pool_id", pve_pool_id),
    ):
        if value is not None:
            body[field] = value
    return await client.post("/api/v1/pools", body=body)


@mcp.tool()
async def openvdi_update_pool(
    pool_id: str,
    display_name: str | None = None,
    description: str | None = None,
    min_spare: int | None = None,
    max_size: int | None = None,
    cpu_cores: int | None = None,
    memory_mb: int | None = None,
    auto_logoff_min: int | None = None,
    delete_on_logoff: bool | None = None,
    refresh_on_logoff: bool | None = None,
    target_nodes: str | None = None,
) -> dict[str, Any]:
    """Update pool settings. Only fields you pass are modified.

    Identity fields (name, pool_type, template_id, cluster_id,
    vmid range, name_prefix, pve_pool_id) are NOT modifiable —
    delete and recreate the pool to change these.

    `status` is also not settable here. Use openvdi_drain_pool to
    initiate a drain or openvdi_delete_pool for tear-down. The
    broker rejects PUT-with-status to keep audit lines and active-
    session checks centralized.

    Args:
        pool_id: UUID of the pool.
        ... see openvdi_create_pool.

    Raises:
        BrokerError(NOT_FOUND): no pool with that id.
        BrokerError(INVALID_REQUEST): max_size below current
            desktop_count, or other validation failure.
    """
    require_writable("openvdi_update_pool")
    client = get_broker_client()
    body: dict[str, Any] = {}
    for field, value in (
        ("display_name", display_name),
        ("description", description),
        ("min_spare", min_spare),
        ("max_size", max_size),
        ("cpu_cores", cpu_cores),
        ("memory_mb", memory_mb),
        ("auto_logoff_min", auto_logoff_min),
        ("delete_on_logoff", delete_on_logoff),
        ("refresh_on_logoff", refresh_on_logoff),
        ("target_nodes", target_nodes),
    ):
        if value is not None:
            body[field] = value
    return await client.put(f"/api/v1/pools/{pool_id}", body=body)


@mcp.tool()
async def openvdi_delete_pool(
    pool_id: str,
    confirm: bool = False,
) -> dict[str, Any] | None:
    """Delete a pool. CASCADES through desktops, entitlements, and
    forces ongoing sessions to end.

    With confirm=False (default), returns a dry-run preview of the
    full impact: desktop count, active session count, entitlement
    count.

    Args:
        pool_id: UUID of the pool.
        confirm: True to execute.

    Raises:
        BrokerError(NOT_FOUND): no pool with that id.
    """
    require_writable("openvdi_delete_pool")
    client = get_broker_client()

    if not confirm:
        pool = await client.get(f"/api/v1/pools/{pool_id}")
        sessions = await client.get(
            "/api/v1/sessions",
            params={"pool_id": pool_id, "status": "active"},
        )
        entitlements = await client.get(
            f"/api/v1/pools/{pool_id}/entitlements",
        )
        capacity = pool.get("capacity") or {}
        return dry_run_envelope(
            action="delete_pool",
            target={
                "id": pool_id,
                "name": pool.get("name"),
                "display_name": pool.get("display_name"),
            },
            blocked_by=None,
            extra={
                "would_destroy": {
                    "desktops": capacity.get("total_desktops", 0),
                    "active_sessions": len(sessions),
                    "entitlements": len(entitlements),
                },
            },
            note=(
                "Cascades through desktops (destroyed), entitlements "
                "(removed), and active sessions (force-ended). Pass "
                "confirm=True to execute."
            ),
        )

    return await client.delete(f"/api/v1/pools/{pool_id}")


@mcp.tool()
async def openvdi_provision_pool(
    pool_id: str,
    count: int,
    timeout_seconds: int = _PROVISION_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Provision desktops in the pool. Returns the pool's final
    state after provisioning completes (or the timeout fires).

    The broker spawns clone tasks asynchronously; this tool polls
    `GET /pools/{id}` every 2s until no desktops remain in
    `provisioning` / `deleting` status, OR the timeout fires.

    On timeout, the LAST observed state is returned — inspect
    `capacity.provisioning` to see what's still in flight; subsequent
    `openvdi_get_pool` calls can be used to keep watching.

    Args:
        pool_id: UUID of the pool.
        count: Number of desktops to add (1-50). Required.
        timeout_seconds: Max wait. Default 300s; raise for slow
            storage.

    Returns:
        Pool state dict (PoolReadDetailed shape); check
        `capacity.provisioning == 0` for completion.

    Raises:
        BrokerError(NOT_FOUND): no pool with that id.
        BrokerError(CONFLICT): pool not active, or count would
            exceed max_size.
    """
    require_writable("openvdi_provision_pool")
    client = get_broker_client()

    # Initial 202 — broker accepts the request and queues the
    # background clone tasks.
    await client.post(
        f"/api/v1/pools/{pool_id}/provision",
        body={"count": count},
    )

    # Poll until no desktops are provisioning/deleting, or timeout.
    return await wait_for_pool_terminal_state(
        fetch=lambda: client.get(f"/api/v1/pools/{pool_id}"),
        is_terminal=pool_provision_terminal,
        timeout_seconds=timeout_seconds,
    )


@mcp.tool()
async def openvdi_drain_pool(
    pool_id: str,
    confirm: bool = False,
    timeout_seconds: int = _DRAIN_TIMEOUT_SECONDS,
) -> dict[str, Any] | None:
    """Drain a pool: stop new connections and wait for existing
    sessions to end naturally.

    The broker flips `pool.status` to 'draining' and refuses NEW
    connect requests; sessions already running continue until
    users log off or the auto-logoff worker times them out. The
    pool stays in 'draining' indefinitely (no auto-flip to
    disabled) — use openvdi_delete_pool or a future un-drain
    endpoint to transition out.

    With confirm=False (default), returns a dry-run preview
    showing active sessions that would persist until they end.

    Args:
        pool_id: UUID of the pool.
        confirm: True to execute.
        timeout_seconds: Max wait for sessions to end. Default
            600s. On timeout, drain is still in progress on the
            broker — re-query openvdi_get_pool to check.

    Raises:
        BrokerError(NOT_FOUND): no pool with that id.
        BrokerError(CONFLICT): pool not in 'active' state (already
            draining, disabled, etc.).
    """
    require_writable("openvdi_drain_pool")
    client = get_broker_client()

    if not confirm:
        pool = await client.get(f"/api/v1/pools/{pool_id}")
        sessions = await client.get(
            "/api/v1/sessions",
            params={"pool_id": pool_id, "status": "active"},
        )
        return dry_run_envelope(
            action="drain_pool",
            target={"id": pool_id, "name": pool.get("name")},
            blocked_by=None,
            extra={
                "active_sessions": [
                    {"id": s["id"], "username": s.get("username")}
                    for s in sessions
                ],
            },
            note=(
                "Drain stops new connections and waits for active "
                "sessions to end naturally. Pool stays 'draining' "
                "until you delete or re-enable it. Pass confirm=True "
                "to execute."
            ),
        )

    # Initial 202 — broker flips pool.status to 'draining'.
    await client.post(f"/api/v1/pools/{pool_id}/drain")

    # Compose a fetch that returns pool record + active session
    # count so pool_drain_terminal has both signals in one dict.
    async def fetch() -> dict[str, Any]:
        pool = await client.get(f"/api/v1/pools/{pool_id}")
        sessions = await client.get(
            "/api/v1/sessions",
            params={"pool_id": pool_id, "status": "active"},
        )
        return {**pool, "_active_session_count": len(sessions)}

    return await wait_for_pool_terminal_state(
        fetch=fetch,
        is_terminal=pool_drain_terminal,
        timeout_seconds=timeout_seconds,
    )
