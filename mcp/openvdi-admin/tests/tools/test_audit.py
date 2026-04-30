"""Audit tool tests."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from openvdi_admin.tools import audit


@pytest.fixture
def mock_client(monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr(
        "openvdi_admin.tools._common.get_broker_client",
        lambda: client,
    )
    monkeypatch.setattr(
        "openvdi_admin.tools.audit.get_broker_client",
        lambda: client,
    )
    return client


class TestQueryAudit:
    async def test_default_pagination_only(self, mock_client):
        mock_client.get.return_value = []
        await audit.openvdi_query_audit()
        call = mock_client.get.call_args
        assert call.args[0] == "/api/v1/audit"
        params = call.kwargs["params"]
        assert params == {"limit": 100, "offset": 0}

    async def test_all_filters_passed_through(self, mock_client):
        mock_client.get.return_value = []
        await audit.openvdi_query_audit(
            actor="alice",
            action="broker.connect",
            resource_type="pool",
            resource_id="p1",
            since="2026-04-01T00:00:00Z",
            until="2026-04-30T23:59:59Z",
            limit=200,
            offset=50,
        )
        params = mock_client.get.call_args.kwargs["params"]
        assert params["actor"] == "alice"
        assert params["action"] == "broker.connect"
        assert params["resource_type"] == "pool"
        assert params["resource_id"] == "p1"
        assert params["since"] == "2026-04-01T00:00:00Z"
        assert params["until"] == "2026-04-30T23:59:59Z"
        assert params["limit"] == 200
        assert params["offset"] == 50

    async def test_iso8601_strings_pass_through_verbatim(
        self, mock_client,
    ):
        # MCP doesn't parse — broker validates. A nonsense value
        # round-trips unchanged so the broker's INVALID_REQUEST
        # surfaces with the original string.
        mock_client.get.return_value = []
        await audit.openvdi_query_audit(since="not-a-date")
        params = mock_client.get.call_args.kwargs["params"]
        assert params["since"] == "not-a-date"

    async def test_action_wildcard_passed_through(self, mock_client):
        # Trailing-`*` prefix is a broker feature; MCP just forwards.
        mock_client.get.return_value = []
        await audit.openvdi_query_audit(action="broker.*")
        params = mock_client.get.call_args.kwargs["params"]
        assert params["action"] == "broker.*"

    async def test_returns_broker_payload_as_is(self, mock_client):
        rows = [
            {"id": "a1", "actor": "alice", "action": "broker.connect"},
            {"id": "a2", "actor": "bob", "action": "broker.session.end"},
        ]
        mock_client.get.return_value = rows
        result = await audit.openvdi_query_audit()
        assert result == rows
