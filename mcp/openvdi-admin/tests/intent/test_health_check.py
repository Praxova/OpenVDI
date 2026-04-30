"""Tests for intent/health_check.py."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from openvdi_admin.errors import BrokerError
from openvdi_admin.intent import health_check


@pytest.fixture
def mock_client(monkeypatch):
    """Replace the broker client with an AsyncMock so we can script
    /health responses (via get_raw) without a live broker."""
    client = AsyncMock()
    monkeypatch.setattr(
        "openvdi_admin.intent.health_check.get_broker_client",
        lambda: client,
    )
    return client


@pytest.fixture
def mock_list_clusters(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(
        "openvdi_admin.intent.health_check.openvdi_list_clusters",
        mock,
    )
    return mock


class TestHappyPath:
    async def test_broker_reachable_all_clusters_active(
        self, mock_client, mock_list_clusters,
    ):
        mock_client.get_raw.return_value = {
            "status": "ok", "version": "0.5.0",
        }
        mock_list_clusters.return_value = [
            {"id": "c1", "name": "default", "status": "active"},
            {"id": "c2", "name": "secondary", "status": "active"},
        ]
        result = await health_check.openvdi_health_check()
        assert result["ok"] is True
        body = result["result"]
        assert body["broker_reachable"] is True
        assert body["broker_version"] == "0.5.0"
        assert len(body["clusters"]) == 2
        assert body["summary"] == "broker reachable; all 2 clusters active"
        # Confirm /health was hit via get_raw, not the unwrap path.
        mock_client.get_raw.assert_called_once_with("/health")

    async def test_single_cluster_summary(
        self, mock_client, mock_list_clusters,
    ):
        mock_client.get_raw.return_value = {"status": "ok"}
        mock_list_clusters.return_value = [
            {"id": "c1", "name": "default", "status": "active"},
        ]
        result = await health_check.openvdi_health_check()
        # Singular form: "1 cluster" not "1 clusters".
        assert (
            result["result"]["summary"]
            == "broker reachable; all 1 cluster active"
        )


class TestPartialClusterAvailability:
    async def test_one_offline_cluster_in_summary(
        self, mock_client, mock_list_clusters,
    ):
        mock_client.get_raw.return_value = {"status": "ok"}
        mock_list_clusters.return_value = [
            {"id": "c1", "name": "default", "status": "active"},
            {"id": "c2", "name": "secondary", "status": "offline"},
        ]
        result = await health_check.openvdi_health_check()
        assert result["ok"] is True
        # Broker still reachable — cluster issue is reported in summary.
        assert result["result"]["broker_reachable"] is True
        assert (
            result["result"]["summary"]
            == "broker reachable; 1 of 2 clusters active"
        )


class TestNoClusters:
    async def test_no_clusters_registered(
        self, mock_client, mock_list_clusters,
    ):
        mock_client.get_raw.return_value = {"status": "ok"}
        mock_list_clusters.return_value = []
        result = await health_check.openvdi_health_check()
        assert result["ok"] is True
        assert result["result"]["clusters"] == []
        assert (
            result["result"]["summary"]
            == "broker reachable; no clusters registered"
        )


class TestVersionField:
    async def test_version_null_when_health_lacks_field(
        self, mock_client, mock_list_clusters,
    ):
        mock_client.get_raw.return_value = {"status": "ok"}
        mock_list_clusters.return_value = []
        result = await health_check.openvdi_health_check()
        assert result["result"]["broker_version"] is None

    async def test_version_null_when_health_returns_non_dict(
        self, mock_client, mock_list_clusters,
    ):
        # Defensive — broker shouldn't return a non-dict, but guard
        # against it not crashing the health check.
        mock_client.get_raw.return_value = "ok"
        mock_list_clusters.return_value = []
        result = await health_check.openvdi_health_check()
        assert result["result"]["broker_version"] is None


class TestFailurePaths:
    async def test_health_endpoint_failure(
        self, mock_client, mock_list_clusters,
    ):
        mock_client.get_raw.side_effect = BrokerError(
            http_status=0,
            code="TRANSPORT_ERROR",
            message="connection refused",
        )
        result = await health_check.openvdi_health_check()
        assert result["ok"] is False
        assert result["error_code"] == "TRANSPORT_ERROR"
        assert result["failed_at_step"] == "ping_broker"
        # list_clusters should NOT have been called once /health failed.
        mock_list_clusters.assert_not_called()

    async def test_list_clusters_failure_after_health_succeeds(
        self, mock_client, mock_list_clusters,
    ):
        mock_client.get_raw.return_value = {"status": "ok"}
        mock_list_clusters.side_effect = BrokerError(
            http_status=500,
            code="INTERNAL_ERROR",
            message="db down",
        )
        result = await health_check.openvdi_health_check()
        assert result["ok"] is False
        assert result["error_code"] == "INTERNAL_ERROR"
        assert result["failed_at_step"] == "list_clusters"


class TestSummaryFormatting:
    async def test_two_active_clusters_plural(
        self, mock_client, mock_list_clusters,
    ):
        mock_client.get_raw.return_value = {"status": "ok"}
        mock_list_clusters.return_value = [
            {"id": "c1", "name": "default", "status": "active"},
            {"id": "c2", "name": "two", "status": "active"},
        ]
        result = await health_check.openvdi_health_check()
        assert "all 2 clusters active" in result["result"]["summary"]
