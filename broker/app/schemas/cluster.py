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
