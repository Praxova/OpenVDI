"""Cluster admin tools."""
from __future__ import annotations

import logging
from typing import Any

from openvdi_admin.server import mcp
from openvdi_admin.tools._common import (
    dry_run_envelope,
    get_broker_client,
    require_writable,
)


logger = logging.getLogger(__name__)


@mcp.tool()
async def openvdi_list_clusters() -> list[dict[str, Any]]:
    """List all registered hypervisor clusters.

    Returns each cluster's id, name, provider_type, api_url, status,
    and updated_at. The broker omits credential fields (token_secret
    is never returned).

    Status values: 'pending' (just registered, awaiting first ping),
    'active' (last ping succeeded), 'maintenance' (admin-disabled),
    'offline' (last ping failed).
    """
    client = get_broker_client()
    return await client.get("/api/v1/clusters")


@mcp.tool()
async def openvdi_get_cluster(cluster_id: str) -> dict[str, Any]:
    """Get full details for a single cluster, including live node
    status pulled from the cluster's Proxmox API at request time.

    Args:
        cluster_id: UUID of the cluster.

    Raises:
        BrokerError(NOT_FOUND): no cluster with that id.
        BrokerError(PROVIDER_ERROR): cluster credentials work but
            Proxmox itself is unreachable.
    """
    client = get_broker_client()
    return await client.get(f"/api/v1/clusters/{cluster_id}")


@mcp.tool()
async def openvdi_create_cluster(
    name: str,
    api_url: str,
    token_id: str,
    token_secret: str,
    verify_ssl: bool = True,
    node_filter: str | None = None,
    provider_type: str = "proxmox",
) -> dict[str, Any]:
    """Register a new hypervisor cluster.

    The broker validates credentials synchronously by calling
    provider.ping() before persisting. Submission can take 1-2
    seconds; longer if the cluster is slow.

    Args:
        name: Display name for the cluster.
        api_url: Hypervisor API endpoint, e.g. https://pve1.example.com:8006.
        token_id: Proxmox API token id, format user@realm!tokenname.
        token_secret: Token secret value.
        verify_ssl: Verify TLS certificate. Set False only for
            self-signed dev clusters.
        node_filter: Optional comma-separated list of node names
            to limit the cluster to.
        provider_type: Hypervisor provider. Default 'proxmox'; v0
            only supports Proxmox.

    Raises:
        BrokerError(PROVIDER_ERROR): credentials don't reach the
            cluster, or ping fails.
        BrokerError(CONFLICT): a cluster with this name already exists.
    """
    require_writable("openvdi_create_cluster")
    client = get_broker_client()
    body: dict[str, Any] = {
        "name": name,
        "api_url": api_url,
        "token_id": token_id,
        "token_secret": token_secret,
        "verify_ssl": verify_ssl,
        "provider_type": provider_type,
    }
    if node_filter is not None:
        body["node_filter"] = node_filter
    return await client.post("/api/v1/clusters", body=body)


@mcp.tool()
async def openvdi_update_cluster(
    cluster_id: str,
    name: str | None = None,
    api_url: str | None = None,
    token_id: str | None = None,
    token_secret: str | None = None,
    verify_ssl: bool | None = None,
    node_filter: str | None = None,
) -> dict[str, Any]:
    """Update an existing cluster. Only fields you pass are modified;
    omitted fields keep their existing value (broker's skip-if-null
    semantics).

    To clear node_filter, pass an empty string. Note that
    token_secret is special: passing it changes the secret;
    omitting it keeps the existing secret. There is no way to
    "clear" the secret (a cluster must always have credentials).

    The broker re-validates credentials by ping() if api_url,
    token_id, or token_secret changed.

    Args:
        cluster_id: UUID of the cluster to update.
        name, api_url, token_id, token_secret, verify_ssl,
        node_filter: see openvdi_create_cluster.

    Raises:
        BrokerError(NOT_FOUND): no cluster with that id.
        BrokerError(PROVIDER_ERROR): updated credentials don't
            reach the cluster.
    """
    require_writable("openvdi_update_cluster")
    client = get_broker_client()
    # Build body from only the fields explicitly provided.
    body: dict[str, Any] = {}
    if name is not None:
        body["name"] = name
    if api_url is not None:
        body["api_url"] = api_url
    if token_id is not None:
        body["token_id"] = token_id
    if token_secret is not None:
        body["token_secret"] = token_secret
    if verify_ssl is not None:
        body["verify_ssl"] = verify_ssl
    if node_filter is not None:
        # Empty string → null on the wire (clears the filter on the
        # broker side). Same idiom as the portal's FE8 pattern.
        body["node_filter"] = node_filter if node_filter != "" else None

    return await client.put(f"/api/v1/clusters/{cluster_id}", body=body)


@mcp.tool()
async def openvdi_delete_cluster(
    cluster_id: str,
    confirm: bool = False,
) -> dict[str, Any] | None:
    """Delete a cluster registration.

    Pools that reference the cluster MUST be deleted first. The
    broker rejects with CONFLICT otherwise.

    With confirm=False (default), returns a dry-run preview showing
    what would be affected and which pools would block the delete.

    Args:
        cluster_id: UUID of the cluster.
        confirm: True to execute; False (default) for dry run.

    Raises:
        BrokerError(NOT_FOUND): no cluster with that id.
        BrokerError(CONFLICT): pools reference the cluster.
    """
    require_writable("openvdi_delete_cluster")
    client = get_broker_client()

    if not confirm:
        cluster = await client.get(f"/api/v1/clusters/{cluster_id}")
        # Best-effort dependency check: the dry-run could miss a pool
        # created between this check and the confirm=True call.
        pools = await client.get(
            "/api/v1/pools",
            params={"cluster_id": cluster_id},
        )
        return dry_run_envelope(
            action="delete_cluster",
            target={
                "id": cluster_id,
                "name": cluster.get("name", "<unknown>"),
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
                "Pass confirm=True to execute. CONFLICT will be raised "
                "if any pools reference the cluster at execution time."
            ),
        )

    return await client.delete(f"/api/v1/clusters/{cluster_id}")
