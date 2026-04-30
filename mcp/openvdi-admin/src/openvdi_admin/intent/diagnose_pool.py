"""openvdi_diagnose_pool intent tool — pool health snapshot.

Aggregates pool config, capacity counts, desktop-level issues
(error / stuck-provisioning / orphan tasks), active session count,
recent audit events, and cluster status into a single structured
result with a `health` rollup ('healthy' / 'degraded' / 'unhealthy')
and an `issues` list with `suggested_action` hints.

Read-only. No writable gate.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from openvdi_admin.errors import BrokerError
from openvdi_admin.intent._result import StepTracker
from openvdi_admin._tool_wrapper import register_tool
from openvdi_admin.tools.audit import openvdi_query_audit
from openvdi_admin.tools.clusters import openvdi_get_cluster
from openvdi_admin.tools.desktops import openvdi_list_desktops
from openvdi_admin.tools.pools import (
    openvdi_get_pool,
    openvdi_get_pool_summary,
)
from openvdi_admin.tools.sessions import openvdi_list_sessions


logger = logging.getLogger(__name__)


# Heuristic threshold — clones on slow storage routinely take 3-7
# minutes; 10 is the round-up that doesn't false-positive.
_STUCK_PROVISIONING_THRESHOLD_MINUTES = 10
_AUDIT_WINDOW_HOURS = 1
_AUDIT_LIMIT = 50


@register_tool()
async def openvdi_diagnose_pool(pool_id: str) -> dict[str, Any]:
    """Comprehensive health snapshot for a single pool.

    Reports:
      - pool config + current state
      - capacity summary (per get_pool_summary)
      - error desktops with their error_message
      - stuck-provisioning desktops (in 'provisioning' for >10 min)
      - orphan in-flight tasks (pve_task_upid older than 10 min,
        proxied via desktop.updated_at)
      - active session count
      - recent audit events affecting this pool (last hour, max 50)
      - cluster status (pool depends on cluster being reachable)
      - rolled-up `health`: 'healthy' / 'degraded' / 'unhealthy'
      - `issues` list with severity + suggested_action per issue

    Args:
        pool_id: UUID of the pool.

    Returns:
        IntentResult-shaped dict.
    """
    tracker = StepTracker()

    try:
        async with tracker.step("fetch_pool") as step:
            pool = await openvdi_get_pool(pool_id)
            step["details"] = {
                "pool_name": pool.get("name"),
                "status": pool.get("status"),
            }

        async with tracker.step("fetch_pool_summary") as step:
            summary = await openvdi_get_pool_summary(pool_id)
            step["details"] = summary.get("capacity", {})

        async with tracker.step("fetch_desktops") as step:
            desktops = await openvdi_list_desktops(
                pool_id=pool_id, limit=200,
            )
            step["details"] = {"count": len(desktops)}

        async with tracker.step("identify_desktop_issues") as step:
            error_desktops = [
                {
                    "id": d["id"],
                    "name": d.get("name"),
                    "error_message": d.get("error_message"),
                }
                for d in desktops
                if d.get("status") == "error"
            ]
            stuck_provisioning = _identify_stuck_provisioning(desktops)
            orphan_tasks = _identify_orphan_tasks(desktops)
            step["details"] = {
                "error": len(error_desktops),
                "stuck": len(stuck_provisioning),
                "orphan_tasks": len(orphan_tasks),
            }

        async with tracker.step("count_active_sessions") as step:
            active_sessions = await openvdi_list_sessions(
                pool_id=pool_id, status="active", limit=200,
            )
            step["details"] = {"count": len(active_sessions)}

        async with tracker.step("fetch_recent_audit") as step:
            since = (
                datetime.now(timezone.utc)
                - timedelta(hours=_AUDIT_WINDOW_HOURS)
            ).isoformat().replace("+00:00", "Z")
            audit_events = await openvdi_query_audit(
                resource_type="pool",
                resource_id=pool_id,
                since=since,
                limit=_AUDIT_LIMIT,
            )
            step["details"] = {"event_count": len(audit_events)}

        async with tracker.step("fetch_cluster") as step:
            cluster = await openvdi_get_cluster(pool["cluster_id"])
            step["details"] = {
                "cluster_name": cluster.get("name"),
                "cluster_status": cluster.get("status"),
            }

        issues = _build_issues_list(
            pool=pool,
            summary=summary,
            error_desktops=error_desktops,
            stuck=stuck_provisioning,
            orphans=orphan_tasks,
            cluster=cluster,
        )
        health = _compute_health(pool, issues, cluster)

        return tracker.success_result(
            operation="diagnose_pool",
            result={
                "pool_id": pool_id,
                "name": pool.get("name"),
                "display_name": pool.get("display_name"),
                "health": health,
                "pool_status": pool.get("status"),
                "pool_type": pool.get("pool_type"),
                "capacity": summary.get("capacity", {}),
                "min_spare": pool.get("min_spare"),
                "max_size": pool.get("max_size"),
                "issues": issues,
                "error_desktops": error_desktops,
                "stuck_provisioning": stuck_provisioning,
                "orphan_tasks": orphan_tasks,
                "active_session_count": len(active_sessions),
                "recent_audit_events": audit_events,
                "cluster": {
                    "id": cluster["id"],
                    "name": cluster.get("name"),
                    "status": cluster.get("status"),
                },
            },
        )

    except BrokerError as exc:
        return tracker.failure_result(
            operation="diagnose_pool",
            error=exc,
            failed_at_step=tracker.last_failed_step(),
        )


def _parse_iso8601(value: str) -> datetime | None:
    """Parse a broker timestamp ('2026-04-30T10:00:00Z' or
    '+00:00'-suffix). Returns None on malformed input so the caller
    can skip the row."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _identify_stuck_provisioning(
    desktops: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return desktops in 'provisioning' status with `created_at`
    older than the threshold.

    Why `created_at`, not `provisioned_at`: the latter is set when
    provisioning COMPLETES, so a stuck-in-provisioning row has it
    null. `created_at` is set when the row is inserted at the start
    of provisioning — a stable proxy for "how long has this been
    in flight."
    """
    threshold = datetime.now(timezone.utc) - timedelta(
        minutes=_STUCK_PROVISIONING_THRESHOLD_MINUTES,
    )
    stuck: list[dict[str, Any]] = []
    for d in desktops:
        if d.get("status") != "provisioning":
            continue
        created_at = _parse_iso8601(d.get("created_at", ""))
        if created_at is None:
            continue
        if created_at < threshold:
            stuck.append({
                "id": d["id"],
                "name": d.get("name"),
                "created_at": d.get("created_at"),
                "minutes_in_provisioning": int(
                    (
                        datetime.now(timezone.utc) - created_at
                    ).total_seconds()
                    / 60,
                ),
            })
    return stuck


def _identify_orphan_tasks(
    desktops: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return desktops whose pve_task_upid hasn't been touched in
    a while.

    NB: `pve_task_upid` and `pve_task_kind` are intentionally
    omitted from DesktopRead per the M2-13 invariant, so this
    detection is a no-op against the current broker. Kept in place
    so a future broker that relaxes the invariant gets orphan
    detection automatically; for now `_identify_orphan_tasks`
    returns []. M6+ candidate: a dedicated admin endpoint that
    surfaces task state.
    """
    threshold = datetime.now(timezone.utc) - timedelta(
        minutes=_STUCK_PROVISIONING_THRESHOLD_MINUTES,
    )
    orphans: list[dict[str, Any]] = []
    for d in desktops:
        upid = d.get("pve_task_upid")
        if not upid:
            continue
        updated_at = _parse_iso8601(d.get("updated_at", ""))
        if updated_at is None:
            continue
        if updated_at < threshold:
            orphans.append({
                "desktop_id": d["id"],
                "desktop_name": d.get("name"),
                "task_upid": upid,
                "minutes_since_update": int(
                    (
                        datetime.now(timezone.utc) - updated_at
                    ).total_seconds()
                    / 60,
                ),
            })
    return orphans


def _build_issues_list(
    *,
    pool: dict[str, Any],
    summary: dict[str, Any],
    error_desktops: list[dict[str, Any]],
    stuck: list[dict[str, Any]],
    orphans: list[dict[str, Any]],
    cluster: dict[str, Any],
) -> list[dict[str, str]]:
    """Build the flat issues array. Each entry has:
      severity: 'error' | 'warning'
      description: human-readable
      suggested_action: tool-call hint (string, NOT auto-callable)
    """
    issues: list[dict[str, str]] = []

    if cluster.get("status") != "active":
        issues.append({
            "severity": "error",
            "description": (
                f"cluster {cluster.get('name')!r} is "
                f"{cluster.get('status')!r}, not 'active'"
            ),
            "suggested_action": (
                f"openvdi_get_cluster({cluster['id']!r})"
            ),
        })

    pool_status = pool.get("status")
    if pool_status == "error":
        issues.append({
            "severity": "error",
            "description": "pool status is 'error'",
            "suggested_action": (
                f"openvdi_query_audit(resource_type='pool', "
                f"resource_id={pool['id']!r}, limit=20)"
            ),
        })
    elif pool_status == "draining":
        issues.append({
            "severity": "warning",
            "description": (
                "pool is draining; new connections rejected"
            ),
            "suggested_action": (
                "wait for active sessions to end, then "
                f"openvdi_delete_pool({pool['id']!r}, confirm=True)"
            ),
        })

    if error_desktops:
        n = len(error_desktops)
        first_name = error_desktops[0].get("name", "unknown")
        issues.append({
            "severity": "error",
            "description": (
                f"{n} desktop{'s' if n != 1 else ''} in error "
                f"state (e.g. {first_name})"
            ),
            "suggested_action": (
                f"openvdi_get_desktop({error_desktops[0]['id']!r})"
            ),
        })

    capacity = summary.get("capacity") or {}
    available = capacity.get("available", 0)
    min_spare = pool.get("min_spare", 0)
    if (
        pool.get("pool_type") == "nonpersistent"
        and available < min_spare
    ):
        deficit = min_spare - available
        issues.append({
            "severity": "warning",
            "description": (
                f"available ({available}) below min_spare "
                f"({min_spare})"
            ),
            "suggested_action": (
                f"openvdi_provision_pool({pool['id']!r}, "
                f"count={deficit}, confirm=True)"
            ),
        })

    if stuck:
        n = len(stuck)
        issues.append({
            "severity": "warning",
            "description": (
                f"{n} desktop{'s' if n != 1 else ''} stuck in "
                "provisioning > 10 min"
            ),
            "suggested_action": (
                f"openvdi_get_desktop({stuck[0]['id']!r}) — "
                "inspect; may need delete + reprovision"
            ),
        })

    if orphans:
        n = len(orphans)
        issues.append({
            "severity": "warning",
            "description": (
                f"{n} orphan task{'s' if n != 1 else ''} "
                "(pve_task_upid set, no recent update)"
            ),
            "suggested_action": (
                "check broker task_tracker worker logs"
            ),
        })

    return issues


def _compute_health(
    pool: dict[str, Any],
    issues: list[dict[str, str]],
    cluster: dict[str, Any],
) -> str:
    """Roll up to 'healthy', 'degraded', or 'unhealthy'."""
    if any(i["severity"] == "error" for i in issues):
        return "unhealthy"
    if any(i["severity"] == "warning" for i in issues):
        return "degraded"
    if cluster.get("status") != "active":
        return "unhealthy"
    return "healthy"
