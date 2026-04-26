"""Audit log read endpoint (M2-14 scope).

Admin-only (enforced by the router-level gate). Appends-only — there is
no POST/PUT/DELETE here; rows are written by the HTTP audit middleware
and by `app.services.audit_service.log_business_event`.

Filter surface for M2:
  actor         — exact match
  action        — exact match OR prefix via trailing `*`
  resource_type — exact match
  resource_id   — exact match (UUID)
  since / until — inclusive timestamp bounds
  sort / order / limit / offset — standard pagination

`*` is the ONLY wildcard; `?` and regex are not supported. There's no
free-text search over `details` JSONB — M4 concern once audit volume
demands it.
"""
from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from fastapi import Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.router import admin_router
from app.database import get_db_session
from app.models import AuditLog
from app.schemas import (
    APIResponse,
    AuditRead,
    PaginationParams,
)


logger = logging.getLogger(__name__)


_AUDIT_SORTABLE = frozenset({"timestamp", "actor", "action"})


@admin_router.get(
    "/audit", response_model=APIResponse[list[AuditRead]],
)
async def list_audit(
    actor: str | None = Query(None),
    action: str | None = Query(
        None,
        description=(
            "Exact match, or prefix with trailing '*' "
            "(e.g. 'broker.*' → all broker.* actions)."
        ),
    ),
    resource_type: str | None = Query(None),
    resource_id: UUID | None = Query(None),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    pagination: PaginationParams = Depends(),
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[list[AuditRead]]:
    stmt = select(AuditLog)
    if actor:
        stmt = stmt.where(AuditLog.actor == actor)
    if action:
        if action.endswith("*"):
            stmt = stmt.where(AuditLog.action.startswith(action[:-1]))
        else:
            stmt = stmt.where(AuditLog.action == action)
    if resource_type:
        stmt = stmt.where(AuditLog.resource_type == resource_type)
    if resource_id is not None:
        stmt = stmt.where(AuditLog.resource_id == resource_id)
    if since is not None:
        stmt = stmt.where(AuditLog.timestamp >= since)
    if until is not None:
        stmt = stmt.where(AuditLog.timestamp <= until)

    # Default sort: newest first. The allow-list keeps getattr safe.
    sort_key = pagination.sort or "timestamp"
    default_order = pagination.order
    if pagination.sort is None:
        # When the caller didn't specify sort, we default to timestamp
        # descending — otherwise a fresh broker's /audit returns the
        # oldest rows which is almost never what operators want.
        default_order = "desc"
    if sort_key not in _AUDIT_SORTABLE:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_REQUEST",
                "message": f"sort must be one of {sorted(_AUDIT_SORTABLE)}",
            },
        )
    col = getattr(AuditLog, sort_key)
    stmt = (
        stmt
        .order_by(col.asc() if default_order == "asc" else col.desc())
        .limit(pagination.limit)
        .offset(pagination.offset)
    )

    rows = (await session.execute(stmt)).scalars().all()
    return APIResponse(data=[AuditRead.model_validate(r) for r in rows])
