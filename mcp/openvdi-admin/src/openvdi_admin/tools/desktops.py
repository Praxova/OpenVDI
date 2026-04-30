"""Desktop admin tools.

Seven tools spanning desktop CRUD + assignment + lifecycle. The
power tool collapses start/stop/shutdown/reboot into one entry per
T2 (single tool with `action` parameter); rebuild and stop/shutdown
trigger long-running async ops wrapped with the polling pattern from
`_polling.py`.

The broker's POST /desktops/{id}/power/{action} validates the same
four-action set; we duplicate the validation client-side to save a
network round-trip and surface a clearer error.
"""
from __future__ import annotations

import logging
from typing import Any

from openvdi_admin.errors import BrokerError
from openvdi_admin._tool_wrapper import register_tool
from openvdi_admin.tools._common import (
    dry_run_envelope,
    get_broker_client,
    require_writable,
)
from openvdi_admin.tools._polling import (
    _DEFAULT_DESKTOP_POWER_TIMEOUT_SECONDS,
    _DEFAULT_DESKTOP_REBUILD_TIMEOUT_SECONDS,
    desktop_power_terminal,
    desktop_rebuild_terminal,
    wait_for_desktop_terminal_state,
)


logger = logging.getLogger(__name__)


# Mirrors the broker's _POWER_ACTION_TO_KIND keys.
_VALID_POWER_ACTIONS = frozenset({"start", "stop", "shutdown", "reboot"})


# ── Read tools ────────────────────────────────────────────────


@register_tool()
async def openvdi_list_desktops(
    pool_id: str | None = None,
    status: str | None = None,
    assigned_user: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List desktops.

    Args:
        pool_id: Filter by pool.
        status: 'provisioning', 'available', 'assigned', 'connected',
            'disconnected', 'error', 'deleting', 'maintenance'.
        assigned_user: Filter by assigned AD username (exact match).
        limit: Max results (1-200, default 50).
        offset: Pagination offset.
    """
    client = get_broker_client()
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    for k, v in (
        ("pool_id", pool_id),
        ("status", status),
        ("assigned_user", assigned_user),
    ):
        if v is not None:
            params[k] = v
    return await client.get("/api/v1/desktops", params=params)


@register_tool()
async def openvdi_get_desktop(desktop_id: str) -> dict[str, Any]:
    """Get full details for a single desktop, including the active
    session (if any) and the live power_state opportunistically read
    from the provider.

    Args:
        desktop_id: UUID of the desktop.
    """
    client = get_broker_client()
    return await client.get(f"/api/v1/desktops/{desktop_id}")


# ── Assignment ────────────────────────────────────────────────


@register_tool()
async def openvdi_assign_desktop(
    desktop_id: str,
    username: str,
    confirm: bool = False,
) -> dict[str, Any] | None:
    """Assign a desktop to a user. Admin override; bypasses the
    normal connect-flow assignment logic.

    The broker derives `assignment_type` from the pool's pool_type
    ('persistent' for persistent pools, 'floating' for nonpersistent)
    — no client-side override is supported.

    Common use cases:
      - Pre-assign a persistent desktop before user first connects.
      - Reassign after employee handoff.
      - Clear/replace a stuck assignment.

    With confirm=False (default), returns a dry-run preview showing
    current assignment (if any) and whether the target user is
    DIRECTLY entitled to the pool. The dry-run cannot detect group
    entitlements (admin endpoints don't reach LDAP per M5-01); a
    `False` direct-entitlement result does NOT mean the user lacks
    access — the broker enforces the actual entitlement check on
    connect, not on assign.

    Args:
        desktop_id: UUID of the desktop.
        username: AD username to assign to.
        confirm: True to execute.

    Raises:
        BrokerError(NOT_FOUND): no desktop with that id.
        BrokerError(CONFLICT): desktop already assigned to a different
            user, or user is already assigned to another desktop in
            the same pool (per-user-per-pool invariant).
    """
    require_writable("openvdi_assign_desktop")
    client = get_broker_client()

    if not confirm:
        desktop = await client.get(f"/api/v1/desktops/{desktop_id}")
        entitlements = await client.get(
            f"/api/v1/pools/{desktop['pool_id']}/entitlements",
        )
        direct_entitled = any(
            e["principal_type"] == "user"
            and e["principal_name"].lower() == username.lower()
            for e in entitlements
        )
        return dry_run_envelope(
            action="assign_desktop",
            target={
                "id": desktop_id,
                "name": desktop.get("name"),
                "pool_id": desktop["pool_id"],
                "current_assigned_user": desktop.get("assigned_user"),
                "new_assigned_user": username,
            },
            blocked_by=None,
            extra={
                "user_directly_entitled": direct_entitled,
                "user_may_be_group_entitled": (
                    True if not direct_entitled else None
                ),
            },
            note=(
                "Reassignment overwrites existing assignment. If "
                "user_directly_entitled is False, the user may still "
                "be entitled via group membership; broker enforces "
                "the actual entitlement check on connect, not on "
                "assign."
            ),
        )

    return await client.post(
        f"/api/v1/desktops/{desktop_id}/assign",
        body={"username": username},
    )


@register_tool()
async def openvdi_unassign_desktop(
    desktop_id: str,
    confirm: bool = False,
) -> dict[str, Any] | None:
    """Clear a desktop's assignment.

    The broker REJECTS unassignment when an active session exists
    (CONFLICT). To unassign mid-session, force-disconnect first via
    openvdi_force_disconnect_session.

    With confirm=False (default), returns a dry-run preview showing
    the current assignment and surfacing any blocking active session.

    Args:
        desktop_id: UUID of the desktop.
        confirm: True to execute.

    Raises:
        BrokerError(NOT_FOUND): no desktop with that id.
        BrokerError(CONFLICT): desktop currently has an active
            session, or desktop is not assigned.
    """
    require_writable("openvdi_unassign_desktop")
    client = get_broker_client()

    if not confirm:
        desktop = await client.get(f"/api/v1/desktops/{desktop_id}")
        sessions = await client.get(
            "/api/v1/sessions",
            params={"desktop_id": desktop_id, "status": "active"},
        )
        return dry_run_envelope(
            action="unassign_desktop",
            target={
                "id": desktop_id,
                "name": desktop.get("name"),
                "current_assigned_user": desktop.get("assigned_user"),
            },
            blocked_by=(
                {"active_sessions": sessions} if sessions else None
            ),
            note=(
                "Broker rejects unassignment when an active session "
                "exists. Force-disconnect the session first, then "
                "retry with confirm=True."
            ),
        )

    return await client.post(f"/api/v1/desktops/{desktop_id}/unassign")


# ── Lifecycle ─────────────────────────────────────────────────


@register_tool()
async def openvdi_rebuild_desktop(
    desktop_id: str,
    confirm: bool = False,
    timeout_seconds: int = _DEFAULT_DESKTOP_REBUILD_TIMEOUT_SECONDS,
) -> dict[str, Any] | None:
    """Rebuild a desktop: stop → destroy → re-clone from template →
    start. Preserves the assignment (the user's persistent assignment
    survives the rebuild) but DESTROYS user data on disk.

    The broker REJECTS rebuild while an active session exists; force-
    disconnect first. The dry-run surfaces the blocking session so
    the agent can act before calling with confirm=True.

    With confirm=False (default), returns a dry-run preview showing
    the current assignment, any blocking active session, and a
    destructive-data note.

    Args:
        desktop_id: UUID of the desktop.
        confirm: True to execute.
        timeout_seconds: Max wait for rebuild to complete. Default
            600s. On timeout, returns the last observed state — the
            rebuild may still be in progress; the agent can keep
            calling openvdi_get_desktop to track.

    Raises:
        BrokerError(NOT_FOUND): no desktop with that id.
        BrokerError(CONFLICT): active session exists, or another
            task is already in flight on this desktop.
    """
    require_writable("openvdi_rebuild_desktop")
    client = get_broker_client()

    if not confirm:
        desktop = await client.get(f"/api/v1/desktops/{desktop_id}")
        sessions = await client.get(
            "/api/v1/sessions",
            params={"desktop_id": desktop_id, "status": "active"},
        )
        return dry_run_envelope(
            action="rebuild_desktop",
            target={
                "id": desktop_id,
                "name": desktop.get("name"),
                "pool_id": desktop["pool_id"],
                "assigned_user": desktop.get("assigned_user"),
            },
            blocked_by=(
                {"active_sessions": sessions} if sessions else None
            ),
            extra={
                "destructive": (
                    "User data on the desktop's disk will be lost; "
                    "assignment survives."
                ),
            },
            note=(
                "Rebuild preserves assignment but destroys disk "
                "data. Broker rejects rebuild while an active "
                "session exists — force-disconnect first if "
                "blocked. Long-running (60-300s typical); tool "
                "polls until complete or timeout."
            ),
        )

    # Initial 202 — broker stops + queues destroy task; the worker
    # finishes the destroy → re-clone → start cycle.
    await client.post(f"/api/v1/desktops/{desktop_id}/rebuild")

    return await wait_for_desktop_terminal_state(
        fetch=lambda: client.get(f"/api/v1/desktops/{desktop_id}"),
        is_terminal=desktop_rebuild_terminal,
        timeout_seconds=timeout_seconds,
    )


@register_tool()
async def openvdi_power_desktop(
    desktop_id: str,
    action: str,
    confirm: bool = False,
    timeout_seconds: int = _DEFAULT_DESKTOP_POWER_TIMEOUT_SECONDS,
) -> dict[str, Any] | None:
    """Control desktop power state. One tool, four actions (T2).

    Actions:
      - 'start':    power on a stopped desktop. Idempotent on
                    already-running. confirm is ignored.
      - 'stop':     forceful stop (pull the plug). DESTRUCTIVE for
                    any unsaved guest state. confirm=False → dry-run.
      - 'shutdown': graceful ACPI shutdown via guest agent.
                    confirm=False → dry-run.
      - 'reboot':   graceful guest-coordinated reboot. confirm is
                    ignored (reboots are normal lifecycle).

    Polls until power_state matches the action's target
    (start/reboot → 'running'; stop/shutdown → 'stopped') or the
    timeout fires. On timeout, returns the last observed state.

    Args:
        desktop_id: UUID of the desktop.
        action: One of 'start', 'stop', 'shutdown', 'reboot'.
        confirm: For 'stop' and 'shutdown' only — True to execute,
            False (default) returns a dry-run preview. Ignored for
            'start' and 'reboot'.
        timeout_seconds: Max wait. Default 30s; raise for slow guest
            agents that don't respond promptly to ACPI signals.

    Raises:
        BrokerError(INVALID_REQUEST): bad action value.
        BrokerError(NOT_FOUND): no desktop with that id.
        BrokerError(CONFLICT): another task already in flight on
            this desktop.
    """
    require_writable("openvdi_power_desktop")
    if action not in _VALID_POWER_ACTIONS:
        raise BrokerError(
            http_status=400,
            code="INVALID_REQUEST",
            message=(
                f"action must be one of {sorted(_VALID_POWER_ACTIONS)}; "
                f"got {action!r}"
            ),
        )

    client = get_broker_client()

    # Dry-run only applies to stop/shutdown (the destructive ones).
    if action in {"stop", "shutdown"} and not confirm:
        desktop = await client.get(f"/api/v1/desktops/{desktop_id}")
        sessions = await client.get(
            "/api/v1/sessions",
            params={"desktop_id": desktop_id, "status": "active"},
        )
        return dry_run_envelope(
            action=f"{action}_desktop",
            target={
                "id": desktop_id,
                "name": desktop.get("name"),
                "current_power_state": desktop.get("power_state"),
                "assigned_user": desktop.get("assigned_user"),
            },
            blocked_by=None,
            extra={
                "active_session": sessions[0] if sessions else None,
            },
            note=(
                f"{action.title()} ends any active session. Pass "
                "confirm=True to execute."
            ),
        )

    await client.post(
        f"/api/v1/desktops/{desktop_id}/power/{action}",
    )

    target_state = (
        "running" if action in {"start", "reboot"} else "stopped"
    )
    return await wait_for_desktop_terminal_state(
        fetch=lambda: client.get(f"/api/v1/desktops/{desktop_id}"),
        is_terminal=desktop_power_terminal(target_state),
        timeout_seconds=timeout_seconds,
    )


@register_tool()
async def openvdi_delete_desktop(
    desktop_id: str,
    confirm: bool = False,
) -> dict[str, Any] | None:
    """Destroy a desktop and remove its DB record. Async on the
    broker side — DELETE returns 202; this tool returns immediately
    after the broker accepts.

    The pool may auto-provision a replacement if it's nonpersistent
    and below min_spare (handled by the warm-spare worker, not the
    MCP).

    With confirm=False (default), returns a dry-run preview.

    Args:
        desktop_id: UUID of the desktop.
        confirm: True to execute.

    Raises:
        BrokerError(NOT_FOUND): no desktop with that id.
        BrokerError(CONFLICT): another task already in flight on
            this desktop.
    """
    require_writable("openvdi_delete_desktop")
    client = get_broker_client()

    if not confirm:
        desktop = await client.get(f"/api/v1/desktops/{desktop_id}")
        sessions = await client.get(
            "/api/v1/sessions",
            params={"desktop_id": desktop_id, "status": "active"},
        )
        return dry_run_envelope(
            action="delete_desktop",
            target={
                "id": desktop_id,
                "name": desktop.get("name"),
                "pool_id": desktop["pool_id"],
                "assigned_user": desktop.get("assigned_user"),
            },
            blocked_by=None,
            extra={
                "active_session": sessions[0] if sessions else None,
            },
            note=(
                "Destroys the VM and removes the DB record. Active "
                "session is force-ended. The pool may re-provision "
                "a replacement (nonpersistent, below min_spare). "
                "Pass confirm=True to execute."
            ),
        )

    return await client.delete(f"/api/v1/desktops/{desktop_id}")
