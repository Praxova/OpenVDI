"""FastMCP server entry point.

Module layout:
  - `mcp` is a module-level FastMCP singleton. Tool modules register
    their @mcp.tool() decorators against it at import time.
  - `_BROKER_AUTH` and `_BROKER_CLIENT` are module-level singletons
    populated by build_server(). Tools access them via
    tools._common.get_broker_client() which wraps `get_client()`.
  - At the bottom of this file we import tools/<resource>.py modules
    purely for the decorator side-effects.

The MCP server speaks the MCP protocol over stdin/stdout (stdio
transport, per C9). Logs go to stderr; stdout is reserved for the
protocol stream.
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


# Module-level FastMCP singleton. Tools register via @mcp.tool()
# decorators in tools/<resource>.py modules.
mcp = FastMCP("openvdi-admin")


# Module-level broker client singletons populated by build_server().
# Tools access these via tools._common.get_broker_client() which
# wraps get_client() so tests can monkeypatch a single name.
_BROKER_AUTH: BrokerAuthClient | None = None
_BROKER_CLIENT: BrokerClient | None = None


def get_client() -> BrokerClient:
    """Return the live BrokerClient. Raises if build_server() hasn't run.

    Tool modules access this via tools._common.get_broker_client()
    which wraps this function so tests can monkeypatch the wrapper
    without touching this module's internals.
    """
    if _BROKER_CLIENT is None:
        raise RuntimeError(
            "MCP server not initialized; build_server() must run before "
            "tools execute"
        )
    return _BROKER_CLIENT


def build_server(settings: Settings | None = None) -> FastMCP:
    """Initialize broker auth + client singletons. Returns the FastMCP
    instance for the caller to run.

    Tools register at import time via @mcp.tool() decorators in the
    tool modules imported at the bottom of this file. build_server()
    only initializes the broker client state; the tools are already
    registered by the time it runs.

    Calling this function more than once replaces the singletons —
    the previous httpx clients are leaked. Tests should call it once
    per fixture (or use the mock pattern in tests/tools/ test files
    that monkeypatch get_broker_client directly).
    """
    global _BROKER_AUTH, _BROKER_CLIENT

    if settings is None:
        settings = get_settings()

    configure_logging(
        format=settings.openvdi_mcp_log_format,
        level=settings.openvdi_mcp_log_level,
    )

    _BROKER_AUTH = BrokerAuthClient(settings)
    _BROKER_CLIENT = BrokerClient(_BROKER_AUTH, settings)

    return mcp


# Register tools by importing the modules. The @mcp.tool() decorators
# in each module side-effect onto `mcp` at import time. M5-05 adds
# more imports here.
import openvdi_admin.tools.audit         # noqa: E402, F401
import openvdi_admin.tools.clusters      # noqa: E402, F401
import openvdi_admin.tools.dashboard     # noqa: E402, F401
import openvdi_admin.tools.entitlements  # noqa: E402, F401
import openvdi_admin.tools.pools         # noqa: E402, F401
import openvdi_admin.tools.templates     # noqa: E402, F401


async def main() -> None:
    """Async entry point. Runs the MCP server until stdin closes."""
    server = build_server()
    try:
        await server.run_stdio_async()
    finally:
        # Defensive — if build_server() partially failed, the close
        # calls still no-op for None.
        if _BROKER_CLIENT is not None:
            await _BROKER_CLIENT.close()
        if _BROKER_AUTH is not None:
            await _BROKER_AUTH.close()


def main_sync() -> None:
    """Synchronous wrapper for the `openvdi-admin` console script."""
    asyncio.run(main())


if __name__ == "__main__":
    main_sync()
