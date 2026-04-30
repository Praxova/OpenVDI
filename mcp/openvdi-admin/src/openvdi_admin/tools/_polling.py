"""Polling helpers for long-running broker operations.

The broker's destructive endpoints (provision, drain) return 202
Accepted and run the actual work asynchronously via background
tasks and the pool_provisioner / task_tracker workers. The MCP
wraps the async-then-poll dance so agents see one synchronous-
looking call per T6.

Reused by M5-05 desktop-power tools and M5-06 intent tools.

Terminal-state predicates are module-level functions so they're
trivial to test in isolation. Each predicate operates on a single
state dict; for compound state (e.g. drain needs pool + session
count) the calling tool composes a fetch that synthesizes a
combined dict.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


_DEFAULT_POLL_INTERVAL_SECONDS = 2.0


# Default per-operation timeouts per T6.
# Power transitions (start/stop/shutdown/reboot) finish in seconds on
# healthy hosts; 30s leaves enough headroom for slow guest agents.
_DEFAULT_DESKTOP_POWER_TIMEOUT_SECONDS = 30
# Rebuild = stop + destroy + re-clone + start. Local-lvm linked clones
# typically complete in 30-90s; 600s sizes for slow storage / loaded
# clusters. Agents on fast hardware can pass timeout_seconds=120.
_DEFAULT_DESKTOP_REBUILD_TIMEOUT_SECONDS = 600


async def wait_for_pool_terminal_state(
    *,
    fetch: Callable[[], Awaitable[dict[str, Any]]],
    is_terminal: Callable[[dict[str, Any]], bool],
    timeout_seconds: int,
    poll_interval: float = _DEFAULT_POLL_INTERVAL_SECONDS,
) -> dict[str, Any]:
    """Poll `fetch()` until `is_terminal(response)` returns True or
    the timeout fires.

    On timeout, returns the LAST observed state — does not raise.
    Agents prefer "got X seconds of progress" over "we waited
    forever then crashed" (per T6).

    Args:
        fetch: Async callable returning the current state dict.
        is_terminal: Predicate; True when polling should stop.
        timeout_seconds: Max wall-clock time before returning. 0 is
            allowed and causes a single fetch with no further polls.
        poll_interval: Sleep between polls.

    Returns:
        Most recent state dict. Caller inspects the relevant fields
        to determine whether terminal was reached or timeout fired.

    Raises:
        BrokerError: passthrough from `fetch()`. Polling does NOT
        absorb broker errors — the agent decides whether to retry.
    """
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    last_state: dict[str, Any] = await fetch()

    if is_terminal(last_state):
        return last_state

    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(poll_interval)
        last_state = await fetch()
        if is_terminal(last_state):
            return last_state

    logger.warning(
        "wait_for_pool_terminal_state timed out after %ds; "
        "returning last observed state",
        timeout_seconds,
    )
    return last_state


def pool_provision_terminal(state: dict[str, Any]) -> bool:
    """True when no desktops are mid-provisioning.

    Reads the `capacity` dict from `GET /pools/{id}` (PoolReadDetailed).
    Provision is terminal when `capacity.provisioning + capacity.deleting`
    drops to 0 — every requested clone has either reached `available`
    or fallen into `error`.

    Defensive: a missing `capacity` block returns True so a malformed
    response can't cause infinite polling. The agent will see no
    counts changing and can retry openvdi_get_pool.
    """
    capacity = state.get("capacity") or {}
    in_flight = capacity.get("provisioning", 0) + capacity.get("deleting", 0)
    return in_flight == 0


def pool_drain_terminal(state: dict[str, Any]) -> bool:
    """True when a draining pool has no remaining active sessions.

    The broker stays in `status='draining'` indefinitely after
    `POST /pools/{id}/drain` — there is no auto-flip to `disabled`
    (M4-21). Drain is "complete" when active sessions reach 0; the
    operator can then re-enable or delete the pool.

    Expects the calling tool to compose a fetch that synthesizes
    `_active_session_count` onto the pool dict by side-querying
    `GET /sessions?pool_id=X&status=active`.
    """
    return state.get("_active_session_count", 0) == 0


async def wait_for_desktop_terminal_state(
    *,
    fetch: Callable[[], Awaitable[dict[str, Any]]],
    is_terminal: Callable[[dict[str, Any]], bool],
    timeout_seconds: int,
    poll_interval: float = _DEFAULT_POLL_INTERVAL_SECONDS,
) -> dict[str, Any]:
    """Same shape as wait_for_pool_terminal_state, parameterized for
    desktop state. Used by power transitions and rebuild.

    Kept as a separate function from the pool waiter for clarity;
    if a third resource gains polling needs, M6+ can merge them
    behind a `resource_type` parameter.
    """
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    last_state: dict[str, Any] = await fetch()

    if is_terminal(last_state):
        return last_state

    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(poll_interval)
        last_state = await fetch()
        if is_terminal(last_state):
            return last_state

    logger.warning(
        "wait_for_desktop_terminal_state timed out after %ds; "
        "returning last observed state",
        timeout_seconds,
    )
    return last_state


def desktop_power_terminal(
    target_power_state: str,
) -> Callable[[dict[str, Any]], bool]:
    """Factory: returns a predicate that's True when the desktop's
    `power_state` matches the target.

    Different power actions converge on different terminal states
    (start/reboot → 'running', stop/shutdown → 'stopped'); the
    factory parameterizes the predicate per call.

    Reads `power_state` (the row's last-known state). The broker's
    GET endpoint opportunistically reconciles this from the live
    provider read on each call, so the value is fresh.
    """

    def _predicate(state: dict[str, Any]) -> bool:
        return state.get("power_state") == target_power_state

    return _predicate


def desktop_rebuild_terminal(state: dict[str, Any]) -> bool:
    """True when a rebuilt desktop is back to operational state.

    Rebuild flow: stop → destroy → re-clone → start. Terminal when
    `status == 'available'` AND `power_state == 'running'`.

    `status == 'error'` is intentionally NOT terminal — the polling
    helper's timeout will fire and the agent sees the error state in
    the returned snapshot. The broker may retry; T6's "return last
    state" semantics let the agent decide what to do.
    """
    return (
        state.get("status") == "available"
        and state.get("power_state") == "running"
    )
