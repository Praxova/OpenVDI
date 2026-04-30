"""openvdi_diagnose_user intent tool — answers 'what's Alice's
situation?' in one round-trip from the agent's perspective.

The honest scope: direct user entitlements are authoritative; the
broker's admin endpoints don't reach LDAP, so we can't verify
group memberships from here. The result clearly separates the
authoritative `directly_entitled_pools` view from the unresolved
`potential_group_entitlements` list — agents (or IT Agent
companion tools) reach for AD if they need to confirm.

Read-only. No writable gate; works in OPENVDI_MCP_READ_ONLY=true
deployments by design.
"""
from __future__ import annotations

import logging
from typing import Any

from openvdi_admin.errors import BrokerError
from openvdi_admin.intent._result import StepTracker
from openvdi_admin.server import mcp
from openvdi_admin.tools.entitlements import openvdi_list_entitlements
from openvdi_admin.tools.pools import openvdi_get_pool, openvdi_list_pools
from openvdi_admin.tools.user_diagnostics import (
    openvdi_list_user_desktops,
    openvdi_list_user_sessions,
)


logger = logging.getLogger(__name__)


@mcp.tool()
async def openvdi_diagnose_user(username: str) -> dict[str, Any]:
    """Comprehensive single-round-trip diagnostic for a user.

    Aggregates:
      - direct user entitlements + per-pool blocking_factor heuristic
      - active sessions (newest 10)
      - recent sessions including ended (newest 10)
      - potential group entitlements across all pools where the user
        is NOT directly entitled — UNRESOLVED; agent verifies group
        membership separately

    Username matching is broker-side case-insensitive (M5-01 A4); MCP
    forwards verbatim.

    Args:
        username: AD username.

    Returns:
        IntentResult-shaped dict. Always returns a structured result
        — broker errors surface via failure_result, not as raised
        exceptions.
    """
    tracker = StepTracker()

    try:
        async with tracker.step("fetch_user_desktops") as step:
            user_desktops = await openvdi_list_user_desktops(username)
            step["details"] = {
                "directly_entitled_pool_count": len(user_desktops),
            }

        async with tracker.step("fetch_active_sessions") as step:
            active_sessions = await openvdi_list_user_sessions(
                username=username, include_ended=False, limit=10,
            )
            step["details"] = {"active_count": len(active_sessions)}

        async with tracker.step("fetch_recent_sessions") as step:
            recent_sessions = await openvdi_list_user_sessions(
                username=username, include_ended=True, limit=10,
            )
            step["details"] = {"total_recent": len(recent_sessions)}

        directly_entitled_pools: list[dict[str, Any]] = []
        async with tracker.step("compute_blocking_factors") as step:
            for pool_view in user_desktops:
                pool_id = pool_view["id"]
                pool = await openvdi_get_pool(pool_id)
                blocking = _compute_blocking_factor(pool)
                directly_entitled_pools.append({
                    "pool_id": pool_id,
                    "pool_name": (
                        pool_view.get("display_name")
                        or pool_view.get("name")
                    ),
                    "pool_type": pool.get("pool_type"),
                    "status": pool.get("status"),
                    "capacity": pool.get("capacity", {}),
                    "min_spare": pool.get("min_spare"),
                    "max_size": pool.get("max_size"),
                    "blocking_factor": blocking,
                    "currently_assigned_desktop": (
                        pool_view.get("assigned_desktop")
                    ),
                })
            step["details"] = {
                "with_blocking_factor": sum(
                    1 for p in directly_entitled_pools
                    if p["blocking_factor"]
                ),
            }

        # Group-entitlement walk: per-pool query because the broker
        # has no "all entitlements" endpoint. N requests for N pools;
        # acceptable at v0 scale (5-50 pools). M6+ candidate.
        async with tracker.step(
            "collect_potential_group_entitlements",
        ) as step:
            all_pools = await openvdi_list_pools(limit=200)
            potential: list[dict[str, Any]] = []
            directly_entitled_pool_ids = {
                p["pool_id"] for p in directly_entitled_pools
            }
            for pool in all_pools:
                if pool["id"] in directly_entitled_pool_ids:
                    continue
                ents = await openvdi_list_entitlements(
                    pool_id=pool["id"],
                    principal_type="group",
                )
                for ent in ents:
                    potential.append({
                        "pool_id": pool["id"],
                        "pool_name": (
                            pool.get("display_name") or pool["name"]
                        ),
                        "via_group": ent["principal_name"],
                    })
            step["details"] = {"count": len(potential)}

        return tracker.success_result(
            operation="diagnose_user",
            result={
                "username": username,
                "directly_entitled_pools": directly_entitled_pools,
                "active_sessions": active_sessions,
                "recent_sessions": recent_sessions,
                "potential_group_entitlements": potential,
                "summary": _summarize_user_state(
                    directly_entitled_pools,
                    active_sessions,
                    potential,
                ),
            },
        )

    except BrokerError as exc:
        return tracker.failure_result(
            operation="diagnose_user",
            error=exc,
            failed_at_step=tracker.last_failed_step(),
        )


def _compute_blocking_factor(pool: dict[str, Any]) -> str | None:
    """Return a short string describing why a connect would fail
    right now, or None if the pool would accept a connect.

    Heuristic — broker is the source of truth at connect time. The
    pool's state may change between this call and an actual connect
    request.
    """
    status = pool.get("status")
    if status == "draining":
        return "POOL_DRAINING"
    if status == "disabled":
        return "POOL_DISABLED"
    if status == "error":
        return "POOL_ERROR"
    capacity = pool.get("capacity") or {}
    available = capacity.get("available", 0)
    if available == 0:
        max_size = pool.get("max_size", 0)
        # Broker's PoolCapacityDetail surfaces `total_desktops`.
        total = capacity.get("total_desktops", 0)
        if total >= max_size:
            return "POOL_FULL"
        # Headroom in the pool but no warm spares; v0 connect doesn't
        # auto-provision so the connect would still fail.
        return "POOL_EMPTY"
    return None


def _summarize_user_state(
    pools: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
    group_ents: list[dict[str, Any]],
) -> str:
    """One-line human-readable summary for the agent UI."""
    if sessions:
        n = len(sessions)
        return f"{n} active session{'s' if n != 1 else ''}"
    blocking = [p for p in pools if p["blocking_factor"]]
    if pools and not blocking:
        return (
            f"entitled to {len(pools)} pool(s); could connect now"
        )
    if blocking:
        codes = sorted({p["blocking_factor"] for p in blocking})
        return f"entitled but blocked: {', '.join(codes)}"
    if group_ents:
        return (
            f"no direct entitlements; {len(group_ents)} potential "
            "group entitlement(s) — verify via IT Agent"
        )
    return "no entitlements found"
