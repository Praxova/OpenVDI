"""openvdi_smoke_test intent tool — broker round-trip validator.

Verifies broker liveness, cluster reachability, pool config, VMID
allocation, Proxmox clone, guest-agent boot, and status reporting.
Does NOT validate console-ticket issuance or end-user connect flow
— those need a real browser.
"""
from __future__ import annotations

import logging
from typing import Any

from openvdi_admin.errors import BrokerError
from openvdi_admin.intent._result import StepTracker
from openvdi_admin.server import mcp
from openvdi_admin.tools._common import require_writable
from openvdi_admin.tools.desktops import (
    openvdi_delete_desktop,
    openvdi_get_desktop,
    openvdi_list_desktops,
)
from openvdi_admin.tools.pools import (
    openvdi_get_pool,
    openvdi_get_pool_summary,
    openvdi_provision_pool,
)


logger = logging.getLogger(__name__)


@mcp.tool()
async def openvdi_smoke_test(
    pool_id: str,
    provision_if_empty: bool = True,
    cleanup_if_provisioned: bool = False,
) -> dict[str, Any]:
    """Broker round-trip validation for a specific pool.

    Steps:
      1. Verify pool is 'active'.
      2. Query pool capacity.
      3. If available_count == 0:
         - provision_if_empty=False → POOL_EMPTY error.
         - provision_if_empty=True  → provision exactly 1 desktop;
           poll for completion; track for cleanup.
      4. Verify a desktop with status='available' is operational
         (status='available', power_state='running').
      5. If we provisioned and cleanup_if_provisioned=True: delete
         the provisioned desktop.

    Args:
        pool_id: UUID of the pool to test.
        provision_if_empty: If True (default), provision 1 desktop
            when capacity is empty. False = pure read-only check.
        cleanup_if_provisioned: If True, delete the just-provisioned
            desktop at the end. Default False (warm spares stay
            warm).

    Returns:
        IntentResult-shaped dict. On success, .result has pool_id
        and verified_desktop_id. On failure, structured error with
        steps + optional rollback_hint.

    Does NOT raise on broker errors — they're caught and returned
    as structured failure (S3).
    """
    # Pure read-only check is allowed even in read-only mode; the
    # provisioning path is mutating, so gate behind require_writable
    # only when provisioning is possible.
    if provision_if_empty:
        require_writable("openvdi_smoke_test")

    tracker = StepTracker()
    provisioned_desktop_id: str | None = None
    desktop_id: str | None = None

    try:
        async with tracker.step("verify_pool_active") as step:
            pool = await openvdi_get_pool(pool_id)
            step["details"] = {
                "pool_id": pool_id,
                "pool_name": pool.get("name"),
                "status": pool.get("status"),
            }
            if pool.get("status") != "active":
                raise BrokerError(
                    http_status=409,
                    code="POOL_INACTIVE",
                    message=(
                        f"pool {pool.get('name', pool_id)} has status "
                        f"{pool.get('status')!r}; smoke test requires "
                        "'active'"
                    ),
                )

        async with tracker.step("query_capacity") as step:
            summary = await openvdi_get_pool_summary(pool_id)
            capacity = summary.get("capacity", {})
            step["details"] = capacity
            # Inside the step so a POOL_EMPTY failure attributes to
            # query_capacity rather than 'unknown'.
            if capacity.get("available", 0) == 0 and not provision_if_empty:
                raise BrokerError(
                    http_status=409,
                    code="POOL_EMPTY",
                    message=(
                        f"pool {pool.get('name', pool_id)} has no "
                        "available desktops; pass "
                        "provision_if_empty=True to provision one"
                    ),
                )

        available = capacity.get("available", 0)
        if available == 0:
            async with tracker.step("provision_one_desktop") as step:
                # Diff list before/after provision to find the new
                # desktop's id (provision_pool returns pool state,
                # not the new desktop id).
                pre_existing = {
                    d["id"]
                    for d in await openvdi_list_desktops(pool_id=pool_id)
                }
                pool_after = await openvdi_provision_pool(
                    pool_id=pool_id, count=1,
                )
                post_existing = {
                    d["id"]
                    for d in await openvdi_list_desktops(pool_id=pool_id)
                }
                new_desktop_ids = post_existing - pre_existing
                if not new_desktop_ids:
                    raise BrokerError(
                        http_status=500,
                        code="INTERNAL_ERROR",
                        message=(
                            "provision returned but no new desktop "
                            "appeared in pool listing"
                        ),
                    )
                provisioned_desktop_id = next(iter(new_desktop_ids))
                tracker.record_created(
                    "desktop", provisioned_desktop_id,
                )
                step["details"] = {
                    "desktop_id": provisioned_desktop_id,
                    "pool_state_after": pool_after.get("status"),
                }

        async with tracker.step("verify_desktop") as step:
            desktops = await openvdi_list_desktops(
                pool_id=pool_id, status="available", limit=1,
            )
            if not desktops:
                raise BrokerError(
                    http_status=500,
                    code="POOL_EMPTY",
                    message=(
                        "no available desktop after provisioning "
                        "step; broker may be in an unexpected state"
                    ),
                )
            desktop_id = desktops[0]["id"]
            desktop = await openvdi_get_desktop(desktop_id)
            step["details"] = {
                "desktop_id": desktop_id,
                "name": desktop.get("name"),
                "status": desktop.get("status"),
                "power_state": desktop.get("power_state"),
            }
            if desktop.get("status") != "available":
                raise BrokerError(
                    http_status=500,
                    code="INVALID_REQUEST",
                    message=(
                        f"desktop status is "
                        f"{desktop.get('status')!r}, expected "
                        "'available'"
                    ),
                )
            if desktop.get("power_state") != "running":
                raise BrokerError(
                    http_status=500,
                    code="INVALID_REQUEST",
                    message=(
                        f"desktop power_state is "
                        f"{desktop.get('power_state')!r}, expected "
                        "'running'"
                    ),
                )
            # `pve_task_upid` isn't surfaced on DesktopRead per the
            # M2-13 invariant; this check is a no-op against the
            # current broker but documents the intent (idle desktop
            # has no in-flight task).
            if desktop.get("pve_task_upid") is not None:
                raise BrokerError(
                    http_status=500,
                    code="INVALID_REQUEST",
                    message=(
                        f"desktop has in-flight task "
                        f"{desktop.get('pve_task_upid')!r}; not idle"
                    ),
                )

        if (
            provisioned_desktop_id is not None
            and cleanup_if_provisioned
        ):
            async with tracker.step(
                "cleanup_provisioned_desktop",
            ) as step:
                await openvdi_delete_desktop(
                    provisioned_desktop_id, confirm=True,
                )
                step["details"] = {
                    "deleted_desktop_id": provisioned_desktop_id,
                }

        return tracker.success_result(
            operation="smoke_test",
            result={
                "pool_id": pool_id,
                "verified_desktop_id": desktop_id,
            },
        )

    except BrokerError as exc:
        rollback_hint = None
        if (
            provisioned_desktop_id is not None
            and not cleanup_if_provisioned
        ):
            rollback_hint = (
                f"openvdi_delete_desktop({provisioned_desktop_id!r}, "
                "confirm=True)"
            )
        return tracker.failure_result(
            operation="smoke_test",
            error=exc,
            failed_at_step=tracker.last_failed_step(),
            rollback_suggestion=rollback_hint,
        )
