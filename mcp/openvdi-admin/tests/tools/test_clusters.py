"""Cluster tool tests. Mocks BrokerClient via dependency injection
through the get_broker_client accessor."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from openvdi_admin.errors import BrokerError
from openvdi_admin.tools import clusters


@pytest.fixture
def mock_client(monkeypatch):
    """Patch get_broker_client to return an AsyncMock-backed BrokerClient.

    Patch BOTH the source binding in tools._common AND the re-bound
    name in tools.clusters — `from x import y` creates a new binding
    that monkeypatching the source doesn't reach.
    """
    client = AsyncMock()
    monkeypatch.setattr(
        "openvdi_admin.tools._common.get_broker_client",
        lambda: client,
    )
    monkeypatch.setattr(
        "openvdi_admin.tools.clusters.get_broker_client",
        lambda: client,
    )
    return client


@pytest.fixture
def writable(monkeypatch, settings):
    """Default: read-only mode is OFF for tests that exercise mutations."""
    monkeypatch.setattr(
        "openvdi_admin.tools._common.get_settings",
        lambda: settings,
    )


class TestListClusters:
    async def test_returns_broker_response(self, mock_client):
        mock_client.get.return_value = [
            {"id": "c1", "name": "default", "status": "active"},
        ]
        result = await clusters.openvdi_list_clusters()
        assert len(result) == 1
        assert result[0]["name"] == "default"
        mock_client.get.assert_called_once_with("/api/v1/clusters")


class TestGetCluster:
    async def test_uses_path_param(self, mock_client):
        mock_client.get.return_value = {"id": "c1", "name": "default"}
        result = await clusters.openvdi_get_cluster("c1")
        assert result["id"] == "c1"
        mock_client.get.assert_called_once_with("/api/v1/clusters/c1")


class TestCreateCluster:
    async def test_basic_create(self, mock_client, writable):
        mock_client.post.return_value = {"id": "new-c", "name": "test"}
        result = await clusters.openvdi_create_cluster(
            name="test",
            api_url="https://pve.test:8006",
            token_id="user@pve!t",
            token_secret="secret",
        )
        assert result["id"] == "new-c"
        mock_client.post.assert_called_once()
        call = mock_client.post.call_args
        assert call.args[0] == "/api/v1/clusters"
        body = call.kwargs["body"]
        assert body["name"] == "test"
        assert body["verify_ssl"] is True  # default
        assert "node_filter" not in body  # not passed
        assert body["provider_type"] == "proxmox"

    async def test_optional_node_filter(self, mock_client, writable):
        mock_client.post.return_value = {"id": "new-c"}
        await clusters.openvdi_create_cluster(
            name="test",
            api_url="https://pve.test:8006",
            token_id="user@pve!t",
            token_secret="secret",
            node_filter="pve1,pve2",
        )
        body = mock_client.post.call_args.kwargs["body"]
        assert body["node_filter"] == "pve1,pve2"

    async def test_blocked_in_read_only_mode(
        self, mock_client, monkeypatch, settings,
    ):
        ro = settings.model_copy(update={"openvdi_mcp_read_only": True})
        monkeypatch.setattr(
            "openvdi_admin.tools._common.get_settings", lambda: ro,
        )
        with pytest.raises(BrokerError) as exc:
            await clusters.openvdi_create_cluster(
                name="t",
                api_url="https://pve.test:8006",
                token_id="x",
                token_secret="y",
            )
        assert exc.value.code == "READ_ONLY_MODE"


class TestUpdateCluster:
    async def test_only_passed_fields_in_body(
        self, mock_client, writable,
    ):
        mock_client.put.return_value = {"id": "c1", "name": "renamed"}
        await clusters.openvdi_update_cluster(
            cluster_id="c1",
            name="renamed",
        )
        body = mock_client.put.call_args.kwargs["body"]
        assert body == {"name": "renamed"}

    async def test_node_filter_empty_string_clears(
        self, mock_client, writable,
    ):
        mock_client.put.return_value = {"id": "c1"}
        await clusters.openvdi_update_cluster(
            cluster_id="c1",
            node_filter="",
        )
        body = mock_client.put.call_args.kwargs["body"]
        assert body["node_filter"] is None

    async def test_no_fields_passed_sends_empty_body(
        self, mock_client, writable,
    ):
        """Update with no fields → empty body. The broker no-ops."""
        mock_client.put.return_value = {"id": "c1"}
        await clusters.openvdi_update_cluster(cluster_id="c1")
        body = mock_client.put.call_args.kwargs["body"]
        assert body == {}


class TestDeleteCluster:
    async def test_dry_run_returns_preview_with_blocking_pools(
        self, mock_client, writable,
    ):
        mock_client.get.side_effect = [
            {"id": "c1", "name": "default"},  # cluster lookup
            [{"id": "p1", "name": "engineering"}],  # pools listing
        ]
        result = await clusters.openvdi_delete_cluster(
            cluster_id="c1", confirm=False,
        )
        assert result["dry_run"] is True
        assert result["action"] == "delete_cluster"
        assert result["target"] == {"id": "c1", "name": "default"}
        assert result["blocked_by"]["pools"] == [
            {"id": "p1", "name": "engineering"},
        ]
        # Did NOT execute delete.
        mock_client.delete.assert_not_called()

    async def test_dry_run_with_no_blocking_pools(
        self, mock_client, writable,
    ):
        mock_client.get.side_effect = [
            {"id": "c1", "name": "default"},
            [],
        ]
        result = await clusters.openvdi_delete_cluster(
            cluster_id="c1", confirm=False,
        )
        assert result["blocked_by"] is None

    async def test_confirm_executes_delete(self, mock_client, writable):
        mock_client.delete.return_value = None
        result = await clusters.openvdi_delete_cluster(
            cluster_id="c1", confirm=True,
        )
        assert result is None
        mock_client.delete.assert_called_once_with("/api/v1/clusters/c1")
        # No GET happens in confirm mode.
        mock_client.get.assert_not_called()

    async def test_blocked_in_read_only_mode(
        self, mock_client, monkeypatch, settings,
    ):
        ro = settings.model_copy(update={"openvdi_mcp_read_only": True})
        monkeypatch.setattr(
            "openvdi_admin.tools._common.get_settings", lambda: ro,
        )
        with pytest.raises(BrokerError) as exc:
            await clusters.openvdi_delete_cluster(
                cluster_id="c1", confirm=True,
            )
        assert exc.value.code == "READ_ONLY_MODE"
