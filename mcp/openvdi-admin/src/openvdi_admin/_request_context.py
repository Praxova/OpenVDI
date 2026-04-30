"""ContextVar holding the current MCP tool's request_id.

Each tool invocation generates a fresh UUID at the top of its
instrumented body; the same UUID is attached as `X-Request-ID`
on outbound broker requests AND included in every log line emitted
during that tool's execution.

`contextvars` is asyncio-native: each task sees its own value with
no cross-talk between concurrent tool invocations. The value
persists across `await` points within the same task, so the
BrokerClient and BrokerAuthClient — which run inside the tool's
task — see the same request_id at every outbound HTTP call.
"""
from __future__ import annotations

import contextvars
import uuid


_REQUEST_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "openvdi_mcp_request_id", default=None,
)


def new_request_id() -> str:
    """Generate a UUID4 and set it as the current task's request_id.

    Returns the new ID so the caller can embed it in the first log
    record emitted for the tool call.
    """
    rid = str(uuid.uuid4())
    _REQUEST_ID.set(rid)
    return rid


def current_request_id() -> str | None:
    """Return the current task's request_id, or None if not set.

    None means "we're outside an instrumented tool body" — for
    example during build_server initialization, or in tests that
    haven't established a context. Outbound httpx calls skip the
    `X-Request-ID` header in this case.
    """
    return _REQUEST_ID.get()


def clear_request_id() -> None:
    """Reset to None. Tests use this between cases to ensure
    isolation; production never clears (the ContextVar dies with
    the task naturally)."""
    _REQUEST_ID.set(None)
