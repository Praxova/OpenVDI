"""Template admin tools."""
from __future__ import annotations

import logging
from typing import Any

from openvdi_admin._tool_wrapper import register_tool
from openvdi_admin.tools._common import (
    dry_run_envelope,
    get_broker_client,
    require_writable,
)


logger = logging.getLogger(__name__)


@register_tool()
async def openvdi_list_templates(
    cluster_id: str | None = None,
    os_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List registered VDI templates.

    Args:
        cluster_id: Filter by cluster.
        os_type: Filter by OS family ('windows11', 'ubuntu24', etc.).
        status: Filter by lifecycle status ('active', 'building',
            'error', 'retired').
        limit: Max results per call (1-200, default 50).
        offset: Pagination offset.
    """
    client = get_broker_client()
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if cluster_id is not None:
        params["cluster_id"] = cluster_id
    if os_type is not None:
        params["os_type"] = os_type
    if status is not None:
        params["status"] = status
    return await client.get("/api/v1/templates", params=params)


@register_tool()
async def openvdi_get_template(template_id: str) -> dict[str, Any]:
    """Get full details for a single template.

    Args:
        template_id: UUID of the template.
    """
    client = get_broker_client()
    return await client.get(f"/api/v1/templates/{template_id}")


@register_tool()
async def openvdi_register_template(
    cluster_id: str,
    name: str,
    pve_vmid: int,
    pve_node: str,
    os_type: str,
    cpu_cores: int = 2,
    memory_mb: int = 4096,
    disk_gb: int = 60,
    description: str | None = None,
    gpu_required: bool = False,
) -> dict[str, Any]:
    """Register a Proxmox VM as a VDI template.

    The Proxmox VM must already exist and be marked as a template
    (qm template <vmid> on the PVE host) before registration.
    The broker validates this by reading VM status from Proxmox
    during registration; non-template VMs are rejected.

    Args:
        cluster_id: UUID of the cluster the template lives in.
        name: Display name for the template.
        pve_vmid: VMID of the existing template VM on Proxmox.
        pve_node: Proxmox node name where the template lives.
        os_type: OS family identifier (e.g. 'windows11', 'ubuntu24').
        cpu_cores: Default cpu cores for desktops cloned from this template.
        memory_mb: Default memory in MB.
        disk_gb: Default disk size in GB.
        description: Free-form description.
        gpu_required: True if desktops cloned from this template need
            GPU passthrough/SR-IOV.

    Raises:
        BrokerError(NOT_FOUND): cluster or VMID doesn't exist.
        BrokerError(CONFLICT): VMID is not a template, or already
            registered with OpenVDI.
    """
    require_writable("openvdi_register_template")
    client = get_broker_client()
    body: dict[str, Any] = {
        "cluster_id": cluster_id,
        "name": name,
        "pve_vmid": pve_vmid,
        "pve_node": pve_node,
        "os_type": os_type,
        "cpu_cores": cpu_cores,
        "memory_mb": memory_mb,
        "disk_gb": disk_gb,
        "gpu_required": gpu_required,
    }
    if description is not None:
        body["description"] = description
    return await client.post("/api/v1/templates", body=body)


@register_tool()
async def openvdi_update_template(
    template_id: str,
    name: str | None = None,
    description: str | None = None,
    os_type: str | None = None,
    cpu_cores: int | None = None,
    memory_mb: int | None = None,
    disk_gb: int | None = None,
    gpu_required: bool | None = None,
) -> dict[str, Any]:
    """Update template metadata. Cannot change pve_vmid, pve_node,
    or cluster_id — those are identity fields. Retire and re-register
    instead.

    Args:
        template_id: UUID of the template.
        name, description, os_type, cpu_cores, memory_mb, disk_gb,
        gpu_required: see openvdi_register_template. Only fields
            you pass are modified.

    Raises:
        BrokerError(NOT_FOUND): no template with that id.
    """
    require_writable("openvdi_update_template")
    client = get_broker_client()
    body: dict[str, Any] = {}
    candidates: dict[str, Any] = {
        "name": name,
        "description": description,
        "os_type": os_type,
        "cpu_cores": cpu_cores,
        "memory_mb": memory_mb,
        "disk_gb": disk_gb,
        "gpu_required": gpu_required,
    }
    for field, value in candidates.items():
        if value is not None:
            body[field] = value
    return await client.put(f"/api/v1/templates/{template_id}", body=body)


@register_tool()
async def openvdi_validate_template(template_id: str) -> dict[str, Any]:
    """Re-check the template against Proxmox: confirm it's still a
    template, the QEMU guest agent is configured (`agent: 1` in PVE
    config), and storage is reachable.

    May change template.status: 'building' -> 'active' on success;
    'active' -> 'error' on failure. Idempotent; safe to call repeatedly.

    Classified destructive (require_writable applies) because it CAN
    mutate DB state. No confirm gate — the operation is read-shaped
    in steady state.

    Args:
        template_id: UUID of the template.

    Returns:
        Updated template record with current status and any
        validation messages.

    Raises:
        BrokerError(NOT_FOUND): no template with that id.
        BrokerError(PROVIDER_ERROR): cluster unreachable.
    """
    require_writable("openvdi_validate_template")
    client = get_broker_client()
    return await client.post(f"/api/v1/templates/{template_id}/validate")


@register_tool()
async def openvdi_retire_template(
    template_id: str,
    confirm: bool = False,
) -> dict[str, Any] | None:
    """Retire a template. Pools currently using the template MUST be
    deleted first. Retirement is reversible (status flips back to
    'active' on a future validate call) but the broker rejects pool
    creation against retired templates.

    With confirm=False (default), returns a dry-run preview.

    Args:
        template_id: UUID of the template.
        confirm: True to execute.

    Raises:
        BrokerError(NOT_FOUND): no template with that id.
        BrokerError(CONFLICT): pools still reference the template.
    """
    require_writable("openvdi_retire_template")
    client = get_broker_client()

    if not confirm:
        template = await client.get(f"/api/v1/templates/{template_id}")
        pools = await client.get(
            "/api/v1/pools",
            params={"template_id": template_id},
        )
        return dry_run_envelope(
            action="retire_template",
            target={
                "id": template_id,
                "name": template.get("name", "<unknown>"),
            },
            blocked_by=(
                {
                    "pools": [
                        {"id": p["id"], "name": p["name"]} for p in pools
                    ]
                }
                if pools
                else None
            ),
            note=(
                "Pass confirm=True to retire. CONFLICT will be raised "
                "if any pools reference the template at execution time. "
                "Retirement is reversible via openvdi_validate_template "
                "once the pool dependency is cleared."
            ),
        )

    return await client.delete(f"/api/v1/templates/{template_id}")
