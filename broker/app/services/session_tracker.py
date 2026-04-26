"""Session state-machine transitions.

A small library, not a worker. Functions mutate the passed-in Session
(and Desktop, where applicable) ORM rows and return None — the caller
owns the transaction.

State machine (docs/session-tracking.md → Layer 1):

    connecting ──► active ──► disconnected ──► ended
        │                          ▲                │
        └──────────────────────────┼────────────────┘
                                   │
                             admin-force-kill

- connecting → active        (broker.connect() after ticket issued)
- active → disconnected      (M4's session monitor, unused in M2)
- active → ended             (user self-disconnect or admin kill)
- disconnected → ended       (M4 timeout cleanup; exposed for completeness)
- connecting → ended         (fail-safe cleanup)

Transitioning to `ended` ALWAYS clears connection_info (S-D2 — no stale
tickets in the DB after session end). This is a security invariant.

Worker-layer session monitoring (guest-agent polling, OS-logoff
detection, idle detection) is M4 scope — not here.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, null as sql_null, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Desktop, DesktopStatus, Session, SessionStatus


class InvalidSessionStateError(Exception):
    """Attempted transition is not legal from the session's current state."""

    def __init__(
        self,
        session_id: UUID,
        current: SessionStatus,
        attempted: str,
    ) -> None:
        super().__init__(
            f"session {session_id} is in {current.value}; "
            f"cannot transition via {attempted}"
        )


async def transition_to_active(
    session: AsyncSession,
    session_row: Session,
    connection_info: dict[str, Any],
) -> None:
    """connecting → active.

    Sets `status=active`, populates `connection_info`, and sets
    `connected_at` if it's still unset.
    """
    if session_row.status != SessionStatus.CONNECTING:
        raise InvalidSessionStateError(
            session_row.id, session_row.status, "transition_to_active",
        )
    session_row.status = SessionStatus.ACTIVE
    session_row.connection_info = connection_info
    if session_row.connected_at is None:
        session_row.connected_at = func.now()


async def transition_to_disconnected(
    session: AsyncSession,
    session_row: Session,
) -> None:
    """active → disconnected.

    Sets `disconnected_at`. Does NOT clear `connection_info` — the
    session hasn't ended yet and a reconnect might still reuse the
    ticket (or at least the ticket data, until Proxmox expires it).
    The unconditional clear lives in `transition_to_ended`.

    In M2 no code path triggers this transition; it exists for M4's
    session monitor.
    """
    if session_row.status != SessionStatus.ACTIVE:
        raise InvalidSessionStateError(
            session_row.id, session_row.status, "transition_to_disconnected",
        )
    session_row.status = SessionStatus.DISCONNECTED
    session_row.disconnected_at = func.now()


async def transition_to_ended(
    session: AsyncSession,
    session_row: Session,
) -> None:
    """(connecting|active|disconnected) → ended.

    ALWAYS clears `connection_info` (S-D2). Sets `ended_at`. Also
    mutates the session's desktop:

    - assignment_type == 'floating': clears `assigned_user` and
      `assignment_type`, returns `desktop.status` to `available`.
    - assignment_type == 'persistent' (or None): desktop.status goes
      to `disconnected`; `assigned_user` is retained.

    ended → ended is rejected so a double-DELETE or a race between
    user-disconnect and admin-kill surfaces a loud error rather than
    silently double-clearing fields.
    """
    if session_row.status == SessionStatus.ENDED:
        raise InvalidSessionStateError(
            session_row.id, session_row.status, "transition_to_ended",
        )

    session_row.status = SessionStatus.ENDED
    session_row.ended_at = func.now()
    # sql_null() instead of Python None: SQLAlchemy's default JSONB
    # binding turns `None` into JSON literal `null` (a 3-character JSON
    # value), which makes `WHERE connection_info IS NULL` miss this row.
    # The model also declares JSONB(none_as_null=True) for defense in
    # depth — see docs/database-schema.md → Notes for the convention.
    session_row.connection_info = sql_null()

    # Mutate the desktop per its assignment_type. We fetch fresh rather
    # than trusting session_row.desktop (lazy="noload" per M2-03 — a
    # bare attribute access returns nothing useful).
    desktop = (
        await session.execute(
            select(Desktop).where(Desktop.id == session_row.desktop_id)
        )
    ).scalar_one()

    if desktop.assignment_type == "floating":
        desktop.assigned_user = None
        desktop.assignment_type = None
        desktop.status = DesktopStatus.AVAILABLE
    else:
        # Persistent assignment (or no assignment at all): user keeps
        # the desktop, but it's no longer connected.
        desktop.status = DesktopStatus.DISCONNECTED
