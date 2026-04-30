"""User diagnostic tools — wrap the M5-01 admin endpoints under
/api/v1/admin/users/{username}/ that mirror the /me/* user endpoints
but accept any username.

Both tools are read-only. The broker canonicalizes `username` to
lowercase before lookup (M5-01 A4); MCP forwards the input
verbatim and lets the broker normalize.

Group entitlements are NOT honored on these endpoints — the admin
JWT carries the admin's groups, not the target user's. Pools
accessible to the target user via group membership require LDAP
resolution and are surfaced separately by the M5-07 intent tool
`openvdi_diagnose_user`.
"""
from __future__ import annotations

from typing import Any

from openvdi_admin._tool_wrapper import register_tool
from openvdi_admin.tools._common import get_broker_client


@register_tool()
async def openvdi_list_user_desktops(
    username: str,
) -> list[dict[str, Any]]:
    """List pools `username` is entitled to (DIRECT user
    entitlements only) with any current assignment.

    Per M5-01: pools accessible via group entitlement are NOT
    included — admin endpoints don't reach LDAP. Use
    openvdi_diagnose_user (M5-07) for the group-aware view.

    Username matching is case-insensitive (broker canonicalizes
    to lowercase per A4). Returns an empty list when the user has
    no direct entitlements and no current assignments — broker
    has no canonical "user exists" check that doesn't reach LDAP,
    and "user has nothing" is the same answer as "user doesn't
    exist" (B4).

    Args:
        username: AD username.
    """
    client = get_broker_client()
    return await client.get(
        f"/api/v1/admin/users/{username}/desktops",
    )


@register_tool()
async def openvdi_list_user_sessions(
    username: str,
    include_ended: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List `username`'s sessions, newest-first.

    Orphan sessions (whose desktop was destroyed) surface with
    desktop_id and desktop_name set to None — preserves user
    history when the underlying desktop has been removed.

    Args:
        username: AD username (case-insensitive per M5-01 A4).
        include_ended: If True, include disconnected/ended
            sessions. Default False (active/connecting only).
        limit: Max sessions (1-200, default 50).
    """
    client = get_broker_client()
    return await client.get(
        f"/api/v1/admin/users/{username}/sessions",
        params={"include_ended": include_ended, "limit": limit},
    )
