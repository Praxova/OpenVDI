"""Entitlement CRUD scoped under a pool (M2-14 scope).

All three endpoints take `pool_id` as a path parameter. The pool row
itself is looked up first; 404 if it doesn't exist. Pools land in M2-15,
so at M2-14 every call here 404s — that's expected.

W-8: OpenVDI does not mirror AD/LDAP. No lookup on `principal_name` at
grant time; a typoed principal simply produces an entitlement that
never matches. The first user attempt to connect surfaces the problem.

Revocation of an entitlement does NOT affect active sessions. If a user
is connected via the entitlement being revoked, the session continues
until the user disconnects. Killing active sessions on revoke would be
an M4+ policy call — out of scope here.
"""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.router import admin_router
from app.database import get_db_session
from app.models import Entitlement, Pool
from app.schemas import (
    APIResponse,
    EntitlementCreate,
    EntitlementRead,
)


logger = logging.getLogger(__name__)


@admin_router.get(
    "/pools/{pool_id}/entitlements",
    response_model=APIResponse[list[EntitlementRead]],
)
async def list_entitlements(
    pool_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[list[EntitlementRead]]:
    pool = await session.get(Pool, pool_id)
    if pool is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "pool not found"},
        )
    rows = (
        await session.execute(
            select(Entitlement)
            .where(Entitlement.pool_id == pool_id)
            .order_by(Entitlement.principal_type, Entitlement.principal_name)
        )
    ).scalars().all()
    return APIResponse(
        data=[EntitlementRead.model_validate(r) for r in rows],
    )


@admin_router.post(
    "/pools/{pool_id}/entitlements",
    status_code=201,
    response_model=APIResponse[EntitlementRead],
)
async def grant_entitlement(
    pool_id: UUID,
    body: EntitlementCreate,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[EntitlementRead]:
    pool = await session.get(Pool, pool_id)
    if pool is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "pool not found"},
        )

    existing = (
        await session.execute(
            select(Entitlement).where(
                Entitlement.pool_id == pool_id,
                Entitlement.principal_type == body.principal_type,
                Entitlement.principal_name == body.principal_name,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CONFLICT",
                "message": (
                    f"{body.principal_type} '{body.principal_name}' is "
                    f"already entitled to pool '{pool.name}'"
                ),
            },
        )

    entitlement = Entitlement(
        pool_id=pool_id,
        principal_type=body.principal_type,
        principal_name=body.principal_name,
    )
    session.add(entitlement)
    await session.commit()
    await session.refresh(entitlement)
    return APIResponse(data=EntitlementRead.model_validate(entitlement))


@admin_router.delete(
    "/pools/{pool_id}/entitlements/{entitlement_id}",
    status_code=204,
)
async def revoke_entitlement(
    pool_id: UUID,
    entitlement_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    entitlement = await session.get(Entitlement, entitlement_id)
    # Cross-pool-id check guards against a stale URL deleting someone
    # else's entitlement when the UUID collides or is guessed.
    if entitlement is None or entitlement.pool_id != pool_id:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "NOT_FOUND",
                "message": "entitlement not found in this pool",
            },
        )
    await session.delete(entitlement)
    await session.commit()
