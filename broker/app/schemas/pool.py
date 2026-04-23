"""Pool request/response schemas.

Mirrors the DB CHECK constraints on vmid_range / max_size so invalid
inputs fail at 400 rather than bubbling IntegrityError up from Postgres.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.pool import PoolStatus, PoolType


class PoolCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    display_name: str
    description: str | None = None
    pool_type: PoolType
    template_id: uuid.UUID
    cluster_id: uuid.UUID

    min_spare: int = 1
    max_size: int = 10

    vmid_range_start: int
    vmid_range_end: int

    name_prefix: str
    target_nodes: str | None = None
    target_storage: str | None = None
    cpu_cores: int | None = None
    memory_mb: int | None = None
    pve_pool_id: str | None = None
    provider_config: dict[str, Any] = Field(default_factory=dict)

    auto_logoff_min: int = 0
    delete_on_logoff: bool = False
    refresh_on_logoff: bool = True

    @model_validator(mode="after")
    def _vmid_range_valid(self) -> "PoolCreate":
        if self.vmid_range_start >= self.vmid_range_end:
            raise ValueError(
                "vmid_range_start must be less than vmid_range_end"
            )
        capacity = self.vmid_range_end - self.vmid_range_start + 1
        if self.max_size > capacity:
            raise ValueError(
                f"max_size ({self.max_size}) exceeds vmid range capacity ({capacity})"
            )
        return self


class PoolUpdate(BaseModel):
    """vmid_range_start, vmid_range_end, template_id, cluster_id, and
    pool_type are immutable post-creation — omitted here on purpose."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    display_name: str | None = None
    description: str | None = None
    min_spare: int | None = None
    max_size: int | None = None
    name_prefix: str | None = None
    target_nodes: str | None = None
    target_storage: str | None = None
    cpu_cores: int | None = None
    memory_mb: int | None = None
    pve_pool_id: str | None = None
    provider_config: dict[str, Any] | None = None
    auto_logoff_min: int | None = None
    delete_on_logoff: bool | None = None
    refresh_on_logoff: bool | None = None
    status: PoolStatus | None = None


class PoolRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    display_name: str
    description: str | None
    pool_type: PoolType
    template_id: uuid.UUID
    cluster_id: uuid.UUID
    min_spare: int
    max_size: int
    vmid_range_start: int
    vmid_range_end: int
    name_prefix: str
    target_nodes: str | None
    target_storage: str | None
    cpu_cores: int | None
    memory_mb: int | None
    pve_pool_id: str | None
    provider_config: dict[str, Any]
    auto_logoff_min: int
    delete_on_logoff: bool
    refresh_on_logoff: bool
    status: PoolStatus
    created_at: datetime
    updated_at: datetime
