"""Audit log query tool."""
from __future__ import annotations

from typing import Any

from openvdi_admin.server import mcp
from openvdi_admin.tools._common import get_broker_client


@mcp.tool()
async def openvdi_query_audit(
    actor: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Query the audit log.

    All filter params are optional; an unfiltered call returns the
    most recent `limit` rows (broker defaults to timestamp DESC).

    Useful filter combinations:
      - actor='alice' → everything Alice did.
      - resource_type='pool', resource_id='<uuid>' → all events on
        a specific pool.
      - action='broker.connect' → all connect attempts.
      - action='broker.*' → all broker.* actions (broker supports
        trailing-`*` prefix wildcard).
      - since='2026-04-01T00:00:00Z' → events after a date.

    `since` / `until` are ISO-8601 timestamp strings; the MCP does
    not parse them — broker validates and surfaces INVALID_REQUEST
    on malformed input.

    Args:
        actor: Username, 'system', or service-account name.
        action: Audit action code (e.g. 'broker.connect',
            'admin.cluster.create'). Supports trailing `*` for
            prefix matching.
        resource_type: 'pool', 'desktop', 'session', 'template',
            'cluster', 'entitlement'.
        resource_id: UUID of the specific resource.
        since: ISO-8601 lower bound (inclusive).
        until: ISO-8601 upper bound (inclusive).
        limit: Max rows (default 100).
        offset: Pagination offset.

    Returns:
        List of audit_log row dicts, newest-first.
    """
    client = get_broker_client()
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    for k, v in (
        ("actor", actor),
        ("action", action),
        ("resource_type", resource_type),
        ("resource_id", resource_id),
        ("since", since),
        ("until", until),
    ):
        if v is not None:
            params[k] = v
    return await client.get("/api/v1/audit", params=params)
