"""Dashboard admin tools.

Two pure-read tools that aggregate broker-wide state. Both are used
by M5-07's diagnosis intent tools (openvdi_health_check) and any
agent answering general questions about deployment scale.
"""
from __future__ import annotations

from typing import Any

from openvdi_admin._tool_wrapper import register_tool
from openvdi_admin.tools._common import get_broker_client


@register_tool()
async def openvdi_get_dashboard_summary() -> dict[str, Any]:
    """Aggregate deployment stats: cluster / pool / desktop /
    session totals plus capacity utilization across the broker.

    No filters. Returns a single dict matching the broker's
    GET /dashboard/summary response (DashboardSummary).
    """
    client = get_broker_client()
    return await client.get("/api/v1/dashboard/summary")


@register_tool()
async def openvdi_get_dashboard_capacity() -> list[dict[str, Any]]:
    """Per-pool capacity breakdown: total / available / assigned /
    connected / provisioning / error counts plus VMID-range math
    for each pool.

    Returns:
        List of per-pool capacity dicts, ordered by display_name.
    """
    client = get_broker_client()
    return await client.get("/api/v1/dashboard/capacity")
