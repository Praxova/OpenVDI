"""Desktop tool tests."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from openvdi_admin.errors import BrokerError
from openvdi_admin.tools import desktops


@pytest.fixture
def mock_client(monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr(
        "openvdi_admin.tools._common.get_broker_client",
        lambda: client,
    )
    monkeypatch.setattr(
        "openvdi_admin.tools.desktops.get_broker_client",
        lambda: client,
    )
    return client


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


@pytest.fixture
def stub_polling(monkeypatch):
    """Replace `wait_for_desktop_terminal_state` so power/rebuild
    tests don't exercise the polling helper — that has its own
    dedicated test file."""
    waiter = AsyncMock()
    monkeypatch.setattr(
        "openvdi_admin.tools.desktops.wait_for_desktop_terminal_state",
        waiter,
    )
    return waiter


class TestListDesktops:
    async def test_default_pagination(self, mock_client):
        mock_client.get.return_value = []
        await desktops.openvdi_list_desktops()
        params = mock_client.get.call_args.kwargs["params"]
        assert params == {"limit": 50, "offset": 0}

    async def test_filters_passed_through(self, mock_client):
        mock_client.get.return_value = []
        await desktops.openvdi_list_desktops(
            pool_id="p1",
            status="available",
            assigned_user="alice",
            limit=10,
            offset=5,
        )
        params = mock_client.get.call_args.kwargs["params"]
        assert params["pool_id"] == "p1"
        assert params["status"] == "available"
        assert params["assigned_user"] == "alice"
        assert params["limit"] == 10
        assert params["offset"] == 5


class TestGetDesktop:
    async def test_uses_path_param(self, mock_client):
        mock_client.get.return_value = {"id": "d1"}
        result = await desktops.openvdi_get_desktop("d1")
        assert result["id"] == "d1"
        mock_client.get.assert_called_once_with("/api/v1/desktops/d1")


class TestAssignDesktop:
    async def test_dry_run_unassigned_directly_entitled(
        self, mock_client, writable,
    ):
        mock_client.get.side_effect = [
            {
                "id": "d1",
                "name": "ENG-001",
                "pool_id": "p1",
                "assigned_user": None,
            },
            [
                {
                    "id": "e1",
                    "principal_type": "user",
                    "principal_name": "alice",
                },
            ],
        ]
        result = await desktops.openvdi_assign_desktop(
            desktop_id="d1", username="alice", confirm=False,
        )
        assert result["dry_run"] is True
        assert result["target"]["current_assigned_user"] is None
        assert result["target"]["new_assigned_user"] == "alice"
        assert result["user_directly_entitled"] is True
        assert result["user_may_be_group_entitled"] is None
        mock_client.post.assert_not_called()

    async def test_dry_run_replaces_existing_assignment(
        self, mock_client, writable,
    ):
        mock_client.get.side_effect = [
            {
                "id": "d1",
                "name": "ENG-001",
                "pool_id": "p1",
                "assigned_user": "bob",
            },
            [],  # no entitlements
        ]
        result = await desktops.openvdi_assign_desktop(
            desktop_id="d1", username="alice", confirm=False,
        )
        assert result["target"]["current_assigned_user"] == "bob"
        assert result["target"]["new_assigned_user"] == "alice"

    async def test_dry_run_flags_un_directly_entitled(
        self, mock_client, writable,
    ):
        mock_client.get.side_effect = [
            {"id": "d1", "name": "ENG-001", "pool_id": "p1"},
            [
                {
                    "id": "e1",
                    "principal_type": "group",
                    "principal_name": "VDI-Engineering",
                },
            ],
        ]
        result = await desktops.openvdi_assign_desktop(
            desktop_id="d1", username="alice", confirm=False,
        )
        assert result["user_directly_entitled"] is False
        assert result["user_may_be_group_entitled"] is True

    async def test_dry_run_user_match_is_case_insensitive(
        self, mock_client, writable,
    ):
        mock_client.get.side_effect = [
            {"id": "d1", "name": "ENG-001", "pool_id": "p1"},
            [
                {
                    "id": "e1",
                    "principal_type": "user",
                    "principal_name": "Alice",
                },
            ],
        ]
        result = await desktops.openvdi_assign_desktop(
            desktop_id="d1", username="ALICE", confirm=False,
        )
        assert result["user_directly_entitled"] is True

    async def test_confirm_posts_assign(
        self, mock_client, writable,
    ):
        mock_client.post.return_value = {
            "id": "d1", "assigned_user": "alice",
        }
        result = await desktops.openvdi_assign_desktop(
            desktop_id="d1", username="alice", confirm=True,
        )
        assert result["assigned_user"] == "alice"
        post_call = mock_client.post.call_args
        assert post_call.args[0] == "/api/v1/desktops/d1/assign"
        # Broker derives assignment_type from pool_type — we only
        # send username.
        assert post_call.kwargs["body"] == {"username": "alice"}

    async def test_blocked_in_read_only_mode(
        self, mock_client, read_only,
    ):
        with pytest.raises(BrokerError) as exc:
            await desktops.openvdi_assign_desktop(
                desktop_id="d1", username="alice",
            )
        assert exc.value.code == "READ_ONLY_MODE"


class TestUnassignDesktop:
    async def test_dry_run_no_active_session(
        self, mock_client, writable,
    ):
        mock_client.get.side_effect = [
            {"id": "d1", "name": "ENG-001", "assigned_user": "alice"},
            [],  # no active sessions
        ]
        result = await desktops.openvdi_unassign_desktop(
            desktop_id="d1", confirm=False,
        )
        assert result["blocked_by"] is None
        assert result["target"]["current_assigned_user"] == "alice"

    async def test_dry_run_active_session_blocks(
        self, mock_client, writable,
    ):
        mock_client.get.side_effect = [
            {"id": "d1", "name": "ENG-001", "assigned_user": "alice"},
            [{"id": "s1", "username": "alice"}],
        ]
        result = await desktops.openvdi_unassign_desktop(
            desktop_id="d1", confirm=False,
        )
        assert result["blocked_by"] is not None
        assert len(result["blocked_by"]["active_sessions"]) == 1

    async def test_confirm_posts_unassign(
        self, mock_client, writable,
    ):
        mock_client.post.return_value = {"id": "d1"}
        result = await desktops.openvdi_unassign_desktop(
            desktop_id="d1", confirm=True,
        )
        assert result["id"] == "d1"
        mock_client.post.assert_called_once_with(
            "/api/v1/desktops/d1/unassign",
        )
        mock_client.get.assert_not_called()

    async def test_blocked_in_read_only_mode(
        self, mock_client, read_only,
    ):
        with pytest.raises(BrokerError) as exc:
            await desktops.openvdi_unassign_desktop(desktop_id="d1")
        assert exc.value.code == "READ_ONLY_MODE"


class TestRebuildDesktop:
    async def test_dry_run_no_active_session(
        self, mock_client, writable,
    ):
        mock_client.get.side_effect = [
            {
                "id": "d1",
                "name": "ENG-001",
                "pool_id": "p1",
                "assigned_user": "alice",
            },
            [],
        ]
        result = await desktops.openvdi_rebuild_desktop(
            desktop_id="d1", confirm=False,
        )
        assert result["dry_run"] is True
        assert result["target"]["assigned_user"] == "alice"
        assert result["blocked_by"] is None
        assert "destructive" in result
        mock_client.post.assert_not_called()

    async def test_dry_run_active_session_blocks(
        self, mock_client, writable,
    ):
        mock_client.get.side_effect = [
            {
                "id": "d1",
                "name": "ENG-001",
                "pool_id": "p1",
                "assigned_user": "alice",
            },
            [{"id": "s1", "username": "alice"}],
        ]
        result = await desktops.openvdi_rebuild_desktop(
            desktop_id="d1", confirm=False,
        )
        assert result["blocked_by"] is not None
        assert len(result["blocked_by"]["active_sessions"]) == 1

    async def test_confirm_posts_then_polls(
        self, mock_client, writable, stub_polling,
    ):
        final_state = {
            "id": "d1", "status": "available", "power_state": "running",
        }
        stub_polling.return_value = final_state

        result = await desktops.openvdi_rebuild_desktop(
            desktop_id="d1", confirm=True,
        )
        assert result == final_state
        mock_client.post.assert_called_once_with(
            "/api/v1/desktops/d1/rebuild",
        )
        kwargs = stub_polling.call_args.kwargs
        assert kwargs["is_terminal"] is desktops.desktop_rebuild_terminal
        assert kwargs["timeout_seconds"] == 600

    async def test_custom_timeout_forwarded(
        self, mock_client, writable, stub_polling,
    ):
        stub_polling.return_value = {}
        await desktops.openvdi_rebuild_desktop(
            desktop_id="d1", confirm=True, timeout_seconds=120,
        )
        assert (
            stub_polling.call_args.kwargs["timeout_seconds"] == 120
        )

    async def test_blocked_in_read_only_mode(
        self, mock_client, read_only,
    ):
        with pytest.raises(BrokerError) as exc:
            await desktops.openvdi_rebuild_desktop(desktop_id="d1")
        assert exc.value.code == "READ_ONLY_MODE"


class TestPowerDesktop:
    async def test_invalid_action_raises_invalid_request(
        self, mock_client, writable,
    ):
        with pytest.raises(BrokerError) as exc:
            await desktops.openvdi_power_desktop(
                desktop_id="d1", action="hibernate",
            )
        assert exc.value.code == "INVALID_REQUEST"
        # Validator rejects before any network call.
        mock_client.post.assert_not_called()
        mock_client.get.assert_not_called()

    async def test_start_executes_without_dry_run(
        self, mock_client, writable, stub_polling,
    ):
        # start ignores confirm; should POST + poll directly.
        stub_polling.return_value = {"power_state": "running"}
        result = await desktops.openvdi_power_desktop(
            desktop_id="d1", action="start",
        )
        assert result == {"power_state": "running"}
        mock_client.post.assert_called_once_with(
            "/api/v1/desktops/d1/power/start",
        )

    async def test_reboot_executes_without_dry_run(
        self, mock_client, writable, stub_polling,
    ):
        stub_polling.return_value = {"power_state": "running"}
        await desktops.openvdi_power_desktop(
            desktop_id="d1", action="reboot",
        )
        mock_client.post.assert_called_once_with(
            "/api/v1/desktops/d1/power/reboot",
        )

    async def test_stop_dry_run_shows_active_session(
        self, mock_client, writable,
    ):
        mock_client.get.side_effect = [
            {
                "id": "d1",
                "name": "ENG-001",
                "power_state": "running",
                "assigned_user": "alice",
            },
            [{"id": "s1", "username": "alice"}],
        ]
        result = await desktops.openvdi_power_desktop(
            desktop_id="d1", action="stop", confirm=False,
        )
        assert result["dry_run"] is True
        assert result["action"] == "stop_desktop"
        assert result["target"]["current_power_state"] == "running"
        assert result["active_session"] == {
            "id": "s1", "username": "alice",
        }
        mock_client.post.assert_not_called()

    async def test_shutdown_dry_run_shape(
        self, mock_client, writable,
    ):
        mock_client.get.side_effect = [
            {
                "id": "d1", "name": "ENG-001",
                "power_state": "running",
            },
            [],  # no active session
        ]
        result = await desktops.openvdi_power_desktop(
            desktop_id="d1", action="shutdown", confirm=False,
        )
        assert result["action"] == "shutdown_desktop"
        assert result["active_session"] is None

    async def test_stop_confirm_executes(
        self, mock_client, writable, stub_polling,
    ):
        stub_polling.return_value = {"power_state": "stopped"}
        result = await desktops.openvdi_power_desktop(
            desktop_id="d1", action="stop", confirm=True,
        )
        assert result == {"power_state": "stopped"}
        mock_client.post.assert_called_once_with(
            "/api/v1/desktops/d1/power/stop",
        )
        # Polling targets `stopped` for stop actions.
        is_terminal = stub_polling.call_args.kwargs["is_terminal"]
        assert is_terminal({"power_state": "stopped"}) is True
        assert is_terminal({"power_state": "running"}) is False

    async def test_start_polling_targets_running(
        self, mock_client, writable, stub_polling,
    ):
        stub_polling.return_value = {}
        await desktops.openvdi_power_desktop(
            desktop_id="d1", action="start",
        )
        is_terminal = stub_polling.call_args.kwargs["is_terminal"]
        assert is_terminal({"power_state": "running"}) is True
        assert is_terminal({"power_state": "stopped"}) is False

    async def test_default_timeout_is_30(
        self, mock_client, writable, stub_polling,
    ):
        stub_polling.return_value = {}
        await desktops.openvdi_power_desktop(
            desktop_id="d1", action="start",
        )
        assert stub_polling.call_args.kwargs["timeout_seconds"] == 30

    async def test_blocked_in_read_only_mode(
        self, mock_client, read_only,
    ):
        with pytest.raises(BrokerError) as exc:
            await desktops.openvdi_power_desktop(
                desktop_id="d1", action="start",
            )
        assert exc.value.code == "READ_ONLY_MODE"


class TestDeleteDesktop:
    async def test_dry_run_shape(self, mock_client, writable):
        mock_client.get.side_effect = [
            {
                "id": "d1",
                "name": "ENG-001",
                "pool_id": "p1",
                "assigned_user": "alice",
            },
            [{"id": "s1", "username": "alice"}],
        ]
        result = await desktops.openvdi_delete_desktop(
            desktop_id="d1", confirm=False,
        )
        assert result["action"] == "delete_desktop"
        assert result["target"]["assigned_user"] == "alice"
        assert result["active_session"] == {
            "id": "s1", "username": "alice",
        }

    async def test_confirm_executes_delete(
        self, mock_client, writable,
    ):
        mock_client.delete.return_value = {
            "desktop_id": "d1", "action": "destroy",
        }
        result = await desktops.openvdi_delete_desktop(
            desktop_id="d1", confirm=True,
        )
        assert result["action"] == "destroy"
        mock_client.delete.assert_called_once_with(
            "/api/v1/desktops/d1",
        )
        mock_client.get.assert_not_called()

    async def test_blocked_in_read_only_mode(
        self, mock_client, read_only,
    ):
        with pytest.raises(BrokerError) as exc:
            await desktops.openvdi_delete_desktop(desktop_id="d1")
        assert exc.value.code == "READ_ONLY_MODE"
