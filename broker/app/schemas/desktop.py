"""Desktop request/response schemas + list filter params."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.desktop import DesktopStatus
from app.schemas.common import PaginationParams


class DesktopCreate(BaseModel):
    """Not used by any HTTP endpoint — the provisioner creates desktops.
    Included for symmetry with the Create/Update/Read trio."""

    model_config = ConfigDict(extra="forbid")

    pool_id: uuid.UUID
    pve_vmid: int
    pve_node: str
    name: str


class DesktopUpdate(BaseModel):
    """Admin-facing desktop edits. `pve_task_upid` / `pve_task_kind`
    are internal broker state (managed by the provisioner + task
    tracker) and deliberately NOT exposed here — the M2-13 invariant
    covers write paths too, not just reads.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    assigned_user: str | None = None
    assignment_type: str | None = None
    status: DesktopStatus | None = None
    power_state: str | None = None
    spice_enabled: bool | None = None
    error_message: str | None = None


class DesktopRead(BaseModel):
    """Never exposes `pve_task_upid` / `pve_task_kind` — those are
    internal broker state (the in-flight async provider task). The
    M2-13 invariant bars them from every response; operators poll
    `status` / `error_message` for task progress.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    pool_id: uuid.UUID
    pve_vmid: int
    pve_node: str
    name: str
    assigned_user: str | None
    assignment_type: str | None
    status: DesktopStatus
    power_state: str
    last_connected: datetime | None
    last_disconnected: datetime | None
    provisioned_at: datetime | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class DesktopListParams(PaginationParams):
    pool_id: uuid.UUID | None = None
    status: DesktopStatus | None = None
    assigned_user: str | None = None


class DesktopAssignRequest(BaseModel):
    """Body for POST /desktops/{id}/assign.

    `username` is opaque — no AD/LDAP validation at this layer per W-8.
    """

    model_config = ConfigDict(extra="forbid")

    username: str


class TaskAccepted(BaseModel):
    """202 response for any desktop-level async action (power, rebuild,
    destroy). Operators poll `GET /desktops/{id}` for progress."""

    desktop_id: uuid.UUID
    action: str
    message: str


# Deferred import to avoid circular at module load time; session.py
# doesn't import from this module, so the other direction is safe.
from app.schemas.session import SessionRead  # noqa: E402


class DesktopReadDetailed(DesktopRead):
    """Desktop detail view: row + (opportunistic) live power state + the
    active session if any.

    `live_power_state` may differ from `power_state` if the provider
    reports a transition in progress; the endpoint opportunistically
    reconciles the row back to match when they differ.
    """

    active_session: SessionRead | None = None
    live_power_state: str
