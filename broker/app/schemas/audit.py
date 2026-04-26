"""Audit log response schema. Append-only — no Create / Update."""
from __future__ import annotations

import ipaddress
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


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

    @field_validator("client_ip", mode="before")
    @classmethod
    def _coerce_ip(cls, v: Any) -> Any:
        # asyncpg hands INET back as ipaddress.IPv4Address / IPv6Address.
        # Pydantic expects str, so coerce at the boundary.
        if isinstance(v, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
            return str(v)
        return v
