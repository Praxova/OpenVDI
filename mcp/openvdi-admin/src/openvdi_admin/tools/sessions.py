"""Session admin tools.

Three tools: list, get, force_disconnect. The broker's
DELETE /sessions/{id} is the force-disconnect endpoint — there is
no separate one — but the MCP names the tool after the action's
effect from the admin's perspective (`force_disconnect_session`,
not `delete_session`) to keep the catalog readable.
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


logger = logging.getLogger(__name__)


@mcp.tool()
async def openvdi_list_sessions(
    pool_id: str | None = None,
    desktop_id: str | None = None,
    username: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List sessions.

    Args:
        pool_id: Filter by pool.
        desktop_id: Filter by desktop.
        username: Filter by AD username.
        status: 'connecting', 'active', 'disconnected', 'ended'.
        limit: Max results (1-200, default 50).
        offset: Pagination offset.
    """
    client = get_broker_client()
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    for k, v in (
        ("pool_id", pool_id),
        ("desktop_id", desktop_id),
        ("username", username),
        ("status", status),
    ):
        if v is not None:
            params[k] = v
    return await client.get("/api/v1/sessions", params=params)


@mcp.tool()
async def openvdi_get_session(session_id: str) -> dict[str, Any]:
    """Get full session details, including guest-agent telemetry
    (os_user, os_info, vm_ip_address, last_heartbeat, idle_since)
    and the desktop / pool the session belongs to.

    Orphan sessions (where the desktop has been destroyed) return
    200 with desktop / pool fields null.

    Args:
        session_id: UUID of the session.
    """
    client = get_broker_client()
    return await client.get(f"/api/v1/sessions/{session_id}")


@mcp.tool()
async def openvdi_force_disconnect_session(
    session_id: str,
    confirm: bool = False,
) -> dict[str, Any] | None:
    """End an active session immediately. The user's connection is
    dropped; the desktop transitions to disconnected/available per
    pool policy.

    Idempotent on already-ended sessions (broker returns 204).

    With confirm=False (default), returns a dry-run preview showing
    the user, desktop, and connection start time.

    Args:
        session_id: UUID of the session.
        confirm: True to execute.

    Raises:
        BrokerError(NOT_FOUND): no session with that id.
    """
    require_writable("openvdi_force_disconnect_session")
    client = get_broker_client()

    if not confirm:
        session = await client.get(f"/api/v1/sessions/{session_id}")
        return dry_run_envelope(
            action="force_disconnect_session",
            target={
                "id": session_id,
                "username": session.get("username"),
                "desktop_id": session.get("desktop_id"),
                "desktop_name": session.get("desktop_name"),
                "status": session.get("status"),
                "connected_at": session.get("connected_at"),
            },
            blocked_by=None,
            note=(
                "Session is force-ended; user's connection drops. "
                "Idempotent on already-ended sessions. Pass "
                "confirm=True to execute."
            ),
        )

    return await client.delete(f"/api/v1/sessions/{session_id}")
