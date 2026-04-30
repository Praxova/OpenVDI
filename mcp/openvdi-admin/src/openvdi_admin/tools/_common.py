"""Shared helpers for thin-wrapper tool modules.

Three exports:
  - get_broker_client(): the runtime BrokerClient singleton accessor
  - require_writable(): raises BrokerError when read-only mode is set
  - dry_run_envelope(): standardizes the confirm=False preview shape
"""
from __future__ import annotations

from typing import Any

from openvdi_admin.client import BrokerClient
from openvdi_admin.config import get_settings
from openvdi_admin.errors import BrokerError


def get_broker_client() -> BrokerClient:
    """Return the live BrokerClient.

    Imported lazily to break the import cycle: server imports
    tool modules to register decorators; tool modules import from
    _common; _common imports server only inside this function.
    """
    from openvdi_admin.server import get_client

    return get_client()


def require_writable(tool_name: str) -> None:
    """Raise BrokerError if the MCP is in read-only mode.

    Every destructive tool calls this at the top of its body.
    Read-only tools (list, get) do NOT call this.
    """
    settings = get_settings()
    if settings.openvdi_mcp_read_only:
        raise BrokerError(
            http_status=403,
            code="READ_ONLY_MODE",
            message=(
                f"Tool '{tool_name}' is destructive and the MCP is in "
                "read-only mode. Restart with OPENVDI_MCP_READ_ONLY=false "
                "to enable mutations."
            ),
        )


def dry_run_envelope(
    *,
    action: str,
    target: dict[str, Any],
    blocked_by: dict[str, Any] | None = None,
    note: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Standardize the preview shape returned when confirm=False.

    Args:
        action: short verb-noun describing what would happen
                (e.g. 'delete_cluster', 'drain_pool').
        target: identifying info about what would be affected.
        blocked_by: dict of dependencies that would prevent execution
                    (e.g. {'pools': [...]}). None when nothing blocks.
        note: free-form clarification for the agent.
        extra: additional fields specific to this dry-run.

    Returns:
        Dict matching the documented dry-run shape:
            {ok, dry_run, action, target, blocked_by, note, ...extra}
    """
    payload: dict[str, Any] = {
        "ok": True,
        "dry_run": True,
        "action": action,
        "target": target,
        "blocked_by": blocked_by,
        "note": note,
    }
    if extra:
        payload.update(extra)
    return payload
