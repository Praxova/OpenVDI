"""Cluster request/response schemas.

token_secret is SecretStr on every Create/Update surface; ClusterRead
deliberately omits it so no endpoint accidentally echoes it back.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


# Providers supported in M2. Widening the Literal is a one-line change
# when a second provider lands.
ProviderType = Literal["proxmox"]


class ClusterCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    provider_type: ProviderType = "proxmox"
    api_url: str
    token_id: str
    token_secret: SecretStr
    verify_ssl: bool = True
    node_filter: str | None = None
    provider_config: dict[str, Any] = Field(default_factory=dict)


class ClusterUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    provider_type: ProviderType | None = None
    api_url: str | None = None
    token_id: str | None = None
    token_secret: SecretStr | None = None  # None = keep existing
    verify_ssl: bool | None = None
    node_filter: str | None = None
    provider_config: dict[str, Any] | None = None


class ClusterRead(BaseModel):
    """Never echoes token_secret."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    provider_type: str
    api_url: str
    token_id: str
    verify_ssl: bool
    node_filter: str | None
    provider_config: dict[str, Any]
    status: str
    created_at: datetime
    updated_at: datetime


class NodeInfoRead(BaseModel):
    """Mirror of providers.base.NodeInfo for the wire.

    Pydantic can't consume frozen dataclasses via `from_attributes` when
    the dataclass holds non-standard types (frozenset) so the schema is
    hand-written. Fields here must match NodeInfo 1:1.
    """

    model_config = ConfigDict(from_attributes=True)

    node: str
    display_name: str
    status: Literal["online", "offline", "maintenance"]
    cpu_cores: int
    memory_bytes: int


class ClusterReadWithNodes(ClusterRead):
    """Cluster row + live node snapshot from the provider.

    `nodes` may be empty if the cluster has no active provider entry in
    `app.state.providers` (e.g. offline at startup, in maintenance) or
    if `list_nodes` raised. Either way the cluster row itself is still
    returned — admins shouldn't be locked out of reading the row just
    because the hypervisor is unreachable.
    """

    nodes: list[NodeInfoRead] = []
