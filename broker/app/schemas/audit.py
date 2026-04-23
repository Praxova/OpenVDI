"""Audit log response schema. Append-only — no Create / Update."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AuditRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: datetime
    actor: str | None
    action: str
    resource_type: str | None
    resource_id: uuid.UUID | None
    details: dict[str, Any] | None
    client_ip: str | None
