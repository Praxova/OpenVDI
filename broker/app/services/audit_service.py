"""Service-level audit — business events the HTTP middleware can't describe.

The HTTP middleware in `app/middleware/audit.py` captures the outer shape
of every admin mutation (`POST /api/v1/pools` → row with method/path/body).
This module is the richer counterpart: it records named events like
`broker.connect`, `pool.provisioning.started` with the structured
context the handler has in hand.

Transaction semantics are the whole point: `log_business_event` joins
the caller's transaction rather than opening a new one. If the caller
rolls back, the audit row rolls back too. We do NOT want audit trails of
things that didn't actually happen; the HTTP middleware already records
the attempt, which is the correct shape for that story.

Callers construct their own `details` dicts. No automatic redaction at
this layer — callers are trusted code paths that know what's in their
payloads. The HTTP middleware redacts because the request body is
client-controlled; different threat model.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


async def log_business_event(
    session: AsyncSession,
    *,
    actor: str,
    action: str,
    resource_type: str | None = None,
    resource_id: UUID | None = None,
    details: dict[str, Any] | None = None,
    client_ip: str | None = None,
) -> None:
    """Record a business-level audit event in the caller's transaction.

    Parameters
    ----------
    session
        Active AsyncSession. This function does NOT commit — the caller
        commits along with its own work.
    actor
        Username responsible. For system-initiated events (background
        pings, scheduled tasks when they arrive), pass "system".
    action
        Dotted name, e.g. `broker.connect`, `broker.session.end`,
        `pool.provisioning.started`. Distinct from HTTP-level actions
        which use `METHOD /path` form. No validation here — typos
        produce valid rows that just won't aggregate with their siblings.
    resource_type, resource_id
        Populate to enable resource-scoped audit queries later.
    details
        Free-form JSON. No automatic redaction — caller owns the shape.
    client_ip
        Pass when available (from `request.client.host` at the API
        layer); None otherwise. Workers and background tasks pass None.

    Caller pattern:

        await log_business_event(
            session=session,
            actor=username,
            action="broker.connect",
            resource_type="session",
            resource_id=session_row.id,
            details={
                "pool_id": str(pool.id),
                "pool_name": pool.name,
                "desktop_name": desktop.name,
            },
            client_ip=request.client.host if request.client else None,
        )
    """
    row = AuditLog(
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details or {},
        client_ip=client_ip,
    )
    session.add(row)
    # No commit — the caller's transaction owns this.
