"""Tests for the polling helpers in tools/_polling.py."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from openvdi_admin.errors import BrokerError
from openvdi_admin.tools import _polling
from openvdi_admin.tools._polling import (
    pool_drain_terminal,
    pool_provision_terminal,
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
