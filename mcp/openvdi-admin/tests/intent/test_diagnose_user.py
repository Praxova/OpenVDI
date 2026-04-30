"""Tests for intent/diagnose_user.py."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from openvdi_admin.errors import BrokerError
from openvdi_admin.intent import diagnose_user


@pytest.fixture
def mock_thin_wrappers(monkeypatch):
    mocks = {
        "openvdi_list_user_desktops": AsyncMock(),
        "openvdi_list_user_sessions": AsyncMock(),
        "openvdi_get_pool": AsyncMock(),
        "openvdi_list_pools": AsyncMock(),
        "openvdi_list_entitlements": AsyncMock(),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(
            f"openvdi_admin.intent.diagnose_user.{name}", mock,
        )
    return mocks


def _user_pool_view(
    pool_id: str,
    name: str = "engineering",
    display_name: str | None = None,
):
    return {
        "id": pool_id,
        "name": name,
        "display_name": display_name or name.title(),
        "status": "active",
        "pool_type": "nonpersistent",
        "assigned_desktop": None,
    }


def _pool_record(
    pool_id: str,
    *,
    status: str = "active",
    available: int = 3,
    total_desktops: int = 5,
    max_size: int = 10,
    pool_type: str = "nonpersistent",
):
    return {
        "id": pool_id,
        "name": "engineering",
        "status": status,
        "pool_type": pool_type,
        "min_spare": 2,
        "max_size": max_size,
        "cluster_id": "c1",
        "capacity": {
            "total_desktops": total_desktops,
            "available": available,
            "assigned": 0,
            "connected": 0,
            "provisioning": 0,
            "error": 0,
            "deleting": 0,
        },
    }


class TestComputeBlockingFactor:
    def test_active_pool_with_capacity_returns_none(self):
        pool = _pool_record("p1", available=3)
        assert (
            diagnose_user._compute_blocking_factor(pool) is None
        )

    def test_draining_pool(self):
        pool = _pool_record("p1", status="draining")
        assert (
            diagnose_user._compute_blocking_factor(pool)
            == "POOL_DRAINING"
        )

    def test_disabled_pool(self):
        pool = _pool_record("p1", status="disabled")
        assert (
            diagnose_user._compute_blocking_factor(pool)
            == "POOL_DISABLED"
        )

    def test_error_pool(self):
        pool = _pool_record("p1", status="error")
        assert (
            diagnose_user._compute_blocking_factor(pool)
            == "POOL_ERROR"
        )

    def test_full_pool(self):
        pool = _pool_record(
            "p1", available=0, total_desktops=10, max_size=10,
        )
        assert (
            diagnose_user._compute_blocking_factor(pool)
            == "POOL_FULL"
        )

    def test_empty_pool_below_max_size(self):
        # Headroom in the range, but no warm spares — v0 connect
        # doesn't auto-provision so it's still blocked.
        pool = _pool_record(
            "p1", available=0, total_desktops=2, max_size=10,
        )
        assert (
            diagnose_user._compute_blocking_factor(pool)
            == "POOL_EMPTY"
        )


class TestSummarizeUserState:
    def test_active_sessions(self):
        result = diagnose_user._summarize_user_state(
            pools=[],
            sessions=[{"id": "s1"}, {"id": "s2"}],
            group_ents=[],
        )
        assert result == "2 active sessions"

    def test_single_session_singular(self):
        result = diagnose_user._summarize_user_state(
            pools=[], sessions=[{"id": "s1"}], group_ents=[],
        )
        assert result == "1 active session"

    def test_entitled_no_blocking_could_connect(self):
        pools = [{"blocking_factor": None}]
        result = diagnose_user._summarize_user_state(
            pools=pools, sessions=[], group_ents=[],
        )
        assert "could connect now" in result

    def test_entitled_with_blocking(self):
        pools = [
            {"blocking_factor": "POOL_FULL"},
            {"blocking_factor": "POOL_DRAINING"},
        ]
        result = diagnose_user._summarize_user_state(
            pools=pools, sessions=[], group_ents=[],
        )
        assert "POOL_DRAINING" in result
        assert "POOL_FULL" in result

    def test_only_group_entitlements(self):
        result = diagnose_user._summarize_user_state(
            pools=[],
            sessions=[],
            group_ents=[{"pool_id": "p1"}],
        )
        assert "potential group entitlement" in result
        assert "IT Agent" in result

    def test_no_entitlements_anywhere(self):
        result = diagnose_user._summarize_user_state(
            pools=[], sessions=[], group_ents=[],
        )
        assert result == "no entitlements found"


class TestHappyPath:
    async def test_user_with_one_direct_pool_one_session(
        self, mock_thin_wrappers,
    ):
        mock_thin_wrappers["openvdi_list_user_desktops"].return_value = [
            _user_pool_view("p1", "engineering", "Engineering"),
        ]
        mock_thin_wrappers["openvdi_list_user_sessions"].side_effect = [
            [{"id": "s1", "status": "active"}],  # active
            [{"id": "s1"}, {"id": "s_old"}],     # recent (incl ended)
        ]
        mock_thin_wrappers["openvdi_get_pool"].return_value = (
            _pool_record("p1", available=2)
        )
        mock_thin_wrappers["openvdi_list_pools"].return_value = [
            {
                "id": "p1",
                "name": "engineering",
                "display_name": "Engineering",
            },
        ]
        # No other pools to scan for group ents.
        mock_thin_wrappers["openvdi_list_entitlements"].return_value = []

        result = await diagnose_user.openvdi_diagnose_user("alice")
        assert result["ok"] is True
        assert result["operation"] == "diagnose_user"
        body = result["result"]
        assert body["username"] == "alice"
        assert len(body["directly_entitled_pools"]) == 1
        assert (
            body["directly_entitled_pools"][0]["blocking_factor"]
            is None
        )
        assert len(body["active_sessions"]) == 1
        assert len(body["recent_sessions"]) == 2
        assert body["potential_group_entitlements"] == []
        assert body["summary"] == "1 active session"


class TestGroupEntitlements:
    async def test_lists_group_entitlements_excluding_direct_pools(
        self, mock_thin_wrappers,
    ):
        # User has direct entitlement to p1 only.
        mock_thin_wrappers["openvdi_list_user_desktops"].return_value = [
            _user_pool_view("p1"),
        ]
        mock_thin_wrappers["openvdi_list_user_sessions"].side_effect = [
            [], [],
        ]
        mock_thin_wrappers["openvdi_get_pool"].return_value = (
            _pool_record("p1")
        )
        # All pools include p1 (already direct) + p2 (group-only).
        mock_thin_wrappers["openvdi_list_pools"].return_value = [
            {
                "id": "p1",
                "name": "engineering",
                "display_name": "Engineering",
            },
            {"id": "p2", "name": "kiosk", "display_name": "Kiosk"},
        ]
        # entitlements list called only for non-direct pools.
        mock_thin_wrappers["openvdi_list_entitlements"].return_value = [
            {
                "id": "e1",
                "principal_type": "group",
                "principal_name": "VDI-Engineering",
            },
        ]

        result = await diagnose_user.openvdi_diagnose_user("alice")
        assert result["ok"] is True
        # list_entitlements should have been called once, for p2
        # only (p1 is direct → skipped).
        assert (
            mock_thin_wrappers["openvdi_list_entitlements"].await_count
            == 1
        )
        call = (
            mock_thin_wrappers["openvdi_list_entitlements"].call_args
        )
        assert call.kwargs["pool_id"] == "p2"
        assert call.kwargs["principal_type"] == "group"
        # Result lists the group-entitlement.
        potential = result["result"]["potential_group_entitlements"]
        assert potential == [
            {
                "pool_id": "p2",
                "pool_name": "Kiosk",
                "via_group": "VDI-Engineering",
            },
        ]


class TestNoEntitlements:
    async def test_summary_reflects_empty_state(
        self, mock_thin_wrappers,
    ):
        mock_thin_wrappers["openvdi_list_user_desktops"].return_value = []
        mock_thin_wrappers["openvdi_list_user_sessions"].side_effect = [
            [], [],
        ]
        mock_thin_wrappers["openvdi_list_pools"].return_value = []
        result = await diagnose_user.openvdi_diagnose_user("ghost")
        assert result["ok"] is True
        assert result["result"]["summary"] == "no entitlements found"


class TestBlockingFactorPropagation:
    async def test_draining_pool_surfaces_in_result(
        self, mock_thin_wrappers,
    ):
        mock_thin_wrappers["openvdi_list_user_desktops"].return_value = [
            _user_pool_view("p1"),
        ]
        mock_thin_wrappers["openvdi_list_user_sessions"].side_effect = [
            [], [],
        ]
        mock_thin_wrappers["openvdi_get_pool"].return_value = (
            _pool_record("p1", status="draining")
        )
        mock_thin_wrappers["openvdi_list_pools"].return_value = [
            {"id": "p1", "name": "engineering"},
        ]
        result = await diagnose_user.openvdi_diagnose_user("alice")
        pool = result["result"]["directly_entitled_pools"][0]
        assert pool["blocking_factor"] == "POOL_DRAINING"


class TestFailurePropagation:
    async def test_fetch_user_desktops_fails(
        self, mock_thin_wrappers,
    ):
        mock_thin_wrappers[
            "openvdi_list_user_desktops"
        ].side_effect = BrokerError(
            http_status=503,
            code="SERVICE_UNAVAILABLE",
            message="broker down",
        )
        result = await diagnose_user.openvdi_diagnose_user("alice")
        assert result["ok"] is False
        assert result["error_code"] == "SERVICE_UNAVAILABLE"
        assert result["failed_at_step"] == "fetch_user_desktops"
