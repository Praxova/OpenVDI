"""Session request/response schemas.

`connection_info` is the raw broker-issued VNC ticket (websocket URL +
password). It NEVER appears in any wire schema — admin or user. It's
cleared to NULL on session end (S-D2) and only the in-process broker
service touches it on the ORM model directly. M2-17 made the schema-
level exclusion explicit; if a future feature needs admin ticket replay
(e.g. session shadowing), that's a new endpoint with its own threat
model — not a field to unlock here.

Admin views:
  SessionReadAdmin       — list-view payload (compact)
  SessionReadDetailed    — detail-view payload + guest-agent telemetry

Read more in `app/api/sessions.py` and the M2-17 prompt.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.pool import PoolType
from app.models.session import SessionStatus


class SessionCreate(BaseModel):
    """Not used directly by admins; sessions are created by the broker
    during the connect flow. Included for symmetry."""

    model_config = ConfigDict(extra="forbid")

    desktop_id: uuid.UUID
    username: str
    protocol: str
    client_ip: str | None = None


class SessionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: SessionStatus | None = None
    connected_at: datetime | None = None
    disconnected_at: datetime | None = None
    ended_at: datetime | None = None
    # connection_info is broker-internal; not editable via PUT.
    os_user: str | None = None
    os_info: dict[str, Any] | None = None
    vm_ip_address: str | None = None
    last_heartbeat: datetime | None = None
    idle_since: datetime | None = None


class SessionRead(BaseModel):
    """Generic session view — used inline as `active_session` on the
    admin desktop detail. Deliberately omits `connection_info`.

    `desktop_id` is nullable per M2-15-fix-2 — sessions persist as
    orphans (desktop_id=NULL) when the parent desktop is destroyed.
    For live sessions returned via the desktop-detail endpoint the
    field is always populated; the nullability is for forward shape
    compatibility.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    desktop_id: uuid.UUID | None
    username: str
    protocol: str
    client_ip: str | None
    status: SessionStatus
    connected_at: datetime | None
    disconnected_at: datetime | None
    ended_at: datetime | None
    os_user: str | None
    os_info: dict[str, Any] | None
    vm_ip_address: str | None
    last_heartbeat: datetime | None
    idle_since: datetime | None
    created_at: datetime


class SessionReadAdmin(BaseModel):
    """Compact admin list-view row. Joins desktop + pool identity inline
    so a session list renders without follow-up requests.

    Payload-shrinking: omits `os_user`/`os_info`/`vm_ip_address`/
    `idle_since` — the detail endpoint carries those.

    The desktop/pool fields are nullable: per M2-15-fix-2 a destroyed
    desktop's session rows survive with `desktop_id=NULL`, and
    `/sessions` uses LEFT OUTER joins so orphaned sessions surface
    with the joined-table fields rendered as None. Session-side
    fields (timestamps, status, protocol, client_ip, username) always
    populate.
    """

    id: uuid.UUID
    desktop_id: uuid.UUID | None
    desktop_name: str | None
    pool_id: uuid.UUID | None
    pool_name: str | None
    pool_type: PoolType | None
    username: str
    protocol: str
    client_ip: str | None
    status: SessionStatus
    connected_at: datetime | None
    disconnected_at: datetime | None
    ended_at: datetime | None
    last_heartbeat: datetime | None


class SessionReadDetailed(SessionReadAdmin):
    """Detail view — admin list fields + guest-agent telemetry.

    Guest-agent fields (`os_user`, `os_info`, `vm_ip_address`,
    `idle_since`) are populated by M4's session monitor; in M2 they're
    null on every row but the wire shape is forward-compatible.
    """

    os_user: str | None
    os_info: dict[str, Any] | None
    vm_ip_address: str | None
    idle_since: datetime | None
