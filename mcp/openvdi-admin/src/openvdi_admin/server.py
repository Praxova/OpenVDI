"""FastMCP server entry point.

Constructs the broker clients on startup, hands them to a future
tools-loader, runs over stdio. Tools land in M5-03 onward; this
module registers zero tools.

The MCP server speaks the MCP protocol over stdin/stdout (stdio
transport, per C9). Logs go to stderr; stdout is reserved for the
protocol stream. Launching with `openvdi-admin` (the console
script) or `python -m openvdi_admin.server` produces a process
that hangs on stdin until the parent agent process closes it.
"""
from __future__ import annotations

import asyncio
import logging

from mcp.server.fastmcp import FastMCP

from openvdi_admin.auth import BrokerAuthClient
from openvdi_admin.client import BrokerClient
from openvdi_admin.config import Settings, get_settings
from openvdi_admin.logging import configure_logging


logger = logging.getLogger(__name__)


def build_server(
    settings: Settings | None = None,
) -> tuple[FastMCP, BrokerAuthClient, BrokerClient]:
    """Construct and return the FastMCP server + the broker clients
    it owns. The clients are returned alongside so the entry point
    can close them on shutdown.

    Tools are NOT registered here in M5-02. M5-03 onward will import
    tool modules; their @mcp.tool() decorators self-register.
    """
    if settings is None:
        settings = get_settings()

    configure_logging(
        format=settings.openvdi_mcp_log_format,
        level=settings.openvdi_mcp_log_level,
    )

    auth = BrokerAuthClient(settings)
    client = BrokerClient(auth, settings)

    mcp = FastMCP("openvdi-admin")

    # Stash the client where tool modules can find it. Per C4: single
    # broker-client instance shared across all tools. M5-03's tool
    # modules will introduce a clean accessor `get_broker_client()`
    # that hides this attribute kludge — don't replicate the
    # underscore pattern outside server.py.
    mcp._broker_client = client  # type: ignore[attr-defined]
    mcp._broker_auth = auth      # type: ignore[attr-defined]

    return mcp, auth, client


async def main() -> None:
    """Async entry point. Runs the MCP server until the parent
    process closes the stdin stream."""
    mcp, auth, client = build_server()
    try:
        await mcp.run_stdio_async()
    finally:
        await client.close()
        await auth.close()


def main_sync() -> None:
    """Synchronous wrapper for the `openvdi-admin` console script.
    Runs the asyncio event loop until completion."""
    asyncio.run(main())


if __name__ == "__main__":
    main_sync()
