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


# Pool names double as the slug used in Proxmox tags
# (openvdi-pool-{name}). Proxmox's pve-tag-list format is [a-z0-9_-]+;
# constraining names at the schema layer means _slugify(pool.name) is a
# no-op and recovery-from-tags is lossless on the pool dimension.
# See docs/database-schema.md → VM Tagging Convention and the m2-15 prompt.
#
# The regex also rejects names starting or ending with '-' or '_': those
# are legal tag characters but would slugify lossily when edges are
# stripped, so we reject at input. Single-character names ("a", "1")
# are allowed via the second alternative.
POOL_NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]*[a-z0-9]$|^[a-z0-9]$"
POOL_NAME_DESCRIPTION = (
    "Lowercase letters, digits, hyphens, and underscores only. "
    "Cannot start or end with '-' or '_'. "
    "Used directly as a Proxmox tag fragment (openvdi-pool-{name})."
)


class PoolCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        ...,
        pattern=POOL_NAME_PATTERN,
        min_length=1,
        max_length=64,
        description=POOL_NAME_DESCRIPTION,
    )
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
    # Reserved for M3+ full-clone support. M2 supports linked clones
    # only — those inherit the template's storage and Proxmox rejects
    # any `storage` parameter on a linked-clone request. The validator
    # below rejects non-null values until full-clone support arrives.
    # Kept on the schema (rather than removed) so M3+ can flip the
    # validator without re-adding the field.
    target_storage: str | None = Field(
        default=None,
        description=(
            "Reserved for future full-clone support. M2 supports linked "
            "clones only, which inherit the template's storage. Setting "
            "this field is currently rejected with INVALID_REQUEST."
        ),
    )
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

    @model_validator(mode="after")
    def _reject_target_storage_until_full_clone_support(self) -> "PoolCreate":
        if self.target_storage is not None:
            raise ValueError(
                "target_storage is reserved for future full-clone support. "
                "M2 supports linked clones only, which inherit the "
                "template's storage. Omit this field."
            )
        return self


class PoolUpdate(BaseModel):
    """vmid_range_start, vmid_range_end, template_id, cluster_id, and
    pool_type are immutable post-creation — omitted here on purpose."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(
        default=None,
        pattern=POOL_NAME_PATTERN,
        min_length=1,
        max_length=64,
        description=POOL_NAME_DESCRIPTION,
    )
    display_name: str | None = None
    description: str | None = None
    min_spare: int | None = None
    max_size: int | None = None
    name_prefix: str | None = None
    target_nodes: str | None = None
    # Reserved — see PoolCreate.target_storage for the rationale. The
    # PUT path rejects non-null values too: an admin who tries to add
    # storage relocation on an existing pool runs into the same M2
    # constraint as on create.
    target_storage: str | None = None
    cpu_cores: int | None = None
    memory_mb: int | None = None
    pve_pool_id: str | None = None
    provider_config: dict[str, Any] | None = None
    auto_logoff_min: int | None = None
    delete_on_logoff: bool | None = None
    refresh_on_logoff: bool | None = None
    status: PoolStatus | None = None

    @model_validator(mode="after")
    def _reject_target_storage_until_full_clone_support(self) -> "PoolUpdate":
        if self.target_storage is not None:
            raise ValueError(
                "target_storage is reserved for future full-clone support. "
                "M2 supports linked clones only, which inherit the "
                "template's storage. Omit this field."
            )
        return self


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


class PoolCapacityDetail(BaseModel):
    """Per-status desktop counts + VMID range math, inlined into the
    pool detail view (GET /pools/{id}).

    Sister type `app.schemas.dashboard.PoolCapacityWithName` extends
    this with pool identity for the dashboard's per-pool breakdown.

    - `range_capacity` = `vmid_range_end - vmid_range_start + 1`
    - `total_desktops` counts ALL rows regardless of status, including
      `deleting` (slots aren't actually free until destroy completes).
    - `free_slots = range_capacity - total_desktops`.
    """

    range_capacity: int
    total_desktops: int
    free_slots: int

    provisioning: int = 0
    available: int = 0
    assigned: int = 0
    connected: int = 0
    disconnected: int = 0
    error: int = 0
    deleting: int = 0
    maintenance: int = 0


class PoolDeleteAccepted(BaseModel):
    """202 response for DELETE /pools/{id}."""

    pool_id: uuid.UUID
    message: str
    desktops_to_destroy: int


class ProvisionRequest(BaseModel):
    """Body for POST /pools/{id}/provision."""

    model_config = ConfigDict(extra="forbid")

    count: int = Field(..., ge=1, le=50)


class ProvisionAccepted(BaseModel):
    """202 response for POST /pools/{id}/provision."""

    pool_id: uuid.UUID
    count_requested: int
    message: str


class DrainAccepted(BaseModel):
    """202 response for POST /pools/{id}/drain."""

    pool_id: uuid.UUID
    message: str
    active_sessions: int = 0


# Inline import at the bottom avoids a circular at module load time:
# pool → desktop is fine (desktop doesn't import pool), and defining
# PoolReadDetailed after DesktopRead is available keeps the annotation
# resolvable without model_rebuild gymnastics.
from app.schemas.desktop import DesktopRead  # noqa: E402


class PoolReadDetailed(PoolRead):
    """Pool detail view: metadata + capacity + inline desktop list.

    The desktop list is NOT paginated — pool detail is a "cards view"
    in the admin UI, expected to show all desktops for one pool. Pool
    sizes are bounded by `max_size` (≤ VMID-range capacity) so this
    stays well under any reasonable per-request payload size.
    """

    capacity: PoolCapacityDetail
    desktops: list[DesktopRead] = []
