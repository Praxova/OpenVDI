"""Pool tool tests."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from openvdi_admin.errors import BrokerError
from openvdi_admin.tools import pools


@pytest.fixture
def mock_client(monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr(
        "openvdi_admin.tools._common.get_broker_client",
        lambda: client,
    )
    monkeypatch.setattr(
        "openvdi_admin.tools.pools.get_broker_client",
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
    """Replace `wait_for_pool_terminal_state` so provision/drain
    tests don't exercise the polling helper — that has its own
    dedicated test file. The stub returns whatever it's told."""
    waiter = AsyncMock()

    monkeypatch.setattr(
        "openvdi_admin.tools.pools.wait_for_pool_terminal_state",
        waiter,
    )
    return waiter


class TestListPools:
    async def test_default_pagination(self, mock_client):
        mock_client.get.return_value = []
        await pools.openvdi_list_pools()
        params = mock_client.get.call_args.kwargs["params"]
        assert params == {"limit": 50, "offset": 0}

    async def test_all_filters_passed_through(self, mock_client):
        mock_client.get.return_value = []
        await pools.openvdi_list_pools(
            cluster_id="c1",
            template_id="t1",
            pool_type="nonpersistent",
            status="active",
            limit=20,
            offset=10,
        )
        params = mock_client.get.call_args.kwargs["params"]
        assert params["cluster_id"] == "c1"
        assert params["template_id"] == "t1"
        assert params["pool_type"] == "nonpersistent"
        assert params["status"] == "active"
        assert params["limit"] == 20
        assert params["offset"] == 10


class TestGetPool:
    async def test_uses_path_param(self, mock_client):
        mock_client.get.return_value = {"id": "p1"}
        result = await pools.openvdi_get_pool("p1")
        assert result["id"] == "p1"
        mock_client.get.assert_called_once_with("/api/v1/pools/p1")


class TestGetPoolSummary:
    async def test_healthy_pool_no_issues(self, mock_client):
        mock_client.get.return_value = {
            "id": "p1",
            "name": "engineering",
            "display_name": "Engineering",
            "status": "active",
            "pool_type": "nonpersistent",
            "min_spare": 2,
            "max_size": 10,
            "capacity": {
                "total_desktops": 5,
                "available": 3,
                "assigned": 2,
                "connected": 2,
                "disconnected": 0,
                "provisioning": 0,
                "error": 0,
                "deleting": 0,
                "free_slots": 5,
            },
        }
        result = await pools.openvdi_get_pool_summary("p1")
        assert result["id"] == "p1"
        assert result["status"] == "active"
        assert result["capacity"]["total"] == 5
        assert result["capacity"]["available"] == 3
        assert result["issues"] == []

    async def test_pool_with_error_desktops_flags_issue(
        self, mock_client,
    ):
        mock_client.get.return_value = {
            "id": "p1",
            "name": "engineering",
            "status": "active",
            "pool_type": "nonpersistent",
            "min_spare": 0,
            "max_size": 10,
            "capacity": {
                "total_desktops": 3,
                "available": 1,
                "assigned": 1,
                "connected": 0,
                "disconnected": 0,
                "provisioning": 0,
                "error": 1,
                "deleting": 0,
                "free_slots": 7,
            },
        }
        result = await pools.openvdi_get_pool_summary("p1")
        assert any(
            "1 desktop(s) in error state" in issue
            for issue in result["issues"]
        )

    async def test_below_min_spare_flagged(self, mock_client):
        mock_client.get.return_value = {
            "id": "p1",
            "name": "engineering",
            "status": "active",
            "pool_type": "nonpersistent",
            "min_spare": 5,
            "max_size": 10,
            "capacity": {
                "total_desktops": 3,
                "available": 1,
                "assigned": 2,
                "connected": 1,
                "disconnected": 0,
                "provisioning": 0,
                "error": 0,
                "deleting": 0,
                "free_slots": 7,
            },
        }
        result = await pools.openvdi_get_pool_summary("p1")
        assert any(
            "below min_spare" in issue for issue in result["issues"]
        )

    async def test_persistent_pool_does_not_check_min_spare(
        self, mock_client,
    ):
        # Persistent pools don't have warm spares; below-min_spare
        # is a nonpersistent-only concern.
        mock_client.get.return_value = {
            "id": "p1",
            "name": "engineering",
            "status": "active",
            "pool_type": "persistent",
            "min_spare": 5,
            "max_size": 10,
            "capacity": {
                "total_desktops": 1, "available": 0, "assigned": 1,
                "connected": 0, "disconnected": 0, "provisioning": 0,
                "error": 0, "deleting": 0, "free_slots": 9,
            },
        }
        result = await pools.openvdi_get_pool_summary("p1")
        assert all(
            "below min_spare" not in issue
            for issue in result["issues"]
        )

    async def test_draining_pool_flagged(self, mock_client):
        mock_client.get.return_value = {
            "id": "p1",
            "name": "engineering",
            "status": "draining",
            "pool_type": "nonpersistent",
            "min_spare": 0,
            "max_size": 10,
            "capacity": {
                "total_desktops": 0, "available": 0, "assigned": 0,
                "connected": 0, "disconnected": 0, "provisioning": 0,
                "error": 0, "deleting": 0, "free_slots": 10,
            },
        }
        result = await pools.openvdi_get_pool_summary("p1")
        assert any("draining" in issue for issue in result["issues"])


class TestCreatePool:
    async def test_basic_create_with_required_fields(
        self, mock_client, writable,
    ):
        mock_client.post.return_value = {"id": "p1", "name": "eng"}
        await pools.openvdi_create_pool(
            name="eng",
            display_name="Engineering",
            pool_type="nonpersistent",
            template_id="t1",
            cluster_id="c1",
            vmid_range_start=5000,
            vmid_range_end=5099,
            name_prefix="ENG",
        )
        body = mock_client.post.call_args.kwargs["body"]
        assert body["name"] == "eng"
        assert body["pool_type"] == "nonpersistent"
        assert body["min_spare"] == 1  # default
        assert body["max_size"] == 10  # default
        assert body["refresh_on_logoff"] is True  # default
        assert "description" not in body  # not passed
        assert "cpu_cores" not in body
        assert "pve_pool_id" not in body

    async def test_with_optional_fields(self, mock_client, writable):
        mock_client.post.return_value = {"id": "p1"}
        await pools.openvdi_create_pool(
            name="kiosk",
            display_name="Kiosk",
            pool_type="nonpersistent",
            template_id="t1",
            cluster_id="c1",
            vmid_range_start=5100,
            vmid_range_end=5199,
            name_prefix="KIOSK",
            min_spare=2,
            max_size=20,
            description="Lobby kiosks",
            target_nodes="pve1,pve2",
            cpu_cores=2,
            memory_mb=4096,
            auto_logoff_min=30,
            delete_on_logoff=True,
            refresh_on_logoff=False,
            pve_pool_id="kiosk-organizational",
        )
        body = mock_client.post.call_args.kwargs["body"]
        assert body["min_spare"] == 2
        assert body["max_size"] == 20
        assert body["description"] == "Lobby kiosks"
        assert body["target_nodes"] == "pve1,pve2"
        assert body["cpu_cores"] == 2
        assert body["memory_mb"] == 4096
        assert body["auto_logoff_min"] == 30
        assert body["delete_on_logoff"] is True
        assert body["refresh_on_logoff"] is False
        assert body["pve_pool_id"] == "kiosk-organizational"

    async def test_blocked_in_read_only_mode(
        self, mock_client, read_only,
    ):
        with pytest.raises(BrokerError) as exc:
            await pools.openvdi_create_pool(
                name="x",
                display_name="X",
                pool_type="nonpersistent",
                template_id="t1",
                cluster_id="c1",
                vmid_range_start=1,
                vmid_range_end=10,
                name_prefix="X",
            )
        assert exc.value.code == "READ_ONLY_MODE"


class TestUpdatePool:
    async def test_only_passed_fields_in_body(
        self, mock_client, writable,
    ):
        mock_client.put.return_value = {"id": "p1"}
        await pools.openvdi_update_pool(
            pool_id="p1",
            display_name="New Name",
            min_spare=3,
        )
        body = mock_client.put.call_args.kwargs["body"]
        assert body == {"display_name": "New Name", "min_spare": 3}

    async def test_no_fields_sends_empty_body(
        self, mock_client, writable,
    ):
        mock_client.put.return_value = {"id": "p1"}
        await pools.openvdi_update_pool(pool_id="p1")
        body = mock_client.put.call_args.kwargs["body"]
        assert body == {}

    async def test_blocked_in_read_only_mode(
        self, mock_client, read_only,
    ):
        with pytest.raises(BrokerError) as exc:
            await pools.openvdi_update_pool(pool_id="p1")
        assert exc.value.code == "READ_ONLY_MODE"


class TestDeletePool:
    async def test_dry_run_shows_full_impact(
        self, mock_client, writable,
    ):
        mock_client.get.side_effect = [
            {
                "id": "p1",
                "name": "engineering",
                "display_name": "Engineering",
                "capacity": {"total_desktops": 5},
            },
            [{"id": "s1"}, {"id": "s2"}],
            [{"id": "e1"}],
        ]
        result = await pools.openvdi_delete_pool(
            pool_id="p1", confirm=False,
        )
        assert result["dry_run"] is True
        assert result["action"] == "delete_pool"
        assert result["target"]["name"] == "engineering"
        assert result["would_destroy"]["desktops"] == 5
        assert result["would_destroy"]["active_sessions"] == 2
        assert result["would_destroy"]["entitlements"] == 1
        mock_client.delete.assert_not_called()

    async def test_dry_run_handles_missing_capacity(
        self, mock_client, writable,
    ):
        # If the broker returns a pool record without `capacity`,
        # the dry-run should still succeed with desktops=0.
        mock_client.get.side_effect = [
            {"id": "p1", "name": "eng"},
            [],
            [],
        ]
        result = await pools.openvdi_delete_pool(
            pool_id="p1", confirm=False,
        )
        assert result["would_destroy"]["desktops"] == 0

    async def test_confirm_executes_delete(self, mock_client, writable):
        mock_client.delete.return_value = None
        result = await pools.openvdi_delete_pool(
            pool_id="p1", confirm=True,
        )
        assert result is None
        mock_client.delete.assert_called_once_with("/api/v1/pools/p1")
        mock_client.get.assert_not_called()

    async def test_blocked_in_read_only_mode(
        self, mock_client, read_only,
    ):
        with pytest.raises(BrokerError) as exc:
            await pools.openvdi_delete_pool(pool_id="p1")
        assert exc.value.code == "READ_ONLY_MODE"


class TestProvisionPool:
    async def test_posts_count_then_polls(
        self, mock_client, writable, stub_polling,
    ):
        final_state = {
            "id": "p1",
            "capacity": {"provisioning": 0, "deleting": 0},
        }
        stub_polling.return_value = final_state

        result = await pools.openvdi_provision_pool(
            pool_id="p1", count=3,
        )
        assert result == final_state
        post_call = mock_client.post.call_args
        assert post_call.args[0] == "/api/v1/pools/p1/provision"
        assert post_call.kwargs["body"] == {"count": 3}
        # wait_for_pool_terminal_state was called with our terminal
        # predicate and the default timeout.
        kwargs = stub_polling.call_args.kwargs
        assert kwargs["is_terminal"] is pools.pool_provision_terminal
        assert kwargs["timeout_seconds"] == 300

    async def test_custom_timeout_forwarded(
        self, mock_client, writable, stub_polling,
    ):
        stub_polling.return_value = {}
        await pools.openvdi_provision_pool(
            pool_id="p1", count=5, timeout_seconds=1200,
        )
        assert (
            stub_polling.call_args.kwargs["timeout_seconds"] == 1200
        )

    async def test_blocked_in_read_only_mode(
        self, mock_client, read_only,
    ):
        with pytest.raises(BrokerError) as exc:
            await pools.openvdi_provision_pool(pool_id="p1", count=1)
        assert exc.value.code == "READ_ONLY_MODE"


class TestDrainPool:
    async def test_dry_run_lists_active_sessions(
        self, mock_client, writable,
    ):
        mock_client.get.side_effect = [
            {"id": "p1", "name": "engineering"},
            [
                {"id": "s1", "username": "alice"},
                {"id": "s2", "username": "bob"},
            ],
        ]
        result = await pools.openvdi_drain_pool(
            pool_id="p1", confirm=False,
        )
        assert result["dry_run"] is True
        assert result["action"] == "drain_pool"
        sessions = result["active_sessions"]
        assert len(sessions) == 2
        assert sessions[0] == {"id": "s1", "username": "alice"}
        mock_client.post.assert_not_called()

    async def test_confirm_posts_drain_then_polls(
        self, mock_client, writable, stub_polling,
    ):
        final_state = {
            "id": "p1",
            "status": "draining",
            "_active_session_count": 0,
        }
        stub_polling.return_value = final_state

        result = await pools.openvdi_drain_pool(
            pool_id="p1", confirm=True,
        )
        assert result == final_state
        mock_client.post.assert_called_once_with("/api/v1/pools/p1/drain")
        kwargs = stub_polling.call_args.kwargs
        assert kwargs["is_terminal"] is pools.pool_drain_terminal
        assert kwargs["timeout_seconds"] == 600

    async def test_drain_fetch_composes_pool_and_session_count(
        self, mock_client, writable, monkeypatch,
    ):
        # Replace the polling helper with one that invokes its
        # `fetch` callable once and returns whatever it produced —
        # so we can verify the composed dict shape.
        captured: dict = {}

        async def fake_waiter(*, fetch, is_terminal, timeout_seconds):
            captured["state"] = await fetch()
            return captured["state"]

        monkeypatch.setattr(
            "openvdi_admin.tools.pools.wait_for_pool_terminal_state",
            fake_waiter,
        )
        mock_client.get.side_effect = [
            {"id": "p1", "status": "draining", "name": "eng"},
            [{"id": "s1"}, {"id": "s2"}],
        ]
        await pools.openvdi_drain_pool(pool_id="p1", confirm=True)
        state = captured["state"]
        assert state["id"] == "p1"
        assert state["status"] == "draining"
        assert state["_active_session_count"] == 2

    async def test_custom_timeout_forwarded(
        self, mock_client, writable, stub_polling,
    ):
        stub_polling.return_value = {}
        await pools.openvdi_drain_pool(
            pool_id="p1", confirm=True, timeout_seconds=1800,
        )
        assert (
            stub_polling.call_args.kwargs["timeout_seconds"] == 1800
        )

    async def test_blocked_in_read_only_mode(
        self, mock_client, read_only,
    ):
        with pytest.raises(BrokerError) as exc:
            await pools.openvdi_drain_pool(pool_id="p1")
        assert exc.value.code == "READ_ONLY_MODE"
