"""User-facing /me/* endpoints (M2-16 scope).

Four endpoints:
  GET    /me/desktops                         entitled pool list + current assignment
  POST   /me/desktops/{pool_id}/connect       broker a desktop (returns noVNC ticket)
  GET    /me/sessions                         user's own sessions
  DELETE /me/sessions/{session_id}            disconnect (idempotent)

The router-level `require_user` gate in `app.api.router` restricts these
to authenticated callers; each handler additionally filters by
`user.username` on the query side. The combination is what makes /me
endpoints safe — the gate alone only proves "someone is logged in",
not "this result is for them."

Connect/end delegate heavy lifting to `app.services.broker`. The broker
service also owns the `broker.connect` / `broker.session.end` audit
writes (inside the same transaction as the state change) — this module
does NOT call `log_business_event` itself.
"""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.router import user_router
from app.database import get_db_session
from app.models import (
    Session as SessionModel,
    SessionStatus,
)
from app.schemas import (
    APIResponse,
    ConnectResponse,
    UserPoolView,
    UserSessionView,
)
from app.schemas.connect import ticket_to_wire
from app.services import broker
from app.services.auth_service import User, current_user
from app.services.user_diagnostics import (
    list_pools_for_user,
    list_sessions_for_user,
)


logger = logging.getLogger(__name__)


# ── GET /me/desktops ──────────────────────────────────────────


@user_router.get(
    "/desktops",
    response_model=APIResponse[list[UserPoolView]],
)
async def list_user_desktops(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[list[UserPoolView]]:
    """List pools the caller is entitled to, with any current assignment.

    Pools in `draining` or `deleting` state remain visible with their
    status surfaced honestly — hiding them would surprise a user whose
    in-flight session is about to be torn down.

    Group entitlements are honored — passes user.groups through to
    list_pools_for_user. The /admin/users/{username}/desktops sibling
    passes groups=None and so sees direct user entitlements only
    (M5-01 § "Group resolution").
    """
    pools = await list_pools_for_user(
        session,
        username=user.username,
        groups=tuple(user.groups),
    )
    return APIResponse(data=pools)


# ── POST /me/desktops/{pool_id}/connect ───────────────────────


@user_router.post(
    "/desktops/{pool_id}/connect",
    response_model=APIResponse[ConnectResponse],
)
async def connect_desktop(
    pool_id: UUID,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[ConnectResponse]:
    """Broker a noVNC ticket for a desktop in `pool_id`.

    Thin wrapper around `app.services.broker.connect()`. The broker
    enforces entitlement, per-user-per-pool, pool status, pool full,
    and writes the `broker.connect` audit row inside its transaction.
    Exceptions from the service land in M2-11's handlers for envelope
    shaping:
        NotEntitledError    → 403 FORBIDDEN
        PoolInactiveError   → 409 CONFLICT
        PoolFullError       → 503 POOL_FULL
        ProviderError       → 5xx PROVIDER_ERROR family
        NoResultFound       → 404 NOT_FOUND (via Starlette default)
    """
    try:
        result = await broker.connect(
            session=session,
            providers=request.app.state.providers,
            username=user.username,
            groups=list(user.groups),
            pool_id=pool_id,
        )
    except NoResultFound:
        # broker.connect() loads the pool with .scalar_one(), so a
        # missing pool_id surfaces here. Translate to 404 — the service
        # docstring already anticipates this mapping.
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "pool not found"},
        )
    except KeyError:
        # broker.connect() does `providers[pool.cluster_id]` which raises
        # KeyError when the cluster is in offline / maintenance state
        # (no provider constructed). Surface as a clear 503 rather than
        # a generic 500 — it's an operations condition, not a user bug.
        raise HTTPException(
            status_code=503,
            detail={
                "code": "SERVICE_UNAVAILABLE",
                "message": (
                    "pool's cluster has no active provider; retry in a bit "
                    "or contact an administrator"
                ),
            },
        )

    return APIResponse(
        data=ConnectResponse(
            session_id=result.session_id,
            desktop_name=result.desktop_name,
            ticket=ticket_to_wire(result.ticket),
        )
    )


# ── GET /me/sessions ──────────────────────────────────────────


@user_router.get(
    "/sessions",
    response_model=APIResponse[list[UserSessionView]],
)
async def list_user_sessions(
    include_ended: bool = Query(
        False,
        description=(
            "If true, include disconnected/ended rows; otherwise return "
            "only active/connecting sessions."
        ),
    ),
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[list[UserSessionView]]:
    """List the caller's sessions. Never surfaces another user's rows."""
    sessions = await list_sessions_for_user(
        session,
        username=user.username,
        include_ended=include_ended,
        limit=limit,
    )
    return APIResponse(data=sessions)


# ── DELETE /me/sessions/{session_id} ──────────────────────────


@user_router.delete(
    "/sessions/{session_id}",
    status_code=204,
)
async def disconnect_user_session(
    session_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Disconnect the caller's session. Idempotent; 404 if not theirs.

    Ownership check: the row must both exist AND belong to the caller.
    Both miss-cases return 404 (not 403) so an attacker can't enumerate
    other users' session IDs from the status code alone — matches the
    M2-14 entitlement-revocation pattern.

    Idempotent on `ended`: we short-circuit before delegating to
    `broker.end_session()` so a double-click on Disconnect produces
    two 204s, not a 204 + 409. M2-09's tracker would otherwise raise
    `InvalidSessionStateError` for `ended → ended`.
    """
    session_row = await session.get(SessionModel, session_id)
    if session_row is None or session_row.username != user.username:
        # 404 on "not mine" → do not leak existence of other users' sessions.
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "session not found"},
        )

    if session_row.status == SessionStatus.ENDED:
        return

    await broker.end_session(
        session=session,
        session_id=session_id,
        actor_username=user.username,
    )
