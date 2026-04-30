"""Tests for the polling helpers in tools/_polling.py."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from openvdi_admin.errors import BrokerError
from openvdi_admin.tools import _polling
from openvdi_admin.tools._polling import (
    desktop_power_terminal,
    desktop_rebuild_terminal,
    pool_drain_terminal,
    pool_provision_terminal,
    wait_for_desktop_terminal_state,
    wait_for_pool_terminal_state,
)


class TestPoolProvisionTerminal:
    def test_zero_provisioning_zero_deleting_is_terminal(self):
        state = {
            "capacity": {
                "provisioning": 0, "deleting": 0,
                "available": 5, "assigned": 3,
            },
        }
        assert pool_provision_terminal(state) is True

    def test_one_provisioning_is_not_terminal(self):
        state = {"capacity": {"provisioning": 1, "deleting": 0}}
        assert pool_provision_terminal(state) is False

    def test_one_deleting_is_not_terminal(self):
        state = {"capacity": {"provisioning": 0, "deleting": 1}}
        assert pool_provision_terminal(state) is False

    def test_provisioning_and_deleting_both_count(self):
        state = {"capacity": {"provisioning": 2, "deleting": 3}}
        assert pool_provision_terminal(state) is False

    def test_missing_capacity_treated_as_terminal(self):
        # Defensive — malformed responses must not infinite-loop.
        assert pool_provision_terminal({}) is True

    def test_null_capacity_treated_as_terminal(self):
        assert pool_provision_terminal({"capacity": None}) is True


class TestPoolDrainTerminal:
    def test_zero_active_sessions_is_terminal(self):
        state = {"status": "draining", "_active_session_count": 0}
        assert pool_drain_terminal(state) is True

    def test_active_sessions_remaining_not_terminal(self):
        state = {"status": "draining", "_active_session_count": 3}
        assert pool_drain_terminal(state) is False

    def test_missing_count_defaults_to_terminal(self):
        # When the composed fetch fails to attach the count, fall
        # back to terminal so we don't loop forever — agent will
        # see status='draining' on the returned record.
        assert pool_drain_terminal({"status": "draining"}) is True


class TestWaitForPoolTerminalState:
    async def test_immediately_terminal(self):
        state = {"capacity": {"provisioning": 0, "deleting": 0}}
        fetch = AsyncMock(return_value=state)
        result = await wait_for_pool_terminal_state(
            fetch=fetch,
            is_terminal=pool_provision_terminal,
            timeout_seconds=10,
        )
        assert result == state
        fetch.assert_awaited_once()

    async def test_polls_until_terminal(self, monkeypatch):
        states = [
            {"capacity": {"provisioning": 2, "deleting": 0}},
            {"capacity": {"provisioning": 1, "deleting": 0}},
            {"capacity": {"provisioning": 0, "deleting": 0}},
        ]
        fetch = AsyncMock(side_effect=states)
        monkeypatch.setattr(_polling.asyncio, "sleep", AsyncMock())

        result = await wait_for_pool_terminal_state(
            fetch=fetch,
            is_terminal=pool_provision_terminal,
            timeout_seconds=60,
        )
        assert result["capacity"]["provisioning"] == 0
        assert fetch.await_count == 3

    async def test_timeout_returns_last_state(self, monkeypatch):
        # Always returns "still provisioning" — timeout fires and
        # the last state is returned without raising.
        last_state = {"capacity": {"provisioning": 1, "deleting": 0}}
        fetch = AsyncMock(return_value=last_state)
        monkeypatch.setattr(_polling.asyncio, "sleep", AsyncMock())

        result = await wait_for_pool_terminal_state(
            fetch=fetch,
            is_terminal=pool_provision_terminal,
            timeout_seconds=0,
            poll_interval=0.001,
        )
        assert result == last_state

    async def test_fetch_error_propagates(self):
        fetch = AsyncMock(
            side_effect=BrokerError(
                http_status=503,
                code="SERVICE_UNAVAILABLE",
                message="broker down",
            ),
        )
        with pytest.raises(BrokerError):
            await wait_for_pool_terminal_state(
                fetch=fetch,
                is_terminal=lambda _: True,
                timeout_seconds=10,
            )


class TestDesktopPowerTerminal:
    def test_factory_returns_callable(self):
        is_running = desktop_power_terminal("running")
        assert callable(is_running)

    def test_predicate_true_when_state_matches(self):
        is_running = desktop_power_terminal("running")
        assert is_running({"power_state": "running"}) is True

    def test_predicate_false_when_state_differs(self):
        is_running = desktop_power_terminal("running")
        assert is_running({"power_state": "stopped"}) is False

    def test_predicate_false_when_state_missing(self):
        is_running = desktop_power_terminal("running")
        assert is_running({}) is False

    def test_factory_targets_stopped_for_stop_actions(self):
        is_stopped = desktop_power_terminal("stopped")
        assert is_stopped({"power_state": "stopped"}) is True
        assert is_stopped({"power_state": "running"}) is False


class TestDesktopRebuildTerminal:
    def test_terminal_when_available_and_running(self):
        assert desktop_rebuild_terminal({
            "status": "available",
            "power_state": "running",
        }) is True

    def test_not_terminal_when_provisioning(self):
        assert desktop_rebuild_terminal({
            "status": "provisioning",
            "power_state": "stopped",
        }) is False

    def test_not_terminal_when_running_but_not_available(self):
        assert desktop_rebuild_terminal({
            "status": "deleting",
            "power_state": "running",
        }) is False

    def test_not_terminal_when_available_but_not_running(self):
        assert desktop_rebuild_terminal({
            "status": "available",
            "power_state": "stopped",
        }) is False

    def test_not_terminal_on_error(self):
        # Error is intentionally non-terminal — the timeout returns
        # the last state and the agent decides what to do.
        assert desktop_rebuild_terminal({
            "status": "error",
            "power_state": "stopped",
        }) is False


class TestWaitForDesktopTerminalState:
    async def test_immediately_terminal(self):
        state = {"power_state": "running"}
        fetch = AsyncMock(return_value=state)
        result = await wait_for_desktop_terminal_state(
            fetch=fetch,
            is_terminal=desktop_power_terminal("running"),
            timeout_seconds=10,
        )
        assert result == state
        fetch.assert_awaited_once()

    async def test_polls_until_running(self, monkeypatch):
        states = [
            {"power_state": "stopped"},
            {"power_state": "stopped"},
            {"power_state": "running"},
        ]
        fetch = AsyncMock(side_effect=states)
        monkeypatch.setattr(_polling.asyncio, "sleep", AsyncMock())

        result = await wait_for_desktop_terminal_state(
            fetch=fetch,
            is_terminal=desktop_power_terminal("running"),
            timeout_seconds=60,
        )
        assert result["power_state"] == "running"
        assert fetch.await_count == 3

    async def test_timeout_returns_last_state(self, monkeypatch):
        last_state = {"power_state": "stopped"}
        fetch = AsyncMock(return_value=last_state)
        monkeypatch.setattr(_polling.asyncio, "sleep", AsyncMock())

        result = await wait_for_desktop_terminal_state(
            fetch=fetch,
            is_terminal=desktop_power_terminal("running"),
            timeout_seconds=0,
            poll_interval=0.001,
        )
        assert result == last_state
