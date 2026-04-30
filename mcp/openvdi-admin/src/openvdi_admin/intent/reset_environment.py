"""openvdi_reset_test_environment intent tool — nuclear cleanup.

Drains, force-disconnects, and deletes every pool whose name
starts with name_prefix. Optionally cascades to templates and
clusters whose pool reference set goes empty after deletion.

Defaults are conservative: name_prefix='test-', and templates +
clusters are KEPT (most operators want pools wiped without
rebuilding templates from Proxmox; templates take real Proxmox
setup and clusters even more so). Pass keep_templates=False /
keep_clusters=False explicitly to opt into the cascade.

Per the broker reality (M5-04): drain is one-way (pool stays in
'draining' indefinitely); the active sessions that don't end
naturally during drain get force-disconnected before the
delete_pool call. force_disconnect_session is fully synchronous on
the broker side (broker.end_session commits before returning), so
no settle-pause is needed before delete.
"""
from __future__ import annotations

import logging
from typing import Any

from openvdi_admin.errors import BrokerError
from openvdi_admin.intent._result import StepTracker
from openvdi_admin._tool_wrapper import register_tool
from openvdi_admin.tools._common import require_writable
from openvdi_admin.tools.clusters import (
    openvdi_delete_cluster,
    openvdi_list_clusters,
)
from openvdi_admin.tools.entitlements import openvdi_list_entitlements
from openvdi_admin.tools.pools import (
    openvdi_delete_pool,
    openvdi_drain_pool,
    openvdi_get_pool,
    openvdi_list_pools,
)
from openvdi_admin.tools.sessions import (
    openvdi_force_disconnect_session,
    openvdi_list_sessions,
)
from openvdi_admin.tools.templates import (
    openvdi_list_templates,
    openvdi_retire_template,
)


logger = logging.getLogger(__name__)


# Pool-name prefixes too dangerous to accept. Reset matches by
# prefix; an empty or wildcard prefix would match every pool.
_FORBIDDEN_PREFIXES = frozenset({"", "*"})


@register_tool()
async def openvdi_reset_test_environment(
    name_prefix: str = "test-",
    keep_clusters: bool = True,
    keep_templates: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Drain → disconnect → delete every pool whose name starts
    with name_prefix. Optionally cascade to templates / clusters.

    Safety guards:
      - name_prefix must be non-empty after stripping whitespace
        and must not equal '*'.
      - confirm=False (default) returns dry-run preview.
      - templates / clusters are touched only when keep_*=False AND
        they have no remaining pool references after pool deletion.

    Args:
        name_prefix: Match prefix for pool names. Default 'test-'.
        keep_clusters: If False, delete clusters with no remaining
            pool refs after pool cleanup. Default True.
        keep_templates: If False, retire templates with no remaining
            pool refs. Default True.
        confirm: True to execute. False (default) returns dry-run.

    Returns:
        IntentResult-shaped dict. On success, summary of pool /
        template / cluster counts cleaned.
    """
    require_writable("openvdi_reset_test_environment")

    stripped = name_prefix.strip()
    if stripped in _FORBIDDEN_PREFIXES or not stripped:
        raise BrokerError(
            http_status=400,
            code="INVALID_REQUEST",
            message=(
                f"name_prefix must be non-empty and specific; "
                f"got {name_prefix!r}"
            ),
        )

    matching_pools = [
        p for p in await openvdi_list_pools(limit=200)
        if p["name"].startswith(name_prefix)
    ]

    if not confirm:
        return await _build_dry_run_preview(
            matching_pools=matching_pools,
            name_prefix=name_prefix,
            keep_clusters=keep_clusters,
            keep_templates=keep_templates,
        )

    tracker = StepTracker()

    try:
        for pool in matching_pools:
            pid = pool["id"]
            pname = pool["name"]

            async with tracker.step(f"drain[{pname}]") as step:
                # Tight timeout — reset is testing-oriented;
                # operators wanting a long graceful drain reach for
                # openvdi_drain_pool directly.
                await openvdi_drain_pool(
                    pool_id=pid,
                    confirm=True,
                    timeout_seconds=60,
                )
                step["details"] = {
                    "pool_name": pname, "pool_id": pid,
                }

            async with tracker.step(
                f"disconnect_active[{pname}]",
            ) as step:
                # Drain blocks new connections but doesn't end
                # existing ones. Any sessions still active after
                # the 60s drain timeout get force-disconnected.
                active = await openvdi_list_sessions(
                    pool_id=pid, status="active",
                )
                disconnected: list[str] = []
                for sess in active:
                    try:
                        await openvdi_force_disconnect_session(
                            sess["id"], confirm=True,
                        )
                        disconnected.append(sess["id"])
                    except BrokerError as inner:
                        # Don't block the rest of cleanup. Pool
                        # delete cascades through anyway.
                        logger.warning(
                            "force-disconnect failed for "
                            "session %s: %s",
                            sess["id"], inner,
                        )
                # No settle-pause: broker.end_session is fully
                # synchronous (transition_to_ended commits before
                # returning), so the desktop's status is already
                # updated by the time the 204 lands.
                step["details"] = {
                    "active_count": len(active),
                    "disconnected_count": len(disconnected),
                }

            async with tracker.step(f"delete[{pname}]") as step:
                await openvdi_delete_pool(
                    pool_id=pid, confirm=True,
                )
                step["details"] = {
                    "pool_name": pname, "pool_id": pid,
                }

        if not keep_templates:
            templates_to_retire: list[str] = []
            async with tracker.step(
                "identify_unreferenced_templates",
            ) as step:
                remaining = await openvdi_list_pools(limit=200)
                referenced = {p["template_id"] for p in remaining}
                templates = await openvdi_list_templates(limit=200)
                for t in templates:
                    if t["id"] not in referenced:
                        templates_to_retire.append(t["id"])
                step["details"] = {
                    "candidates": len(templates_to_retire),
                }
            for tid in templates_to_retire:
                async with tracker.step(
                    f"retire_template[{tid[:8]}]",
                ) as step:
                    await openvdi_retire_template(
                        template_id=tid, confirm=True,
                    )
                    step["details"] = {"template_id": tid}

        if not keep_clusters:
            clusters_to_delete: list[str] = []
            async with tracker.step(
                "identify_unreferenced_clusters",
            ) as step:
                remaining = await openvdi_list_pools(limit=200)
                referenced = {p["cluster_id"] for p in remaining}
                clusters = await openvdi_list_clusters()
                for c in clusters:
                    if c["id"] not in referenced:
                        clusters_to_delete.append(c["id"])
                step["details"] = {
                    "candidates": len(clusters_to_delete),
                }
            for cid in clusters_to_delete:
                async with tracker.step(
                    f"delete_cluster[{cid[:8]}]",
                ) as step:
                    await openvdi_delete_cluster(
                        cluster_id=cid, confirm=True,
                    )
                    step["details"] = {"cluster_id": cid}

        return tracker.success_result(
            operation="reset_test_environment",
            result={
                "name_prefix": name_prefix,
                "pools_deleted": len(matching_pools),
            },
        )

    except BrokerError as exc:
        # No single rollback command for partial reset; the agent
        # re-runs the tool to continue cleanup of whatever's left.
        return tracker.failure_result(
            operation="reset_test_environment",
            error=exc,
            failed_at_step=tracker.last_failed_step(),
            rollback_suggestion=None,
        )


async def _build_dry_run_preview(
    *,
    matching_pools: list[dict[str, Any]],
    name_prefix: str,
    keep_clusters: bool,
    keep_templates: bool,
) -> dict[str, Any]:
    """Aggregate the dry-run shape — what WOULD be touched."""
    would_drain_then_delete: list[dict[str, Any]] = []
    total_desktops = 0
    total_active_sessions = 0
    total_entitlements = 0

    for pool in matching_pools:
        full = await openvdi_get_pool(pool["id"])
        capacity = full.get("capacity", {}) or {}
        sessions = await openvdi_list_sessions(
            pool_id=pool["id"], status="active",
        )
        ents = await openvdi_list_entitlements(pool_id=pool["id"])
        d = capacity.get("total_desktops", 0)
        s = len(sessions)
        e = len(ents)
        total_desktops += d
        total_active_sessions += s
        total_entitlements += e
        would_drain_then_delete.append({
            "pool_id": pool["id"],
            "name": pool["name"],
            "desktops": d,
            "active_sessions": s,
            "entitlements": e,
        })

    would_retire_templates: list[dict[str, str]] = []
    would_delete_clusters: list[dict[str, str]] = []

    if not keep_templates:
        all_pools = await openvdi_list_pools(limit=200)
        # Pools that would still exist post-reset.
        remaining_template_refs = {
            p["template_id"] for p in all_pools
            if not p["name"].startswith(name_prefix)
        }
        templates = await openvdi_list_templates(limit=200)
        would_retire_templates = [
            {"id": t["id"], "name": t["name"]}
            for t in templates
            if t["id"] not in remaining_template_refs
        ]

    if not keep_clusters:
        all_pools = await openvdi_list_pools(limit=200)
        remaining_cluster_refs = {
            p["cluster_id"] for p in all_pools
            if not p["name"].startswith(name_prefix)
        }
        clusters = await openvdi_list_clusters()
        would_delete_clusters = [
            {"id": c["id"], "name": c["name"]}
            for c in clusters
            if c["id"] not in remaining_cluster_refs
        ]

    return {
        "ok": True,
        "dry_run": True,
        "operation": "reset_test_environment",
        "name_prefix": name_prefix,
        "would_drain_then_delete": would_drain_then_delete,
        "would_retire_templates": would_retire_templates,
        "would_delete_clusters": would_delete_clusters,
        "summary": {
            "pools": len(matching_pools),
            "desktops": total_desktops,
            "active_sessions": total_active_sessions,
            "entitlements": total_entitlements,
            "templates": len(would_retire_templates),
            "clusters": len(would_delete_clusters),
        },
        "note": (
            "Pass confirm=True to execute. Reset is destructive — "
            "pools, desktops, entitlements, and (optionally) "
            "templates and clusters will be removed. Drained pools "
            "stay 'draining' until deleted; force-disconnect "
            "handles any sessions that didn't end during drain."
        ),
    }
