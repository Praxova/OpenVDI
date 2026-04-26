"""Admin sessions endpoints (M2-17).

Three endpoints:
  GET    /sessions             list, filtered + paginated (admin scope)
  GET    /sessions/{id}        detail with guest-agent telemetry
  DELETE /sessions/{id}        force-disconnect

Counterpart to M2-16's `/me/sessions` family — the admin views show
every session, scoped by query params instead of `WHERE username = me`.

`connection_info` (the raw broker-issued VNC ticket) NEVER appears in
any response. Schema enforcement: `SessionReadAdmin` and
`SessionReadDetailed` simply do not declare the field.
"""
from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.router import admin_router
from app.database import get_db_session
from app.models import (
    Desktop,
    Pool,
    Session as SessionModel,
    SessionStatus,
)
from app.schemas import (
    APIResponse,
    PaginationParams,
    SessionReadAdmin,
    SessionReadDetailed,
)
from app.services import broker
from app.services.audit_service import log_business_event
from app.services.auth_service import User, current_user


logger = logging.getLogger(__name__)


_SESSION_SORTABLE = frozenset(
    {"created_at", "connected_at", "ended_at", "username"}
)


def _ip_str(value) -> str | None:
    """Coerce asyncpg's IPv4Address/IPv6Address to str; pass None through.
    asyncpg hands INET back as ipaddress objects, but the wire shape is
    plain strings."""
    if value is None:
        return None
    return str(value)


@admin_router.get(
    "/sessions", response_model=APIResponse[list[SessionReadAdmin]],
)
async def list_sessions(
    username: str | None = Query(None, description="filter by session owner"),
    pool_id: UUID | None = Query(None),
    desktop_id: UUID | None = Query(None),
    status: SessionStatus | None = Query(None),
    since: datetime | None = Query(
        None, description="sessions created at or after this time",
    ),
    until: datetime | None = Query(
        None, description="sessions created at or before this time",
    ),
    include_ended: bool = Query(
        True,
        description=(
            "If false, return only connecting/active sessions; otherwise "
            "include disconnected/ended too."
        ),
    ),
    pagination: PaginationParams = Depends(),
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[list[SessionReadAdmin]]:
    sort_key = pagination.sort or "created_at"
    if sort_key not in _SESSION_SORTABLE:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_REQUEST",
                "message": f"sort must be one of {sorted(_SESSION_SORTABLE)}",
            },
        )

    # LEFT OUTER joins so orphaned sessions (desktop destroyed, FK
    # set to NULL per M2-15-fix-2) still surface. Filters that key on
    # Desktop.pool_id naturally exclude orphans, which is the right
    # behavior — "show me sessions for pool X" implies the pool's
    # desktops still exist.
    stmt = (
        select(SessionModel, Desktop, Pool)
        .outerjoin(Desktop, SessionModel.desktop_id == Desktop.id)
        .outerjoin(Pool, Desktop.pool_id == Pool.id)
    )
    if username:
        stmt = stmt.where(SessionModel.username == username)
    if pool_id is not None:
        stmt = stmt.where(Desktop.pool_id == pool_id)
    if desktop_id is not None:
        stmt = stmt.where(SessionModel.desktop_id == desktop_id)
    if status is not None:
        stmt = stmt.where(SessionModel.status == status)
    if since is not None:
        stmt = stmt.where(SessionModel.created_at >= since)
    if until is not None:
        stmt = stmt.where(SessionModel.created_at <= until)
    if not include_ended:
        stmt = stmt.where(
            SessionModel.status.in_(
                (SessionStatus.CONNECTING, SessionStatus.ACTIVE)
            )
        )

    col = getattr(SessionModel, sort_key)
    stmt = (
        stmt
        .order_by(col.asc() if pagination.order == "asc" else col.desc())
        .limit(pagination.limit)
        .offset(pagination.offset)
    )

    rows = (await session.execute(stmt)).all()
    return APIResponse(
        data=[
            SessionReadAdmin(
                id=s.id,
                desktop_id=d.id if d is not None else None,
                desktop_name=d.name if d is not None else None,
                pool_id=p.id if p is not None else None,
                pool_name=p.display_name if p is not None else None,
                pool_type=p.pool_type if p is not None else None,
                username=s.username,
                protocol=s.protocol,
                client_ip=_ip_str(s.client_ip),
                status=s.status,
                connected_at=s.connected_at,
                disconnected_at=s.disconnected_at,
                ended_at=s.ended_at,
                last_heartbeat=s.last_heartbeat,
            )
            for s, d, p in rows
        ]
    )


@admin_router.get(
    "/sessions/{session_id}",
    response_model=APIResponse[SessionReadDetailed],
)
async def get_session(
    session_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[SessionReadDetailed]:
    # LEFT OUTER joins so an orphaned session (desktop destroyed) still
    # returns 200 with desktop/pool fields nulled. A non-existent
    # session id still returns 404 — the row itself drives the lookup.
    row = (
        await session.execute(
            select(SessionModel, Desktop, Pool)
            .outerjoin(Desktop, SessionModel.desktop_id == Desktop.id)
            .outerjoin(Pool, Desktop.pool_id == Pool.id)
            .where(SessionModel.id == session_id)
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "session not found"},
        )
    s, d, p = row
    return APIResponse(
        data=SessionReadDetailed(
            id=s.id,
            desktop_id=d.id if d is not None else None,
            desktop_name=d.name if d is not None else None,
            pool_id=p.id if p is not None else None,
            pool_name=p.display_name if p is not None else None,
            pool_type=p.pool_type if p is not None else None,
            username=s.username,
            protocol=s.protocol,
            client_ip=_ip_str(s.client_ip),
            status=s.status,
            connected_at=s.connected_at,
            disconnected_at=s.disconnected_at,
            ended_at=s.ended_at,
            last_heartbeat=s.last_heartbeat,
            os_user=s.os_user,
            os_info=s.os_info,
            vm_ip_address=_ip_str(s.vm_ip_address),
            idle_since=s.idle_since,
        )
    )


@admin_router.delete(
    "/sessions/{session_id}", status_code=204,
)
async def force_disconnect_session(
    session_id: UUID,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Admin force-disconnect.

    Delegates to `broker.end_session` (same engine `/me/sessions` uses);
    differs in the actor and in writing an extra
    `admin.session.force_disconnect` audit row. The redundancy is
    intentional — it lets "show all admin force-disconnects" be a
    single `WHERE action = '...'` query instead of an end_session-rows
    + actor-!= owner-rows join.

    Idempotent on already-ended (matches /me/sessions behavior).
    """
    session_row = await session.get(SessionModel, session_id)
    if session_row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "session not found"},
        )
    if session_row.status == SessionStatus.ENDED:
        return

    # Capture session-owner identity before delegating; broker.end_session
    # will overwrite the row's status but leaves username intact.
    session_owner = session_row.username
    desktop_id = session_row.desktop_id

    await broker.end_session(
        session=session,
        session_id=session_id,
        actor_username=user.username,  # the admin, not the session owner
    )

    # Second audit row in the same session — joins broker.end_session's
    # transaction (which has already committed, so this opens a new one
    # that lives only as long as our explicit commit below).
    await log_business_event(
        session=session,
        actor=user.username,
        action="admin.session.force_disconnect",
        resource_type="session",
        resource_id=session_id,
        details={
            "session_owner": session_owner,
            "desktop_id": str(desktop_id),
        },
        client_ip=request.client.host if request.client else None,
    )
    await session.commit()
