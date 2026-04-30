"""User-diagnostic tool tests."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from openvdi_admin.tools import user_diagnostics


@pytest.fixture
def mock_client(monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr(
        "openvdi_admin.tools._common.get_broker_client",
        lambda: client,
    )
    monkeypatch.setattr(
        "openvdi_admin.tools.user_diagnostics.get_broker_client",
        lambda: client,
    )
    return client


class TestListUserDesktops:
    async def test_uses_path_param(self, mock_client):
        mock_client.get.return_value = []
        await user_diagnostics.openvdi_list_user_desktops("alice")
        mock_client.get.assert_called_once_with(
            "/api/v1/admin/users/alice/desktops",
        )

    async def test_passes_username_verbatim_no_lowercase(
        self, mock_client,
    ):
        # Broker canonicalizes to lowercase; the MCP forwards
        # whatever case the agent sent.
        mock_client.get.return_value = []
        await user_diagnostics.openvdi_list_user_desktops("ALICE")
        mock_client.get.assert_called_once_with(
            "/api/v1/admin/users/ALICE/desktops",
        )

    async def test_returns_empty_list_passes_through(
        self, mock_client,
    ):
        mock_client.get.return_value = []
        result = await user_diagnostics.openvdi_list_user_desktops(
            "ghost",
        )
        assert result == []

    async def test_returns_pools_with_assignments(
        self, mock_client,
    ):
        rows = [
            {
                "pool_id": "p1",
                "pool_name": "engineering",
                "assignment": {"desktop_id": "d1"},
            },
            {
                "pool_id": "p2",
                "pool_name": "kiosk",
                "assignment": None,
            },
        ]
        mock_client.get.return_value = rows
        result = await user_diagnostics.openvdi_list_user_desktops(
            "alice",
        )
        assert result == rows


class TestListUserSessions:
    async def test_default_excludes_ended(self, mock_client):
        mock_client.get.return_value = []
        await user_diagnostics.openvdi_list_user_sessions("alice")
        call = mock_client.get.call_args
        assert call.args[0] == "/api/v1/admin/users/alice/sessions"
        params = call.kwargs["params"]
        assert params["include_ended"] is False
        assert params["limit"] == 50

    async def test_include_ended_passed_through(self, mock_client):
        mock_client.get.return_value = []
        await user_diagnostics.openvdi_list_user_sessions(
            "alice", include_ended=True,
        )
        params = mock_client.get.call_args.kwargs["params"]
        assert params["include_ended"] is True

    async def test_custom_limit_passed_through(self, mock_client):
        mock_client.get.return_value = []
        await user_diagnostics.openvdi_list_user_sessions(
            "alice", limit=200,
        )
        params = mock_client.get.call_args.kwargs["params"]
        assert params["limit"] == 200

    async def test_orphan_sessions_pass_through_unchanged(
        self, mock_client,
    ):
        # Broker returns orphan sessions with desktop_id / desktop_name
        # set to None; MCP passes them through.
        rows = [
            {
                "id": "s1",
                "username": "alice",
                "desktop_id": None,
                "desktop_name": None,
                "status": "ended",
            },
        ]
        mock_client.get.return_value = rows
        result = await user_diagnostics.openvdi_list_user_sessions(
            "alice", include_ended=True,
        )
        assert result == rows
