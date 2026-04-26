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
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.router import user_router
from app.database import get_db_session
from app.models import (
    Desktop,
    DesktopStatus,
    Entitlement,
    Pool,
    Session as SessionModel,
    SessionStatus,
)
from app.schemas import (
    APIResponse,
    ConnectResponse,
    UserDesktopView,
    UserPoolView,
    UserSessionView,
)
from app.schemas.connect import ticket_to_wire
from app.services import broker
from app.services.auth_service import User, current_user


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
    """
    # Entitled pool ids: direct user match OR one of the user's groups.
    # Using a single query with a `distinct()` is cheaper than fetching
    # entitlements and then pools. `.in_((,))` on asyncpg evaluates
    # fine (produces `IN ()` which is always false) but the outer OR
    # means the direct-user match still fires when `user.groups` is
    # empty.
    entitled_stmt = (
        select(Pool)
        .join(Entitlement, Entitlement.pool_id == Pool.id)
        .where(
            or_(
                and_(
                    Entitlement.principal_type == "user",
                    Entitlement.principal_name == user.username,
                ),
                and_(
                    Entitlement.principal_type == "group",
                    Entitlement.principal_name.in_(user.groups),
                ),
            )
        )
        .distinct()
        .order_by(Pool.display_name)
    )
    pools = (await session.execute(entitled_stmt)).scalars().all()
    if not pools:
        return APIResponse(data=[])

    pool_ids = [p.id for p in pools]
    assigned_rows = (
        await session.execute(
            select(Desktop).where(
                Desktop.pool_id.in_(pool_ids),
                Desktop.assigned_user == user.username,
                Desktop.status != DesktopStatus.DELETING,
            )
        )
    ).scalars().all()
    # At most one row per pool by the per-user-per-pool invariant
    # (M2-08 enforces it at connect time; M2-15 at admin assign).
    by_pool: dict[UUID, Desktop] = {d.pool_id: d for d in assigned_rows}

    return APIResponse(
        data=[
            UserPoolView(
                id=pool.id,
                name=pool.name,
                display_name=pool.display_name,
                description=pool.description,
                pool_type=pool.pool_type,
                status=pool.status,
                assigned_desktop=(
                    UserDesktopView.model_validate(by_pool[pool.id])
                    if pool.id in by_pool else None
                ),
            )
            for pool in pools
        ]
    )


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


_ACTIVE_SESSION_STATUSES = (SessionStatus.CONNECTING, SessionStatus.ACTIVE)


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
    """List the caller's sessions. Never surfaces another user's rows.

    Three-table join so desktop + pool names land inline — users don't
    want to issue three requests to render a session list. Volume is
    low (a handful of rows per user) so the join is cheap.
    """
    # LEFT OUTER joins so sessions whose desktop has been destroyed
    # (M2-15-fix-2: sessions.desktop_id is ON DELETE SET NULL) still
    # surface in history. The denormalized desktop/pool fields end up
    # None for those orphans; the session-side fields always populate.
    stmt = (
        select(SessionModel, Desktop, Pool)
        .outerjoin(Desktop, SessionModel.desktop_id == Desktop.id)
        .outerjoin(Pool, Desktop.pool_id == Pool.id)
        .where(SessionModel.username == user.username)
    )
    if not include_ended:
        stmt = stmt.where(SessionModel.status.in_(_ACTIVE_SESSION_STATUSES))
    stmt = stmt.order_by(SessionModel.created_at.desc()).limit(limit)

    rows = (await session.execute(stmt)).all()
    return APIResponse(
        data=[
            UserSessionView(
                id=s.id,
                desktop_id=d.id if d is not None else None,
                desktop_name=d.name if d is not None else None,
                pool_id=p.id if p is not None else None,
                pool_name=p.display_name if p is not None else None,
                protocol=s.protocol,
                status=s.status,
                connected_at=s.connected_at,
                disconnected_at=s.disconnected_at,
                ended_at=s.ended_at,
            )
            for s, d, p in rows
        ]
    )


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
