"""Tests for intent/reset_environment.py."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from openvdi_admin.errors import BrokerError
from openvdi_admin.intent import reset_environment


@pytest.fixture
def mock_thin_wrappers(monkeypatch):
    mocks = {
        "openvdi_list_pools": AsyncMock(),
        "openvdi_get_pool": AsyncMock(),
        "openvdi_drain_pool": AsyncMock(),
        "openvdi_delete_pool": AsyncMock(),
        "openvdi_list_sessions": AsyncMock(),
        "openvdi_force_disconnect_session": AsyncMock(),
        "openvdi_list_entitlements": AsyncMock(),
        "openvdi_list_templates": AsyncMock(),
        "openvdi_retire_template": AsyncMock(),
        "openvdi_list_clusters": AsyncMock(),
        "openvdi_delete_cluster": AsyncMock(),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(
            f"openvdi_admin.intent.reset_environment.{name}", mock,
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


class TestPrefixGuards:
    @pytest.mark.parametrize(
        "prefix", ["", "*", "   "],
    )
    async def test_forbidden_prefixes_refused(
        self, mock_thin_wrappers, writable, prefix,
    ):
        with pytest.raises(BrokerError) as exc:
            await reset_environment.openvdi_reset_test_environment(
                name_prefix=prefix,
            )
        assert exc.value.code == "INVALID_REQUEST"
        # Refusal is up-front; no broker calls happen.
        mock_thin_wrappers["openvdi_list_pools"].assert_not_called()


class TestDryRun:
    async def test_basic_dry_run_shape(
        self, mock_thin_wrappers, writable,
    ):
        mock_thin_wrappers["openvdi_list_pools"].return_value = [
            {
                "id": "p1",
                "name": "test-eng",
                "template_id": "t1",
                "cluster_id": "c1",
            },
            {
                "id": "p2",
                "name": "production",
                "template_id": "t1",
                "cluster_id": "c1",
            },
        ]
        mock_thin_wrappers["openvdi_get_pool"].return_value = {
            "capacity": {"total_desktops": 5},
        }
        mock_thin_wrappers["openvdi_list_sessions"].return_value = [
            {"id": "s1"},
        ]
        mock_thin_wrappers["openvdi_list_entitlements"].return_value = [
            {"id": "e1"}, {"id": "e2"},
        ]
        result = (
            await reset_environment.openvdi_reset_test_environment(
                name_prefix="test-",
            )
        )
        assert result["dry_run"] is True
        assert len(result["would_drain_then_delete"]) == 1
        target = result["would_drain_then_delete"][0]
        assert target["name"] == "test-eng"
        assert target["desktops"] == 5
        assert target["active_sessions"] == 1
        assert target["entitlements"] == 2
        assert result["summary"]["pools"] == 1
        # No mutations in dry-run.
        mock_thin_wrappers["openvdi_drain_pool"].assert_not_called()
        mock_thin_wrappers["openvdi_delete_pool"].assert_not_called()

    async def test_dry_run_keep_templates_false_lists_candidates(
        self, mock_thin_wrappers, writable,
    ):
        mock_thin_wrappers["openvdi_list_pools"].return_value = [
            {
                "id": "p1",
                "name": "test-eng",
                "template_id": "t1",
                "cluster_id": "c1",
            },
        ]
        mock_thin_wrappers["openvdi_get_pool"].return_value = {
            "capacity": {"total_desktops": 0},
        }
        mock_thin_wrappers["openvdi_list_sessions"].return_value = []
        mock_thin_wrappers["openvdi_list_entitlements"].return_value = []
        mock_thin_wrappers["openvdi_list_templates"].return_value = [
            {"id": "t1", "name": "win11"},
            {"id": "t2", "name": "ubuntu24"},
        ]
        result = (
            await reset_environment.openvdi_reset_test_environment(
                name_prefix="test-", keep_templates=False,
            )
        )
        # t1 is referenced only by the test pool that would go away;
        # t2 is unreferenced. Both qualify for retirement.
        names = {t["name"] for t in result["would_retire_templates"]}
        assert names == {"win11", "ubuntu24"}

    async def test_dry_run_keep_clusters_false_lists_candidates(
        self, mock_thin_wrappers, writable,
    ):
        mock_thin_wrappers["openvdi_list_pools"].return_value = [
            {
                "id": "p1",
                "name": "test-eng",
                "template_id": "t1",
                "cluster_id": "c1",
            },
        ]
        mock_thin_wrappers["openvdi_get_pool"].return_value = {
            "capacity": {"total_desktops": 0},
        }
        mock_thin_wrappers["openvdi_list_sessions"].return_value = []
        mock_thin_wrappers["openvdi_list_entitlements"].return_value = []
        mock_thin_wrappers["openvdi_list_clusters"].return_value = [
            {"id": "c1", "name": "dev-cluster"},
        ]
        result = (
            await reset_environment.openvdi_reset_test_environment(
                name_prefix="test-", keep_clusters=False,
            )
        )
        assert result["would_delete_clusters"] == [
            {"id": "c1", "name": "dev-cluster"},
        ]


class TestExecution:
    async def test_single_pool_no_active_sessions(
        self, mock_thin_wrappers, writable,
    ):
        mock_thin_wrappers["openvdi_list_pools"].return_value = [
            {"id": "p1", "name": "test-eng",
             "template_id": "t1", "cluster_id": "c1"},
        ]
        mock_thin_wrappers["openvdi_drain_pool"].return_value = {}
        mock_thin_wrappers["openvdi_list_sessions"].return_value = []
        mock_thin_wrappers["openvdi_delete_pool"].return_value = None

        result = (
            await reset_environment.openvdi_reset_test_environment(
                name_prefix="test-", confirm=True,
            )
        )
        assert result["ok"] is True
        assert result["result"]["pools_deleted"] == 1
        names = [s["name"] for s in result["steps"]]
        assert names == [
            "drain[test-eng]",
            "disconnect_active[test-eng]",
            "delete[test-eng]",
        ]
        mock_thin_wrappers[
            "openvdi_drain_pool"
        ].assert_called_once_with(
            pool_id="p1", confirm=True, timeout_seconds=60,
        )
        mock_thin_wrappers[
            "openvdi_force_disconnect_session"
        ].assert_not_called()

    async def test_force_disconnect_runs_for_remaining_sessions(
        self, mock_thin_wrappers, writable,
    ):
        mock_thin_wrappers["openvdi_list_pools"].return_value = [
            {"id": "p1", "name": "test-eng",
             "template_id": "t1", "cluster_id": "c1"},
        ]
        mock_thin_wrappers["openvdi_drain_pool"].return_value = {}
        mock_thin_wrappers["openvdi_list_sessions"].return_value = [
            {"id": "s1"}, {"id": "s2"},
        ]
        mock_thin_wrappers[
            "openvdi_force_disconnect_session"
        ].return_value = None
        mock_thin_wrappers["openvdi_delete_pool"].return_value = None

        result = (
            await reset_environment.openvdi_reset_test_environment(
                name_prefix="test-", confirm=True,
            )
        )
        assert result["ok"] is True
        # Both sessions force-disconnected.
        assert (
            mock_thin_wrappers[
                "openvdi_force_disconnect_session"
            ].await_count
            == 2
        )

    async def test_force_disconnect_failure_logged_and_continues(
        self, mock_thin_wrappers, writable,
    ):
        mock_thin_wrappers["openvdi_list_pools"].return_value = [
            {"id": "p1", "name": "test-eng",
             "template_id": "t1", "cluster_id": "c1"},
        ]
        mock_thin_wrappers["openvdi_drain_pool"].return_value = {}
        mock_thin_wrappers["openvdi_list_sessions"].return_value = [
            {"id": "s1"},
        ]
        mock_thin_wrappers[
            "openvdi_force_disconnect_session"
        ].side_effect = BrokerError(
            http_status=409,
            code="CONFLICT",
            message="session in flux",
        )
        mock_thin_wrappers["openvdi_delete_pool"].return_value = None

        result = (
            await reset_environment.openvdi_reset_test_environment(
                name_prefix="test-", confirm=True,
            )
        )
        # Disconnect failed but reset continued through delete.
        assert result["ok"] is True
        mock_thin_wrappers["openvdi_delete_pool"].assert_called_once()

    async def test_multiple_pools_processed_in_order(
        self, mock_thin_wrappers, writable,
    ):
        mock_thin_wrappers["openvdi_list_pools"].return_value = [
            {"id": "p1", "name": "test-a",
             "template_id": "t1", "cluster_id": "c1"},
            {"id": "p2", "name": "test-b",
             "template_id": "t1", "cluster_id": "c1"},
            {"id": "p3", "name": "test-c",
             "template_id": "t1", "cluster_id": "c1"},
        ]
        mock_thin_wrappers["openvdi_drain_pool"].return_value = {}
        mock_thin_wrappers["openvdi_list_sessions"].return_value = []
        mock_thin_wrappers["openvdi_delete_pool"].return_value = None

        result = (
            await reset_environment.openvdi_reset_test_environment(
                name_prefix="test-", confirm=True,
            )
        )
        assert result["result"]["pools_deleted"] == 3
        # Three drains, three deletes.
        assert (
            mock_thin_wrappers["openvdi_drain_pool"].await_count == 3
        )
        assert (
            mock_thin_wrappers["openvdi_delete_pool"].await_count == 3
        )

    async def test_template_cluster_cascade(
        self, mock_thin_wrappers, writable,
    ):
        mock_thin_wrappers["openvdi_list_pools"].side_effect = [
            # Initial scan during execute path.
            [
                {"id": "p1", "name": "test-eng",
                 "template_id": "t1", "cluster_id": "c1"},
            ],
            # After pool deletion: no pools remain.
            [],
            [],
        ]
        mock_thin_wrappers["openvdi_drain_pool"].return_value = {}
        mock_thin_wrappers["openvdi_list_sessions"].return_value = []
        mock_thin_wrappers["openvdi_delete_pool"].return_value = None
        mock_thin_wrappers["openvdi_list_templates"].return_value = [
            {"id": "t1", "name": "win11"},
        ]
        mock_thin_wrappers["openvdi_retire_template"].return_value = None
        mock_thin_wrappers["openvdi_list_clusters"].return_value = [
            {"id": "c1", "name": "dev"},
        ]
        mock_thin_wrappers["openvdi_delete_cluster"].return_value = None

        result = (
            await reset_environment.openvdi_reset_test_environment(
                name_prefix="test-",
                confirm=True,
                keep_templates=False,
                keep_clusters=False,
            )
        )
        assert result["ok"] is True
        mock_thin_wrappers[
            "openvdi_retire_template"
        ].assert_called_once_with(template_id="t1", confirm=True)
        mock_thin_wrappers[
            "openvdi_delete_cluster"
        ].assert_called_once_with(cluster_id="c1", confirm=True)

    async def test_failure_mid_pool_returns_failure_result(
        self, mock_thin_wrappers, writable,
    ):
        mock_thin_wrappers["openvdi_list_pools"].return_value = [
            {"id": "p1", "name": "test-eng",
             "template_id": "t1", "cluster_id": "c1"},
        ]
        mock_thin_wrappers["openvdi_drain_pool"].side_effect = (
            BrokerError(
                http_status=500,
                code="INTERNAL_ERROR",
                message="broker error",
            )
        )

        result = (
            await reset_environment.openvdi_reset_test_environment(
                name_prefix="test-", confirm=True,
            )
        )
        assert result["ok"] is False
        assert result["error_code"] == "INTERNAL_ERROR"
        assert result["failed_at_step"] == "drain[test-eng]"


class TestReadOnly:
    async def test_blocked_in_read_only_mode(
        self, mock_thin_wrappers, read_only,
    ):
        with pytest.raises(BrokerError) as exc:
            await reset_environment.openvdi_reset_test_environment()
        assert exc.value.code == "READ_ONLY_MODE"
