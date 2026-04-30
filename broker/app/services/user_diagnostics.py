"""User-scoped diagnostic queries — extracted from app.api.user (M2-16)
in M5-01 so /me/* and /admin/users/{username}/* share their logic.

Two functions:
  - list_pools_for_user      → for /me/desktops AND /admin/users/.../desktops
  - list_sessions_for_user   → for /me/sessions  AND /admin/users/.../sessions

The admin variants pass groups=None (no group-entitlement resolution
because the admin's JWT doesn't carry the target user's groups; LDAP
isn't queried from this layer). The user variants pass the calling
user's actual groups list. See M5-01 prompt § "Group resolution" for
the full reasoning.
"""
from __future__ import annotations

from typing import Sequence
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Desktop,
    DesktopStatus,
    Entitlement,
    Pool,
    Session as SessionModel,
    SessionStatus,
)
from app.schemas import UserDesktopView, UserPoolView, UserSessionView


_ACTIVE_SESSION_STATUSES = (SessionStatus.CONNECTING, SessionStatus.ACTIVE)


async def list_pools_for_user(
    session: AsyncSession,
    *,
    username: str,
    groups: Sequence[str] | None,
) -> list[UserPoolView]:
    """Return pools entitled to `username`, with current per-pool assignment.

    `username` MUST already be canonical-lowercase per A4. The caller
    (API layer) is responsible for the .lower() — putting it here would
    mean two layers do the same coercion silently.

    `groups`:
      - sequence of group names → include pools entitled via group
        membership in addition to direct user entitlements. Used by
        /me/desktops where the caller's groups are known from the JWT.
      - None → return ONLY pools with a direct user entitlement.
        Used by /admin/users/{username}/desktops where the calling
        admin's JWT does not carry the target user's groups, and the
        broker does not query AD from admin endpoints.

    Empty list (`[]`) for `groups` is treated as "user variant with no
    group memberships" — direct entitlements still apply. Conflating
    `[]` with `None` would silently break the admin contract.

    Per pool, surfaces an `assigned_desktop` if the user holds one
    (status != deleting). At most one row per pool by the
    per-user-per-pool invariant (M2-08 / M2-15 enforce).
    """
    direct = and_(
        Entitlement.principal_type == "user",
        Entitlement.principal_name == username,
    )
    if groups is not None and len(groups) > 0:
        entitlement_filter = or_(
            direct,
            and_(
                Entitlement.principal_type == "group",
                Entitlement.principal_name.in_(groups),
            ),
        )
    else:
        entitlement_filter = direct

    pools_stmt = (
        select(Pool)
        .join(Entitlement, Entitlement.pool_id == Pool.id)
        .where(entitlement_filter)
        .distinct()
        .order_by(Pool.display_name)
    )
    pools = (await session.execute(pools_stmt)).scalars().all()
    if not pools:
        return []

    pool_ids: list[UUID] = [p.id for p in pools]
    assigned = (
        await session.execute(
            select(Desktop).where(
                Desktop.pool_id.in_(pool_ids),
                Desktop.assigned_user == username,
                Desktop.status != DesktopStatus.DELETING,
            )
        )
    ).scalars().all()
    by_pool: dict[UUID, Desktop] = {d.pool_id: d for d in assigned}

    return [
        UserPoolView(
            id=pool.id,
            name=pool.name,
            display_name=pool.display_name,
            description=pool.description,
            pool_type=pool.pool_type,
            status=pool.status,
            assigned_desktop=(
                UserDesktopView.model_validate(by_pool[pool.id])
                if pool.id in by_pool
                else None
            ),
        )
        for pool in pools
    ]


async def list_sessions_for_user(
    session: AsyncSession,
    *,
    username: str,
    include_ended: bool,
    limit: int,
) -> list[UserSessionView]:
    """Return `username`'s sessions ordered newest-first.

    `username` MUST already be canonical-lowercase per A4. The caller
    (API layer) is responsible for the .lower().

    `include_ended=False` returns only connecting / active rows;
    `include_ended=True` returns the full history up to `limit`.

    Orphan sessions (`desktop_id IS NULL` per the M2-15 ON DELETE
    SET NULL relationship) surface with `desktop_name` and
    `pool_name` rendered as None — this is intentional history
    visibility, not a bug.
    """
    stmt = (
        select(SessionModel, Desktop, Pool)
        .outerjoin(Desktop, SessionModel.desktop_id == Desktop.id)
        .outerjoin(Pool, Desktop.pool_id == Pool.id)
        .where(SessionModel.username == username)
    )
    if not include_ended:
        stmt = stmt.where(SessionModel.status.in_(_ACTIVE_SESSION_STATUSES))
    stmt = stmt.order_by(SessionModel.created_at.desc()).limit(limit)

    rows = (await session.execute(stmt)).all()
    return [
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
