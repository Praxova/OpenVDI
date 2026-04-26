"""User-view response schemas for /me/* endpoints.

Narrower than admin schemas by design:

- VMID / node / cluster / template IDs are omitted (hypervisor internals,
  no user value).
- `pve_task_upid` / `pve_task_kind` / `error_message` are omitted (M2-13
  invariant + admin-only operational detail).
- `assigned_user` on a desktop is omitted — the user already knows a
  desktop they're looking at is theirs, and never seeing other users'
  names here removes a whole class of future "show all assignments"
  mistakes that would leak identity across a shared pool.
- Session fields like `connection_info`, `vm_ip_address`, `os_user`,
  `client_ip` are omitted — those are telemetry for operators, never
  surfaced to users.

Principle: the user view is "what do I need to connect and know where
I stand?". Everything else is admin surface.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.desktop import DesktopStatus
from app.models.pool import PoolStatus, PoolType
from app.models.session import SessionStatus


class UserDesktopView(BaseModel):
    """Minimal desktop view for /me endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    status: DesktopStatus
    power_state: str
    last_connected: datetime | None


class UserPoolView(BaseModel):
    """Pool view surfaced via GET /me/desktops.

    `assigned_desktop` is populated if the user currently holds a
    persistent assignment in this pool, or an active floating one
    (from an in-flight session). Users without any assignment see
    `None` — that's the normal state before a first connect or after
    a non-persistent session ends.
    """

    id: uuid.UUID
    name: str
    display_name: str
    description: str | None = None
    pool_type: PoolType
    status: PoolStatus
    assigned_desktop: UserDesktopView | None = None


class UserSessionView(BaseModel):
    """Session view surfaced via GET /me/sessions.

    `desktop_name` and `pool_name` are denormalized into the row so a
    caller doesn't need to cross-reference three endpoints to render
    a session list.

    The desktop/pool fields are nullable: per M2-15-fix-2 the FK on
    `sessions.desktop_id` is `ON DELETE SET NULL`, so an orphaned
    session (desktop has been destroyed) surfaces with these fields
    set to None. The session-side fields (`protocol`, timestamps,
    `status`) survive the destroy and are always populated.
    """

    id: uuid.UUID
    desktop_id: uuid.UUID | None
    desktop_name: str | None
    pool_id: uuid.UUID | None
    pool_name: str | None
    protocol: str
    status: SessionStatus
    connected_at: datetime | None
    disconnected_at: datetime | None
    ended_at: datetime | None
