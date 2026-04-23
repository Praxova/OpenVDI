"""Aggregate response shapes for /dashboard/* endpoints."""
from __future__ import annotations

import uuid

from pydantic import BaseModel


class DashboardSummary(BaseModel):
    total_clusters: int
    total_pools: int
    total_desktops: int
    active_sessions: int
    pool_capacity_total: int
    pool_capacity_used: int


class PoolCapacity(BaseModel):
    pool_id: uuid.UUID
    name: str
    max_size: int
    total: int
    available: int
    assigned: int
    connected: int


class CapacityResponse(BaseModel):
    pools: list[PoolCapacity]
