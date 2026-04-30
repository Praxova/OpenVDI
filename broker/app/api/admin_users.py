"""Admin user-diagnostics endpoints (M5-01).

Two read-only endpoints under /api/v1/admin/users/{username}/...
that mirror the /me/* user variants but accept any username. Used
by the M5 MCP server's `openvdi_diagnose_user` intent tool to answer
"what does Alice's account look like" without round-tripping
multiple endpoints.

Group entitlements are NOT honored here — the admin's JWT carries
the admin's groups, not the target user's. The endpoint returns
only pools entitled to `username` via DIRECT user match. Pools
accessible via group membership require LDAP knowledge of the
target user's groups, which is out of scope for v0 admin endpoints
(see M5-01 prompt § "Group resolution").
"""
from __future__ import annotations

import logging

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.router import admin_router
from app.database import get_db_session
from app.schemas import APIResponse, UserPoolView, UserSessionView
from app.services.user_diagnostics import (
    list_pools_for_user,
    list_sessions_for_user,
)


logger = logging.getLogger(__name__)


@admin_router.get(
    "/admin/users/{username}/desktops",
    response_model=APIResponse[list[UserPoolView]],
)
async def admin_list_user_desktops(
    username: str,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[list[UserPoolView]]:
    """Pools `username` is entitled to (DIRECT user entitlements only),
    with any current assignment.

    Returns 200 with empty list when the username has no direct
    entitlements and no current assignments — the broker has no
    canonical "user exists" check that doesn't reach LDAP, and
    "user has nothing" is the same useful answer as "user doesn't
    exist." Per decision B4.

    Username matching is case-insensitive (canonicalized to lowercase
    before lookup) per A4.
    """
    canonical = username.lower()
    pools = await list_pools_for_user(
        session, username=canonical, groups=None,
    )
    return APIResponse(data=pools)


@admin_router.get(
    "/admin/users/{username}/sessions",
    response_model=APIResponse[list[UserSessionView]],
)
async def admin_list_user_sessions(
    username: str,
    include_ended: bool = Query(
        False,
        description=(
            "If true, include disconnected/ended rows; otherwise return "
            "only active/connecting sessions."
        ),
    ),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[list[UserSessionView]]:
    """`username`'s sessions, newest-first. Orphan sessions (whose
    desktop was destroyed) surface with desktop_name / pool_name
    as None.

    Username matching is case-insensitive per A4.
    """
    canonical = username.lower()
    sessions = await list_sessions_for_user(
        session,
        username=canonical,
        include_ended=include_ended,
        limit=limit,
    )
    return APIResponse(data=sessions)
