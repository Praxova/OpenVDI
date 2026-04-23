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
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    assigned_user: str | None = None
    assignment_type: str | None = None
    status: DesktopStatus | None = None
    power_state: str | None = None
    spice_enabled: bool | None = None
    error_message: str | None = None
    pve_task_upid: str | None = None


class DesktopRead(BaseModel):
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
    pve_task_upid: str | None
    created_at: datetime
    updated_at: datetime


class DesktopListParams(PaginationParams):
    pool_id: uuid.UUID | None = None
    status: DesktopStatus | None = None
    assigned_user: str | None = None
