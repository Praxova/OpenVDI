"""Entitlement request/response schemas."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


PrincipalType = Literal["user", "group"]


class EntitlementCreate(BaseModel):
    """POST body for /pools/{pool_id}/entitlements.

    `pool_id` is the path parameter, so it deliberately is NOT part of
    the request body — removing duplication between URL and body.
    """

    model_config = ConfigDict(extra="forbid")

    principal_type: PrincipalType
    principal_name: str


class EntitlementUpdate(BaseModel):
    """pool_id is immutable post-creation. Re-entitling a principal to a
    different pool means deleting and re-creating."""

    model_config = ConfigDict(extra="forbid")

    principal_type: PrincipalType | None = None
    principal_name: str | None = None


class EntitlementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    pool_id: uuid.UUID
    principal_type: str
    principal_name: str
    created_at: datetime
