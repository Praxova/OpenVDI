"""Pool entitlement tools."""
from __future__ import annotations

import logging
from typing import Any

from openvdi_admin.errors import BrokerError
from openvdi_admin.server import mcp
from openvdi_admin.tools._common import (
    dry_run_envelope,
    get_broker_client,
    require_writable,
)


logger = logging.getLogger(__name__)


@mcp.tool()
async def openvdi_list_entitlements(
    pool_id: str,
    principal_type: str | None = None,
) -> list[dict[str, Any]]:
    """List entitlements granting access to a pool.

    Args:
        pool_id: UUID of the pool.
        principal_type: Filter to 'user' or 'group'. Default: both.
    """
    client = get_broker_client()
    params: dict[str, Any] = {}
    if principal_type is not None:
        params["principal_type"] = principal_type
    return await client.get(
        f"/api/v1/pools/{pool_id}/entitlements",
        params=params,
    )


@mcp.tool()
async def openvdi_grant_entitlement(
    pool_id: str,
    principal_type: str,
    principal_name: str,
) -> dict[str, Any]:
    """Grant pool access to a user or AD group.

    No LDAP validation is performed at the broker — entitlements are
    stored verbatim. If the user/group doesn't exist in AD, the
    grant is harmless until someone with that name attempts to log in.

    Per the M4 A4 convention, principal_name is canonicalized to
    lowercase for principal_type='user' on the broker side. Group
    names are stored as provided (case-sensitive).

    Args:
        pool_id: UUID of the pool.
        principal_type: 'user' or 'group'.
        principal_name: AD username or group name.

    Raises:
        BrokerError(NOT_FOUND): no pool with that id.
        BrokerError(CONFLICT): entitlement already exists for this
            pool/principal combination.
    """
    require_writable("openvdi_grant_entitlement")
    client = get_broker_client()
    return await client.post(
        f"/api/v1/pools/{pool_id}/entitlements",
        body={
            "principal_type": principal_type,
            "principal_name": principal_name,
        },
    )


@mcp.tool()
async def openvdi_revoke_entitlement(
    pool_id: str,
    entitlement_id: str,
    confirm: bool = False,
) -> dict[str, Any] | None:
    """Revoke a previously granted entitlement.

    Active sessions are NOT terminated by revocation — the broker only
    blocks NEW connect requests once the entitlement is gone. To force-
    end existing sessions, use openvdi_force_disconnect_session
    (M5-05).

    With confirm=False (default), returns a dry-run preview that
    includes any active sessions that would persist post-revocation.

    Args:
        pool_id: UUID of the pool.
        entitlement_id: UUID of the entitlement row.
        confirm: True to execute.

    Raises:
        BrokerError(NOT_FOUND): no such pool or entitlement.
    """
    require_writable("openvdi_revoke_entitlement")
    client = get_broker_client()

    if not confirm:
        entitlements = await client.get(
            f"/api/v1/pools/{pool_id}/entitlements"
        )
        ent = next(
            (e for e in entitlements if e["id"] == entitlement_id),
            None,
        )
        if ent is None:
            raise BrokerError(
                http_status=404,
                code="NOT_FOUND",
                message=(
                    f"entitlement {entitlement_id} not found on pool "
                    f"{pool_id}"
                ),
            )
        # Active sessions tied to the principal — informational, not
        # blocking. Revocation is allowed to leave them running.
        sessions = (
            await client.get(
                "/api/v1/sessions",
                params={
                    "username": ent["principal_name"],
                    "pool_id": pool_id,
                    "status": "active",
                },
            )
            if ent["principal_type"] == "user"
            else []
        )
        return dry_run_envelope(
            action="revoke_entitlement",
            target={
                "id": entitlement_id,
                "principal_type": ent["principal_type"],
                "principal_name": ent["principal_name"],
            },
            blocked_by=None,
            extra={"active_sessions": sessions},
            note=(
                "Active sessions persist after revocation; the "
                "broker only blocks NEW connect requests. Use "
                "openvdi_force_disconnect_session to end existing "
                "sessions."
            ),
        )

    return await client.delete(
        f"/api/v1/pools/{pool_id}/entitlements/{entitlement_id}"
    )
