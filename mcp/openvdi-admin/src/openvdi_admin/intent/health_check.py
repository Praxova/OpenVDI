"""openvdi_health_check intent tool — broker + cluster liveness.

The agent-session-start tool. Light, fast, structured. Three things:
  1. Broker reachable (GET /health, raw payload — see below).
  2. Each cluster's status (openvdi_list_clusters).
  3. Aggregate one-line summary string.

Why `client.get_raw("/health")` and not `client.get`: the broker's
`/health` endpoint deliberately returns the plain payload, NOT the
{data, error} envelope (M4-12). The default `BrokerClient.get`
calls `unwrap_envelope`, which would raise on the non-envelope
shape. `get_raw` skips the unwrap step for endpoints that
intentionally bypass the envelope contract.

Reaches into `tools._common.get_broker_client()` directly because
there's no thin wrapper for `/health` (it's not a normal admin
endpoint). This is the one exception to T4 in the entire intent
layer; a single-line wrapper would carry no value.

Read-only. No writable gate.
"""
from __future__ import annotations

import logging
from typing import Any

from openvdi_admin.errors import BrokerError
from openvdi_admin.intent._result import StepTracker
from openvdi_admin._tool_wrapper import register_tool
from openvdi_admin.tools._common import get_broker_client
from openvdi_admin.tools.clusters import openvdi_list_clusters


logger = logging.getLogger(__name__)


@register_tool()
async def openvdi_health_check() -> dict[str, Any]:
    """Single tool the agent invokes at session start to verify the
    broker is alive and clusters are reachable.

    A broker that is reachable but has all clusters in 'offline'
    state still returns ok=true with broker_reachable=true and a
    summary that flags the cluster issue. The MCP itself is
    functioning; cluster connectivity is a deeper layer the agent
    should diagnose separately.

    Returns:
        IntentResult-shaped dict with broker_reachable,
        broker_version, per-cluster statuses, and a summary string.
    """
    tracker = StepTracker()
    client = get_broker_client()

    broker_reachable = False
    broker_version: str | None = None
    clusters: list[dict[str, Any]] = []

    try:
        async with tracker.step("ping_broker") as step:
            health = await client.get_raw("/health")
            broker_reachable = True
            if isinstance(health, dict):
                broker_version = health.get("version")
            step["details"] = {
                "reachable": True,
                "version": broker_version,
            }

        async with tracker.step("list_clusters") as step:
            for c in await openvdi_list_clusters():
                clusters.append({
                    "id": c["id"],
                    "name": c.get("name"),
                    "status": c.get("status"),
                })
            step["details"] = {
                "total": len(clusters),
                "active": sum(
                    1 for c in clusters if c["status"] == "active"
                ),
            }

        active_count = sum(
            1 for c in clusters if c["status"] == "active"
        )
        total_count = len(clusters)
        if total_count == 0:
            summary = "broker reachable; no clusters registered"
        elif active_count == total_count:
            plural = "s" if total_count != 1 else ""
            summary = (
                f"broker reachable; all {total_count} "
                f"cluster{plural} active"
            )
        else:
            summary = (
                f"broker reachable; {active_count} of "
                f"{total_count} clusters active"
            )

        return tracker.success_result(
            operation="health_check",
            result={
                "broker_reachable": broker_reachable,
                "broker_version": broker_version,
                "clusters": clusters,
                "summary": summary,
            },
        )

    except BrokerError as exc:
        return tracker.failure_result(
            operation="health_check",
            error=exc,
            failed_at_step=tracker.last_failed_step(),
        )
