"""Decorator-based instrumentation for MCP tools.

Every @mcp.tool()-registered function is wrapped so:
  - A fresh request_id (UUID4) is generated and set on the
    `_REQUEST_ID` ContextVar — the BrokerClient + BrokerAuthClient
    pick it up and attach `X-Request-ID` on every outbound HTTP
    call. Operators correlating "what did the agent do" can grep
    one UUID across MCP and broker logs.
  - One INFO log line per tool completion (and optionally one at
    start, gated by OPENVDI_MCP_LOG_TOOL_STARTS).
  - On BrokerError: ERROR log without traceback (the structured
    exception_code carries enough context). Re-raised.
  - On unexpected exception: ERROR log WITH traceback. Re-raised.

Use `register_tool()` for new tools — it combines instrumentation
with FastMCP registration in a single decorator.

CRITICAL — sensitive data handling:
============================================================
DO NOT, EVER, log tool arguments. This is a hard rule, not a
best practice. Tool args may contain:
  - Service-account passwords (BrokerAuthClient relogin path).
  - Provider API tokens (cluster create / update).
  - LDAP credentials forwarded through the agent.

The redaction problem at the MCP layer is genuinely hard to get
right (new sensitive fields appear with new tools; key-name
heuristics miss the unconventional ones; partial-redaction leaks
structure). The broker's audit_log already records redacted args
per M2-12; operators correlating "what did Claude pass to this
tool" use the request_id from the MCP log to find the broker
audit row, which has the redacted view.

If a future contributor proposes "let me log args with a
redaction list / opt-in / debug-only / etc." — the answer is no.
The right channel for tool args is the broker's audit log, full
stop. Do not weaken this rule. (Forward note from M5-08 plan.)
============================================================
"""
from __future__ import annotations

import functools
import logging
import time
from typing import Any, Awaitable, Callable, TypeVar

from openvdi_admin._mcp_instance import mcp
from openvdi_admin._request_context import new_request_id
from openvdi_admin.config import get_settings
from openvdi_admin.errors import BrokerError


logger = logging.getLogger(__name__)


_F = TypeVar("_F", bound=Callable[..., Awaitable[Any]])


def instrument_tool(fn: _F) -> _F:
    """Wrap an async tool function with request_id + logging.

    Decorator order: must be applied INSIDE @mcp.tool() so FastMCP
    registers the wrapped version. Use `register_tool()` instead of
    stacking the two decorators by hand — it's the documented
    call-site shape.

    Tool ARGS are not logged. See module docstring.
    """
    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        rid = new_request_id()
        tool_name = fn.__name__
        start = time.monotonic()

        if _log_tool_starts_enabled():
            logger.info(
                "tool started",
                extra={"tool": tool_name, "request_id": rid},
            )

        try:
            result = await fn(*args, **kwargs)
        except BrokerError as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.error(
                "tool failed",
                extra={
                    "tool": tool_name,
                    "request_id": rid,
                    "outcome": "error",
                    "duration_ms": duration_ms,
                    "exception_type": "BrokerError",
                    "exception_code": exc.code,
                    "exception_message": exc.message,
                },
                exc_info=False,
            )
            raise
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.error(
                "tool failed (unexpected)",
                extra={
                    "tool": tool_name,
                    "request_id": rid,
                    "outcome": "error",
                    "duration_ms": duration_ms,
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                },
                exc_info=True,
            )
            raise
        else:
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.info(
                "tool completed",
                extra={
                    "tool": tool_name,
                    "request_id": rid,
                    "outcome": "ok",
                    "duration_ms": duration_ms,
                    "result_envelope_ok": _result_envelope_ok(result),
                },
            )
            return result

    return wrapper  # type: ignore[return-value]


def _log_tool_starts_enabled() -> bool:
    """Read the OPENVDI_MCP_LOG_TOOL_STARTS setting.

    Falls back to False if Settings can't be loaded — for example in
    unit tests where required env vars are unset. The wrapper still
    works (and emits the per-completion line); only the optional
    "tool started" line is skipped.
    """
    try:
        return get_settings().openvdi_mcp_log_tool_starts
    except Exception:
        return False


def _result_envelope_ok(result: Any) -> bool | None:
    """Return result['ok'] if it's an IntentResult-shaped dict;
    otherwise None.

    Distinguishes:
      - intent tool returned ok=True → log shows envelope_ok=True
      - intent tool returned ok=False structured failure → log
        shows outcome=ok, envelope_ok=False (the function call
        succeeded; the operation it modeled didn't)
      - thin wrapper returned a list / non-IntentResult dict →
        log shows envelope_ok=null (no envelope to read)
    """
    if (
        isinstance(result, dict)
        and "ok" in result
        and isinstance(result["ok"], bool)
    ):
        return result["ok"]
    return None


def register_tool(name: str | None = None):
    """Combined decorator: instruments + registers with FastMCP.

    Replaces `@mcp.tool()` at call sites. Equivalent to:

        @mcp.tool()
        @instrument_tool
        async def openvdi_x(...): ...

    Usage:

        from openvdi_admin._tool_wrapper import register_tool

        @register_tool()
        async def openvdi_list_clusters() -> list[dict[str, Any]]:
            ...
    """

    def decorator(fn: Callable[..., Awaitable[Any]]):
        wrapped = instrument_tool(fn)
        if name:
            return mcp.tool(name=name)(wrapped)
        return mcp.tool()(wrapped)

    return decorator
