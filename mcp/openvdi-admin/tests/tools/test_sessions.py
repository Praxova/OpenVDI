"""Session tool tests."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from openvdi_admin.errors import BrokerError
from openvdi_admin.tools import sessions


@pytest.fixture
def mock_client(monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr(
        "openvdi_admin.tools._common.get_broker_client",
        lambda: client,
    )
    monkeypatch.setattr(
        "openvdi_admin.tools.sessions.get_broker_client",
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


class TestListSessions:
    async def test_default_pagination(self, mock_client):
        mock_client.get.return_value = []
        await sessions.openvdi_list_sessions()
        params = mock_client.get.call_args.kwargs["params"]
        assert params == {"limit": 50, "offset": 0}

    async def test_filters_passed_through(self, mock_client):
        mock_client.get.return_value = []
        await sessions.openvdi_list_sessions(
            pool_id="p1",
            desktop_id="d1",
            username="alice",
            status="active",
            limit=20,
            offset=10,
        )
        params = mock_client.get.call_args.kwargs["params"]
        assert params["pool_id"] == "p1"
        assert params["desktop_id"] == "d1"
        assert params["username"] == "alice"
        assert params["status"] == "active"
        assert params["limit"] == 20
        assert params["offset"] == 10


class TestGetSession:
    async def test_uses_path_param(self, mock_client):
        mock_client.get.return_value = {"id": "s1"}
        result = await sessions.openvdi_get_session("s1")
        assert result["id"] == "s1"
        mock_client.get.assert_called_once_with("/api/v1/sessions/s1")


class TestForceDisconnectSession:
    async def test_dry_run_shape(self, mock_client, writable):
        mock_client.get.return_value = {
            "id": "s1",
            "username": "alice",
            "desktop_id": "d1",
            "desktop_name": "ENG-001",
            "status": "active",
            "connected_at": "2026-04-01T10:00:00Z",
        }
        result = await sessions.openvdi_force_disconnect_session(
            session_id="s1", confirm=False,
        )
        assert result["dry_run"] is True
        assert result["action"] == "force_disconnect_session"
        assert result["target"]["username"] == "alice"
        assert result["target"]["desktop_name"] == "ENG-001"
        assert result["target"]["connected_at"] == "2026-04-01T10:00:00Z"
        mock_client.delete.assert_not_called()

    async def test_confirm_executes_delete(
        self, mock_client, writable,
    ):
        mock_client.delete.return_value = None
        result = await sessions.openvdi_force_disconnect_session(
            session_id="s1", confirm=True,
        )
        assert result is None
        mock_client.delete.assert_called_once_with(
            "/api/v1/sessions/s1",
        )
        mock_client.get.assert_not_called()

    async def test_blocked_in_read_only_mode(
        self, mock_client, read_only,
    ):
        with pytest.raises(BrokerError) as exc:
            await sessions.openvdi_force_disconnect_session(
                session_id="s1",
            )
        assert exc.value.code == "READ_ONLY_MODE"
