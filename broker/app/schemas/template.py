"""Template request/response schemas."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TemplateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cluster_id: uuid.UUID
    name: str
    pve_vmid: int
    pve_node: str
    os_type: str  # plain str for M2 — no enum yet
    description: str | None = None
    cpu_cores: int = 2
    memory_mb: int = 4096
    disk_gb: int = 60
    gpu_required: bool = False
    tags: list[Any] = Field(default_factory=list)
    provider_config: dict[str, Any] = Field(default_factory=dict)


class TemplateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # cluster_id and pve_vmid are the (cluster, vmid) uniqueness pair —
    # immutable post-creation.
    name: str | None = None
    pve_node: str | None = None
    os_type: str | None = None
    description: str | None = None
    cpu_cores: int | None = None
    memory_mb: int | None = None
    disk_gb: int | None = None
    gpu_required: bool | None = None
    tags: list[Any] | None = None
    provider_config: dict[str, Any] | None = None


class TemplateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    cluster_id: uuid.UUID
    name: str
    pve_vmid: int
    pve_node: str
    os_type: str
    description: str | None
    cpu_cores: int
    memory_mb: int
    disk_gb: int
    gpu_required: bool
    tags: list[Any]
    provider_config: dict[str, Any]
    status: str
    created_at: datetime
    updated_at: datetime


class ValidationCheck(BaseModel):
    """Single boolean check surfaced by POST /templates/{id}/validate."""

    name: str
    passed: bool
    message: str


class TemplateValidationResult(BaseModel):
    """Aggregate result of a template validation run."""

    template_id: uuid.UUID
    passed: bool
    checks: list[ValidationCheck]
