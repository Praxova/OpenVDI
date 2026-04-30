"""Dashboard tool tests."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from openvdi_admin.tools import dashboard


@pytest.fixture
def mock_client(monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr(
        "openvdi_admin.tools._common.get_broker_client",
        lambda: client,
    )
    monkeypatch.setattr(
        "openvdi_admin.tools.dashboard.get_broker_client",
        lambda: client,
    )
    return client


class TestDashboardSummary:
    async def test_forwards_to_summary_endpoint(self, mock_client):
        payload = {
            "clusters": {"total": 2, "by_status": {"active": 2}},
            "pools": {"total": 3},
        }
        mock_client.get.return_value = payload
        result = await dashboard.openvdi_get_dashboard_summary()
        assert result == payload
        mock_client.get.assert_called_once_with(
            "/api/v1/dashboard/summary",
        )


class TestDashboardCapacity:
    async def test_forwards_to_capacity_endpoint(self, mock_client):
        rows = [
            {"pool_id": "p1", "total_desktops": 10, "available": 3},
            {"pool_id": "p2", "total_desktops": 5, "available": 1},
        ]
        mock_client.get.return_value = rows
        result = await dashboard.openvdi_get_dashboard_capacity()
        assert result == rows
        mock_client.get.assert_called_once_with(
            "/api/v1/dashboard/capacity",
        )
