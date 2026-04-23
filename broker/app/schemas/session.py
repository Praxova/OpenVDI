"""Session request/response schemas.

connection_info is always included in SessionRead. The API layer strips
it for non-admin callers per API-3-b; keeping the schema role-unaware
is deliberate.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

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
    connection_info: dict[str, Any] | None = None
    os_user: str | None = None
    os_info: dict[str, Any] | None = None
    vm_ip_address: str | None = None
    last_heartbeat: datetime | None = None
    idle_since: datetime | None = None


class SessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    desktop_id: uuid.UUID
    username: str
    protocol: str
    client_ip: str | None
    status: SessionStatus
    connected_at: datetime | None
    disconnected_at: datetime | None
    ended_at: datetime | None
    connection_info: dict[str, Any] | None
    os_user: str | None
    os_info: dict[str, Any] | None
    vm_ip_address: str | None
    last_heartbeat: datetime | None
    idle_since: datetime | None
    created_at: datetime
