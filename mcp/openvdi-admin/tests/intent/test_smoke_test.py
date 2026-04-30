"""Tests for intent/smoke_test.py."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from openvdi_admin.errors import BrokerError
from openvdi_admin.intent import smoke_test


@pytest.fixture
def mock_thin_wrappers(monkeypatch):
    """Replace every thin wrapper imported into smoke_test with an
    AsyncMock so we can script returns per test.

    Returns a dict by name so tests can poke .return_value /
    .side_effect on individual wrappers.
    """
    mocks = {
        "openvdi_get_pool": AsyncMock(),
        "openvdi_get_pool_summary": AsyncMock(),
        "openvdi_provision_pool": AsyncMock(),
        "openvdi_get_desktop": AsyncMock(),
        "openvdi_list_desktops": AsyncMock(),
        "openvdi_delete_desktop": AsyncMock(),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(
            f"openvdi_admin.intent.smoke_test.{name}", mock,
        )
    return mocks


@pytest.fixture
def writable(monkeypatch, settings):
    monkeypatch.setattr(
        "openvdi_admin.tools._common.get_settings",
        lambda: settings,
    )


@pytest.fixture
def read_only(monkeypatch, settings):
    ro = settings.model_copy(update={"openvdi_mcp_read_only": True})
    monkeypatch.setattr(
        "openvdi_admin.tools._common.get_settings", lambda: ro,
    )


def _active_pool(pool_id: str = "p1", name: str = "test-eng"):
    return {"id": pool_id, "name": name, "status": "active"}


def _summary_with(available: int):
    return {
        "id": "p1",
        "name": "test-eng",
        "status": "active",
        "capacity": {
            "total": 5,
            "available": available,
            "assigned": 0,
            "connected": 0,
            "provisioning": 0,
            "error": 0,
        },
    }


def _ready_desktop(desktop_id: str = "d1"):
    return {
        "id": desktop_id,
        "name": "ENG-001",
        "status": "available",
        "power_state": "running",
    }


class TestHappyPath:
    async def test_pool_with_available_desktop(
        self, mock_thin_wrappers, writable,
    ):
        mock_thin_wrappers["openvdi_get_pool"].return_value = _active_pool()
        mock_thin_wrappers["openvdi_get_pool_summary"].return_value = (
            _summary_with(available=2)
        )
        mock_thin_wrappers["openvdi_list_desktops"].return_value = [
            _ready_desktop("d1"),
        ]
        mock_thin_wrappers["openvdi_get_desktop"].return_value = (
            _ready_desktop("d1")
        )

        result = await smoke_test.openvdi_smoke_test(pool_id="p1")
        assert result["ok"] is True
        assert result["operation"] == "smoke_test"
        assert result["result"]["pool_id"] == "p1"
        assert result["result"]["verified_desktop_id"] == "d1"
        # Exactly 3 steps: verify_pool_active, query_capacity,
        # verify_desktop. No provision step (available > 0). No
        # cleanup step (cleanup_if_provisioned=False).
        names = [s["name"] for s in result["steps"]]
        assert names == [
            "verify_pool_active",
            "query_capacity",
            "verify_desktop",
        ]
        # Provision wasn't called — pool already had a desktop.
        mock_thin_wrappers["openvdi_provision_pool"].assert_not_called()


class TestPoolNotActive:
    async def test_inactive_pool_fails_at_first_step(
        self, mock_thin_wrappers, writable,
    ):
        mock_thin_wrappers["openvdi_get_pool"].return_value = {
            "id": "p1", "name": "test-eng", "status": "draining",
        }
        result = await smoke_test.openvdi_smoke_test(pool_id="p1")
        assert result["ok"] is False
        assert result["error_code"] == "POOL_INACTIVE"
        assert result["failed_at_step"] == "verify_pool_active"
        # We never made a desktop, so no rollback hint.
        assert result.get("rollback_hint") is None


class TestPoolEmptyNoProvision:
    async def test_empty_with_provision_disabled(
        self, mock_thin_wrappers, writable,
    ):
        mock_thin_wrappers["openvdi_get_pool"].return_value = _active_pool()
        mock_thin_wrappers["openvdi_get_pool_summary"].return_value = (
            _summary_with(available=0)
        )
        result = await smoke_test.openvdi_smoke_test(
            pool_id="p1", provision_if_empty=False,
        )
        assert result["ok"] is False
        assert result["error_code"] == "POOL_EMPTY"
        assert result["failed_at_step"] == "query_capacity"
        mock_thin_wrappers["openvdi_provision_pool"].assert_not_called()


class TestProvisionFlow:
    async def test_empty_pool_provisions_one_and_verifies(
        self, mock_thin_wrappers, writable,
    ):
        mock_thin_wrappers["openvdi_get_pool"].return_value = _active_pool()
        mock_thin_wrappers["openvdi_get_pool_summary"].return_value = (
            _summary_with(available=0)
        )
        # Diff: pre had no desktops, post has one.
        mock_thin_wrappers["openvdi_list_desktops"].side_effect = [
            [],  # pre-provision
            [{"id": "d-new"}],  # post-provision
            [_ready_desktop("d-new")],  # verify_desktop list
        ]
        mock_thin_wrappers["openvdi_provision_pool"].return_value = {
            "id": "p1", "status": "active",
        }
        mock_thin_wrappers["openvdi_get_desktop"].return_value = (
            _ready_desktop("d-new")
        )

        result = await smoke_test.openvdi_smoke_test(
            pool_id="p1", provision_if_empty=True,
        )
        assert result["ok"] is True
        names = [s["name"] for s in result["steps"]]
        assert "provision_one_desktop" in names
        # No cleanup step (cleanup_if_provisioned=False default).
        assert "cleanup_provisioned_desktop" not in names
        mock_thin_wrappers[
            "openvdi_provision_pool"
        ].assert_called_once_with(pool_id="p1", count=1)

    async def test_cleanup_runs_when_requested(
        self, mock_thin_wrappers, writable,
    ):
        mock_thin_wrappers["openvdi_get_pool"].return_value = _active_pool()
        mock_thin_wrappers["openvdi_get_pool_summary"].return_value = (
            _summary_with(available=0)
        )
        mock_thin_wrappers["openvdi_list_desktops"].side_effect = [
            [], [{"id": "d-new"}], [_ready_desktop("d-new")],
        ]
        mock_thin_wrappers["openvdi_provision_pool"].return_value = {}
        mock_thin_wrappers["openvdi_get_desktop"].return_value = (
            _ready_desktop("d-new")
        )

        result = await smoke_test.openvdi_smoke_test(
            pool_id="p1",
            provision_if_empty=True,
            cleanup_if_provisioned=True,
        )
        assert result["ok"] is True
        names = [s["name"] for s in result["steps"]]
        assert "cleanup_provisioned_desktop" in names
        mock_thin_wrappers[
            "openvdi_delete_desktop"
        ].assert_called_once_with("d-new", confirm=True)

    async def test_provision_failure_includes_rollback_hint(
        self, mock_thin_wrappers, writable,
    ):
        mock_thin_wrappers["openvdi_get_pool"].return_value = _active_pool()
        mock_thin_wrappers["openvdi_get_pool_summary"].return_value = (
            _summary_with(available=0)
        )
        mock_thin_wrappers["openvdi_list_desktops"].side_effect = [
            [],
            [{"id": "d-new"}],
            # verify_desktop raises a verification mismatch later;
            # we set up the verify-step list call to return one.
            [{"id": "d-new", "status": "available"}],
        ]
        mock_thin_wrappers["openvdi_provision_pool"].return_value = {}
        # Desktop comes back in `error` state — verify_desktop fails.
        mock_thin_wrappers["openvdi_get_desktop"].return_value = {
            "id": "d-new", "status": "error", "power_state": "stopped",
        }

        result = await smoke_test.openvdi_smoke_test(
            pool_id="p1", provision_if_empty=True,
        )
        assert result["ok"] is False
        assert result["failed_at_step"] == "verify_desktop"
        # We provisioned but cleanup is off — rollback_hint should
        # tell the agent how to clean it up.
        assert result["rollback_hint"] is not None
        assert (
            "d-new"
            in result["rollback_hint"]["suggested_cleanup"]
        )


class TestVerificationFailures:
    async def test_status_mismatch_raises_invalid_request(
        self, mock_thin_wrappers, writable,
    ):
        mock_thin_wrappers["openvdi_get_pool"].return_value = _active_pool()
        mock_thin_wrappers["openvdi_get_pool_summary"].return_value = (
            _summary_with(available=1)
        )
        mock_thin_wrappers["openvdi_list_desktops"].return_value = [
            _ready_desktop("d1"),
        ]
        mock_thin_wrappers["openvdi_get_desktop"].return_value = {
            "id": "d1", "status": "error", "power_state": "running",
        }
        result = await smoke_test.openvdi_smoke_test(pool_id="p1")
        assert result["ok"] is False
        assert result["error_code"] == "INVALID_REQUEST"
        assert "status" in result["error_message"]

    async def test_power_state_mismatch_raises(
        self, mock_thin_wrappers, writable,
    ):
        mock_thin_wrappers["openvdi_get_pool"].return_value = _active_pool()
        mock_thin_wrappers["openvdi_get_pool_summary"].return_value = (
            _summary_with(available=1)
        )
        mock_thin_wrappers["openvdi_list_desktops"].return_value = [
            _ready_desktop("d1"),
        ]
        mock_thin_wrappers["openvdi_get_desktop"].return_value = {
            "id": "d1",
            "status": "available",
            "power_state": "stopped",
        }
        result = await smoke_test.openvdi_smoke_test(pool_id="p1")
        assert result["ok"] is False
        assert "power_state" in result["error_message"]


class TestReadOnly:
    async def test_provision_path_blocked_in_read_only(
        self, mock_thin_wrappers, read_only,
    ):
        with pytest.raises(BrokerError) as exc:
            await smoke_test.openvdi_smoke_test(
                pool_id="p1", provision_if_empty=True,
            )
        assert exc.value.code == "READ_ONLY_MODE"

    async def test_no_provision_path_allowed_in_read_only(
        self, mock_thin_wrappers, read_only,
    ):
        # provision_if_empty=False bypasses the writable gate so a
        # read-only diagnosis works against a populated pool.
        mock_thin_wrappers["openvdi_get_pool"].return_value = _active_pool()
        mock_thin_wrappers["openvdi_get_pool_summary"].return_value = (
            _summary_with(available=1)
        )
        mock_thin_wrappers["openvdi_list_desktops"].return_value = [
            _ready_desktop("d1"),
        ]
        mock_thin_wrappers["openvdi_get_desktop"].return_value = (
            _ready_desktop("d1")
        )
        result = await smoke_test.openvdi_smoke_test(
            pool_id="p1", provision_if_empty=False,
        )
        assert result["ok"] is True
