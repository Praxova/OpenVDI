"""openvdi_deploy_pool intent tool — full pool standup.

Composes verify_template + create_pool + grant_entitlement (×N) +
optionally provision_pool. On any mid-flight failure, returns a
structured error with a rollback_hint pointing at the single
`delete_pool` command that would clean up everything (delete_pool
cascades through entitlements + desktops).

No auto-rollback per the seed: agents make that decision based on
the steps array and rollback_hint.
"""
from __future__ import annotations

import logging
from typing import Any

from openvdi_admin.errors import BrokerError
from openvdi_admin.intent._result import StepTracker
from openvdi_admin._tool_wrapper import register_tool
from openvdi_admin.tools._common import require_writable
from openvdi_admin.tools.entitlements import openvdi_grant_entitlement
from openvdi_admin.tools.pools import (
    openvdi_create_pool,
    openvdi_provision_pool,
)
from openvdi_admin.tools.templates import openvdi_get_template


logger = logging.getLogger(__name__)


@register_tool()
async def openvdi_deploy_pool(
    template_id: str,
    pool_name: str,
    pool_display_name: str,
    pool_type: str,
    cluster_id: str,
    vmid_range_start: int,
    vmid_range_end: int,
    name_prefix: str,
    entitlements: list[dict[str, str]],
    min_spare: int = 1,
    max_size: int = 10,
    description: str | None = None,
    pre_provision: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Stand up a complete pool: verify template, create pool, grant
    entitlements, optionally pre-provision warm spares.

    Steps:
      1. Verify template exists and is 'active'.
      2. Create the pool.
      3. Grant each entitlement.
      4. If pre_provision: provision min_spare desktops and wait.

    On failure mid-way, returns a structured `ok=False` result with
    `rollback_hint.suggested_cleanup` pointing at the single
    delete_pool call that would clean up everything (delete_pool
    cascades through entitlements + desktops).

    Args:
        template_id: UUID of an existing template.
        pool_name: URL-safe slug (lowercase, [a-z0-9_-]).
        pool_display_name: Human-readable name shown in UI.
        pool_type: 'persistent' or 'nonpersistent'.
        cluster_id: UUID of the cluster.
        vmid_range_start, vmid_range_end: Inclusive VMID range.
        name_prefix: VM name prefix.
        entitlements: List of {type, name} or
            {principal_type, principal_name} dicts. At least one
            required.
        min_spare: Warm-spare target (used when pre_provision=True).
        max_size: Pool max size.
        description: Optional pool description.
        pre_provision: If True (default), provision min_spare
            desktops after pool + entitlement creation.
        confirm: True to execute. False (default) returns dry-run
            preview.

    Returns:
        IntentResult dict. Success: pool_id, pool_name, provisioned
        count, pre_provision_complete flag.
    """
    require_writable("openvdi_deploy_pool")

    if not entitlements:
        raise BrokerError(
            http_status=400,
            code="INVALID_REQUEST",
            message="entitlements list cannot be empty",
        )

    if not confirm:
        return {
            "ok": True,
            "dry_run": True,
            "operation": "deploy_pool",
            "would_create": {
                "pool_name": pool_name,
                "pool_type": pool_type,
                "vmid_range": [vmid_range_start, vmid_range_end],
                "max_size": max_size,
                "min_spare": min_spare,
                "entitlements_count": len(entitlements),
                "pre_provision_count": (
                    min_spare if pre_provision else 0
                ),
            },
            "note": (
                "Pass confirm=True to execute. Steps: verify "
                "template, create pool, grant entitlements, "
                "optionally provision. On failure, rollback_hint "
                "will point at the delete_pool call that cleans up."
            ),
        }

    tracker = StepTracker()
    pool_id: str | None = None

    try:
        async with tracker.step("verify_template") as step:
            template = await openvdi_get_template(template_id)
            step["details"] = {
                "template_id": template_id,
                "name": template.get("name"),
                "status": template.get("status"),
            }
            if template.get("status") != "active":
                raise BrokerError(
                    http_status=409,
                    code="CONFLICT",
                    message=(
                        f"template "
                        f"{template.get('name', template_id)} has "
                        f"status {template.get('status')!r}; "
                        "deploy_pool requires 'active'"
                    ),
                )

        async with tracker.step("create_pool") as step:
            pool = await openvdi_create_pool(
                name=pool_name,
                display_name=pool_display_name,
                pool_type=pool_type,
                template_id=template_id,
                cluster_id=cluster_id,
                vmid_range_start=vmid_range_start,
                vmid_range_end=vmid_range_end,
                name_prefix=name_prefix,
                min_spare=min_spare,
                max_size=max_size,
                description=description,
            )
            pool_id = pool["id"]
            tracker.record_created("pool", pool_id)
            step["details"] = {
                "pool_id": pool_id,
                "name": pool.get("name"),
            }

        # Each entitlement is its own step so partial-grant failures
        # are visible in the trace.
        for i, ent in enumerate(entitlements):
            async with tracker.step(
                f"grant_entitlement[{i}]",
            ) as step:
                ptype = ent.get("type") or ent.get("principal_type")
                pname = ent.get("name") or ent.get("principal_name")
                if not ptype or not pname:
                    raise BrokerError(
                        http_status=400,
                        code="INVALID_REQUEST",
                        message=(
                            f"entitlement[{i}] missing type or "
                            f"name; got {ent!r}"
                        ),
                    )
                granted = await openvdi_grant_entitlement(
                    pool_id=pool_id,
                    principal_type=ptype,
                    principal_name=pname,
                )
                tracker.record_created("entitlement", granted["id"])
                step["details"] = {
                    "principal": f"{ptype}:{pname}",
                    "entitlement_id": granted["id"],
                }

        provisioned_count = 0
        if pre_provision and min_spare > 0:
            async with tracker.step("provision_pool") as step:
                provisioned_pool = await openvdi_provision_pool(
                    pool_id=pool_id,
                    count=min_spare,
                )
                # PoolReadDetailed.capacity per M5-04 (broker shape).
                capacity = provisioned_pool.get("capacity", {})
                provisioned_count = capacity.get("available", 0)
                step["details"] = {
                    "requested": min_spare,
                    "available_count": provisioned_count,
                    "in_progress_count": capacity.get(
                        "provisioning", 0,
                    ),
                }

        return tracker.success_result(
            operation="deploy_pool",
            result={
                "pool_id": pool_id,
                "pool_name": pool_name,
                "provisioned_count": provisioned_count,
                "pre_provision_complete": (
                    not pre_provision
                    or provisioned_count >= min_spare
                ),
            },
        )

    except BrokerError as exc:
        rollback = (
            f"openvdi_delete_pool({pool_id!r}, confirm=True)"
            if pool_id
            else None
        )
        return tracker.failure_result(
            operation="deploy_pool",
            error=exc,
            failed_at_step=tracker.last_failed_step(),
            rollback_suggestion=rollback,
        )
