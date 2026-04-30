"""Tests for intent/deploy_pool.py."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from openvdi_admin.errors import BrokerError
from openvdi_admin.intent import deploy_pool


@pytest.fixture
def mock_thin_wrappers(monkeypatch):
    mocks = {
        "openvdi_get_template": AsyncMock(),
        "openvdi_create_pool": AsyncMock(),
        "openvdi_grant_entitlement": AsyncMock(),
        "openvdi_provision_pool": AsyncMock(),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(
            f"openvdi_admin.intent.deploy_pool.{name}", mock,
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


_BASE_KWARGS = dict(
    template_id="t1",
    pool_name="test-eng",
    pool_display_name="Test Engineering",
    pool_type="nonpersistent",
    cluster_id="c1",
    vmid_range_start=9000,
    vmid_range_end=9019,
    name_prefix="TEST",
    entitlements=[{"type": "user", "name": "alice"}],
    min_spare=2,
    max_size=10,
)


class TestDryRun:
    async def test_returns_dry_run_shape_without_calls(
        self, mock_thin_wrappers, writable,
    ):
        result = await deploy_pool.openvdi_deploy_pool(
            **_BASE_KWARGS,
        )
        assert result["dry_run"] is True
        assert result["operation"] == "deploy_pool"
        assert result["would_create"]["pool_name"] == "test-eng"
        assert result["would_create"]["entitlements_count"] == 1
        assert result["would_create"]["pre_provision_count"] == 2
        for mock in mock_thin_wrappers.values():
            mock.assert_not_called()

    async def test_dry_run_pre_provision_zero_when_disabled(
        self, mock_thin_wrappers, writable,
    ):
        result = await deploy_pool.openvdi_deploy_pool(
            pre_provision=False, **_BASE_KWARGS,
        )
        assert result["would_create"]["pre_provision_count"] == 0


class TestEmptyEntitlements:
    async def test_raises_invalid_request(
        self, mock_thin_wrappers, writable,
    ):
        kwargs = {**_BASE_KWARGS, "entitlements": []}
        with pytest.raises(BrokerError) as exc:
            await deploy_pool.openvdi_deploy_pool(**kwargs)
        assert exc.value.code == "INVALID_REQUEST"


class TestHappyPath:
    async def test_full_flow_with_pre_provision(
        self, mock_thin_wrappers, writable,
    ):
        mock_thin_wrappers["openvdi_get_template"].return_value = {
            "id": "t1", "name": "win11", "status": "active",
        }
        mock_thin_wrappers["openvdi_create_pool"].return_value = {
            "id": "p1", "name": "test-eng",
        }
        mock_thin_wrappers["openvdi_grant_entitlement"].return_value = {
            "id": "e1",
        }
        mock_thin_wrappers["openvdi_provision_pool"].return_value = {
            "id": "p1",
            "capacity": {"available": 2, "provisioning": 0},
        }

        result = await deploy_pool.openvdi_deploy_pool(
            confirm=True, **_BASE_KWARGS,
        )
        assert result["ok"] is True
        assert result["result"]["pool_id"] == "p1"
        assert result["result"]["provisioned_count"] == 2
        assert result["result"]["pre_provision_complete"] is True
        names = [s["name"] for s in result["steps"]]
        assert names == [
            "verify_template",
            "create_pool",
            "grant_entitlement[0]",
            "provision_pool",
        ]

    async def test_no_pre_provision(
        self, mock_thin_wrappers, writable,
    ):
        mock_thin_wrappers["openvdi_get_template"].return_value = {
            "id": "t1", "name": "win11", "status": "active",
        }
        mock_thin_wrappers["openvdi_create_pool"].return_value = {
            "id": "p1",
        }
        mock_thin_wrappers["openvdi_grant_entitlement"].return_value = {
            "id": "e1",
        }
        result = await deploy_pool.openvdi_deploy_pool(
            confirm=True, pre_provision=False, **_BASE_KWARGS,
        )
        assert result["ok"] is True
        assert result["result"]["provisioned_count"] == 0
        assert result["result"]["pre_provision_complete"] is True
        mock_thin_wrappers["openvdi_provision_pool"].assert_not_called()


class TestEntitlementShapes:
    async def test_accepts_principal_type_principal_name_keys(
        self, mock_thin_wrappers, writable,
    ):
        kwargs = {
            **_BASE_KWARGS,
            "entitlements": [
                {
                    "principal_type": "group",
                    "principal_name": "VDI-Engineering",
                },
            ],
            "pre_provision": False,
        }
        mock_thin_wrappers["openvdi_get_template"].return_value = {
            "id": "t1", "status": "active",
        }
        mock_thin_wrappers["openvdi_create_pool"].return_value = {
            "id": "p1",
        }
        mock_thin_wrappers["openvdi_grant_entitlement"].return_value = {
            "id": "e1",
        }
        result = await deploy_pool.openvdi_deploy_pool(
            confirm=True, **kwargs,
        )
        assert result["ok"] is True
        call = mock_thin_wrappers["openvdi_grant_entitlement"].call_args
        assert call.kwargs["principal_type"] == "group"
        assert call.kwargs["principal_name"] == "VDI-Engineering"

    async def test_missing_keys_raises_invalid_request(
        self, mock_thin_wrappers, writable,
    ):
        kwargs = {
            **_BASE_KWARGS,
            "entitlements": [{"name": "alice"}],  # type missing
        }
        mock_thin_wrappers["openvdi_get_template"].return_value = {
            "id": "t1", "status": "active",
        }
        mock_thin_wrappers["openvdi_create_pool"].return_value = {
            "id": "p1",
        }
        result = await deploy_pool.openvdi_deploy_pool(
            confirm=True, **kwargs,
        )
        assert result["ok"] is False
        assert result["error_code"] == "INVALID_REQUEST"


class TestFailurePaths:
    async def test_template_inactive_no_rollback_hint(
        self, mock_thin_wrappers, writable,
    ):
        mock_thin_wrappers["openvdi_get_template"].return_value = {
            "id": "t1", "name": "win11", "status": "retired",
        }
        result = await deploy_pool.openvdi_deploy_pool(
            confirm=True, **_BASE_KWARGS,
        )
        assert result["ok"] is False
        assert result["error_code"] == "CONFLICT"
        assert result["failed_at_step"] == "verify_template"
        # Nothing was created → no rollback hint.
        assert result["rollback_hint"] is None
        mock_thin_wrappers["openvdi_create_pool"].assert_not_called()

    async def test_create_pool_fails_no_rollback_hint(
        self, mock_thin_wrappers, writable,
    ):
        mock_thin_wrappers["openvdi_get_template"].return_value = {
            "id": "t1", "status": "active",
        }
        mock_thin_wrappers["openvdi_create_pool"].side_effect = (
            BrokerError(
                http_status=409,
                code="CONFLICT",
                message="vmid range overlaps",
            )
        )
        result = await deploy_pool.openvdi_deploy_pool(
            confirm=True, **_BASE_KWARGS,
        )
        assert result["ok"] is False
        assert result["failed_at_step"] == "create_pool"
        # Pool creation failed → nothing to clean up.
        assert result["rollback_hint"] is None

    async def test_grant_fails_rollback_points_at_delete_pool(
        self, mock_thin_wrappers, writable,
    ):
        mock_thin_wrappers["openvdi_get_template"].return_value = {
            "id": "t1", "status": "active",
        }
        mock_thin_wrappers["openvdi_create_pool"].return_value = {
            "id": "p1",
        }
        mock_thin_wrappers["openvdi_grant_entitlement"].side_effect = (
            BrokerError(
                http_status=409,
                code="CONFLICT",
                message="entitlement already exists",
            )
        )
        result = await deploy_pool.openvdi_deploy_pool(
            confirm=True, **_BASE_KWARGS,
        )
        assert result["ok"] is False
        assert result["failed_at_step"] == "grant_entitlement[0]"
        assert (
            "openvdi_delete_pool('p1', confirm=True)"
            == result["rollback_hint"]["suggested_cleanup"]
        )

    async def test_provision_fails_rollback_includes_pool(
        self, mock_thin_wrappers, writable,
    ):
        mock_thin_wrappers["openvdi_get_template"].return_value = {
            "id": "t1", "status": "active",
        }
        mock_thin_wrappers["openvdi_create_pool"].return_value = {
            "id": "p1",
        }
        mock_thin_wrappers["openvdi_grant_entitlement"].return_value = {
            "id": "e1",
        }
        mock_thin_wrappers["openvdi_provision_pool"].side_effect = (
            BrokerError(
                http_status=409,
                code="POOL_FULL",
                message="exceeds max_size",
            )
        )
        result = await deploy_pool.openvdi_deploy_pool(
            confirm=True, **_BASE_KWARGS,
        )
        assert result["ok"] is False
        assert result["error_code"] == "POOL_FULL"
        assert result["failed_at_step"] == "provision_pool"
        # Resources created so far: pool + entitlement.
        created = result["rollback_hint"]["created_resources"]
        assert {"type": "pool", "id": "p1"} in created
        assert {"type": "entitlement", "id": "e1"} in created


class TestReadOnly:
    async def test_blocked_in_read_only_mode(
        self, mock_thin_wrappers, read_only,
    ):
        with pytest.raises(BrokerError) as exc:
            await deploy_pool.openvdi_deploy_pool(**_BASE_KWARGS)
        assert exc.value.code == "READ_ONLY_MODE"
