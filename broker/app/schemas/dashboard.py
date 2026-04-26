"""Aggregate response shapes for /dashboard/* endpoints (M2-17).

Two endpoints:
  GET /dashboard/summary    → DashboardSummary (totals across all resources)
  GET /dashboard/capacity   → list[PoolCapacityWithName] (per-pool breakdown)

`PoolCapacityWithName` extends `PoolCapacityDetail` (from schemas/pool.py)
with pool identity. Reusing the M2-15 capacity shape keeps capacity math
in one place — see the N+1 disclaimer in api/dashboard.py.
"""
from __future__ import annotations

import uuid

from pydantic import BaseModel

from app.models.pool import PoolStatus, PoolType
from app.schemas.pool import PoolCapacityDetail


class ResourceStatusCounts(BaseModel):
    """Generic shape for "total + breakdown by status" rollups."""

    total: int
    by_status: dict[str, int]


class PoolSummaryCounts(BaseModel):
    """Pools have a useful pool_type axis on top of status."""

    total: int
    by_status: dict[str, int]
    by_type: dict[str, int]   # "persistent" / "nonpersistent"


class SessionSummaryCounts(BaseModel):
    """Sessions get explicit fields per status because the dashboard
    cares about three transitional buckets distinctly (active vs
    connecting vs disconnected) rather than treating them as a generic
    by_status map."""

    total: int
    active: int
    connecting: int
    disconnected: int
    ended: int


class CapacitySummary(BaseModel):
    """Cluster-wide capacity rollup. `total_vmid_slots` is the sum of
    per-pool VMID-range widths (including drained/deleting pools, since
    those slots are still booked); `total_desktops` is rows currently
    instantiated."""

    total_vmid_slots: int
    total_desktops: int


class DashboardSummary(BaseModel):
    """Top-of-page admin landing page payload."""

    clusters: ResourceStatusCounts
    pools: PoolSummaryCounts
    desktops: ResourceStatusCounts
    sessions: SessionSummaryCounts
    capacity: CapacitySummary


class PoolCapacityWithName(PoolCapacityDetail):
    """Per-pool capacity row + pool identity. Returned as a list by
    /dashboard/capacity; admin UI sorts client-side."""

    pool_id: uuid.UUID
    pool_name: str
    pool_display_name: str
    pool_status: PoolStatus
    pool_type: PoolType
