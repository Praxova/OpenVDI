"""Tests for intent/diagnose_pool.py."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from openvdi_admin.errors import BrokerError
from openvdi_admin.intent import diagnose_pool


@pytest.fixture
def mock_thin_wrappers(monkeypatch):
    mocks = {
        "openvdi_get_pool": AsyncMock(),
        "openvdi_get_pool_summary": AsyncMock(),
        "openvdi_list_desktops": AsyncMock(),
        "openvdi_list_sessions": AsyncMock(),
        "openvdi_query_audit": AsyncMock(),
        "openvdi_get_cluster": AsyncMock(),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(
            f"openvdi_admin.intent.diagnose_pool.{name}", mock,
        )
    return mocks


def _pool(
    *,
    pool_id: str = "p1",
    status: str = "active",
    pool_type: str = "nonpersistent",
    cluster_id: str = "c1",
    min_spare: int = 2,
    max_size: int = 10,
):
    return {
        "id": pool_id,
        "name": "engineering",
        "display_name": "Engineering",
        "status": status,
        "pool_type": pool_type,
        "cluster_id": cluster_id,
        "min_spare": min_spare,
        "max_size": max_size,
        "capacity": {
            "total_desktops": 5,
            "available": 3,
        },
    }


def _summary(*, available: int = 3):
    return {
        "id": "p1",
        "name": "engineering",
        "status": "active",
        "pool_type": "nonpersistent",
        "capacity": {
            "total": 5,
            "available": available,
            "assigned": 0,
            "connected": 0,
            "provisioning": 0,
            "error": 0,
            "deleting": 0,
        },
    }


def _active_cluster():
    return {"id": "c1", "name": "default", "status": "active"}


def _setup_minimal(mocks, *, pool=None, desktops=None, cluster=None):
    """Wire up the fetch sequence with sensible defaults so each
    test only overrides what it cares about."""
    mocks["openvdi_get_pool"].return_value = pool or _pool()
    mocks["openvdi_get_pool_summary"].return_value = _summary()
    mocks["openvdi_list_desktops"].return_value = desktops or []
    mocks["openvdi_list_sessions"].return_value = []
    mocks["openvdi_query_audit"].return_value = []
    mocks["openvdi_get_cluster"].return_value = (
        cluster or _active_cluster()
    )


def _iso_minutes_ago(minutes: int) -> str:
    """Return an ISO-8601 'Z'-suffixed timestamp N minutes in the
    past."""
    ts = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return ts.isoformat().replace("+00:00", "Z")


class TestParseIso8601:
    def test_parses_z_suffix(self):
        result = diagnose_pool._parse_iso8601(
            "2026-04-30T10:00:00Z",
        )
        assert result is not None
        assert result.tzinfo is not None

    def test_parses_offset_suffix(self):
        result = diagnose_pool._parse_iso8601(
            "2026-04-30T10:00:00+00:00",
        )
        assert result is not None

    def test_returns_none_for_empty_string(self):
        assert diagnose_pool._parse_iso8601("") is None

    def test_returns_none_for_garbage(self):
        assert diagnose_pool._parse_iso8601("not-a-date") is None


class TestIdentifyStuckProvisioning:
    def test_skips_non_provisioning_status(self):
        desktops = [
            {
                "id": "d1",
                "status": "available",
                "created_at": _iso_minutes_ago(60),
            },
        ]
        assert diagnose_pool._identify_stuck_provisioning(desktops) == []

    def test_flags_old_provisioning_desktops(self):
        desktops = [
            {
                "id": "d1",
                "name": "ENG-001",
                "status": "provisioning",
                "created_at": _iso_minutes_ago(15),
            },
        ]
        stuck = diagnose_pool._identify_stuck_provisioning(desktops)
        assert len(stuck) == 1
        assert stuck[0]["id"] == "d1"
        assert stuck[0]["minutes_in_provisioning"] >= 15

    def test_does_not_flag_recent_provisioning(self):
        # 5 min < 10 min threshold.
        desktops = [
            {
                "id": "d1",
                "status": "provisioning",
                "created_at": _iso_minutes_ago(5),
            },
        ]
        assert diagnose_pool._identify_stuck_provisioning(desktops) == []

    def test_skips_desktop_with_no_created_at(self):
        desktops = [
            {
                "id": "d1",
                "status": "provisioning",
            },
        ]
        assert diagnose_pool._identify_stuck_provisioning(desktops) == []

    def test_skips_desktop_with_garbage_created_at(self):
        desktops = [
            {
                "id": "d1",
                "status": "provisioning",
                "created_at": "not-a-date",
            },
        ]
        assert diagnose_pool._identify_stuck_provisioning(desktops) == []


class TestComputeHealth:
    def test_healthy_when_no_issues_and_cluster_active(self):
        result = diagnose_pool._compute_health(
            pool=_pool(),
            issues=[],
            cluster=_active_cluster(),
        )
        assert result == "healthy"

    def test_degraded_with_only_warnings(self):
        result = diagnose_pool._compute_health(
            pool=_pool(),
            issues=[{"severity": "warning", "description": "x"}],
            cluster=_active_cluster(),
        )
        assert result == "degraded"

    def test_unhealthy_with_any_error_severity(self):
        result = diagnose_pool._compute_health(
            pool=_pool(),
            issues=[
                {"severity": "warning"},
                {"severity": "error"},
            ],
            cluster=_active_cluster(),
        )
        assert result == "unhealthy"

    def test_unhealthy_when_cluster_offline(self):
        result = diagnose_pool._compute_health(
            pool=_pool(),
            issues=[],
            cluster={"id": "c1", "status": "offline"},
        )
        assert result == "unhealthy"


class TestHappyPath:
    async def test_healthy_pool_no_issues(self, mock_thin_wrappers):
        _setup_minimal(mock_thin_wrappers)
        result = await diagnose_pool.openvdi_diagnose_pool("p1")
        assert result["ok"] is True
        body = result["result"]
        assert body["health"] == "healthy"
        assert body["issues"] == []
        assert body["error_desktops"] == []
        assert body["stuck_provisioning"] == []
        assert body["active_session_count"] == 0


class TestErrorDesktops:
    async def test_error_desktops_make_pool_unhealthy(
        self, mock_thin_wrappers,
    ):
        _setup_minimal(
            mock_thin_wrappers,
            desktops=[
                {
                    "id": "d1",
                    "name": "ENG-007",
                    "status": "error",
                    "error_message": "clone failed: lock",
                },
            ],
        )
        result = await diagnose_pool.openvdi_diagnose_pool("p1")
        body = result["result"]
        assert body["health"] == "unhealthy"
        assert len(body["error_desktops"]) == 1
        assert (
            body["error_desktops"][0]["error_message"]
            == "clone failed: lock"
        )
        # An issue surfaces with severity=error and a get_desktop hint.
        error_issues = [
            i for i in body["issues"] if i["severity"] == "error"
        ]
        assert any(
            "ENG-007" in i["description"] for i in error_issues
        )


class TestBelowMinSpare:
    async def test_below_min_spare_is_degraded(
        self, mock_thin_wrappers,
    ):
        mock_thin_wrappers["openvdi_get_pool"].return_value = _pool(
            min_spare=5,
        )
        mock_thin_wrappers["openvdi_get_pool_summary"].return_value = (
            _summary(available=1)
        )
        mock_thin_wrappers["openvdi_list_desktops"].return_value = []
        mock_thin_wrappers["openvdi_list_sessions"].return_value = []
        mock_thin_wrappers["openvdi_query_audit"].return_value = []
        mock_thin_wrappers["openvdi_get_cluster"].return_value = (
            _active_cluster()
        )

        result = await diagnose_pool.openvdi_diagnose_pool("p1")
        body = result["result"]
        assert body["health"] == "degraded"
        # provision_pool suggested with the deficit count.
        msgs = [i["description"] for i in body["issues"]]
        assert any("below min_spare" in m for m in msgs)
        actions = [i["suggested_action"] for i in body["issues"]]
        # 5 - 1 = 4 deficit.
        assert any("count=4" in a for a in actions)


class TestDraining:
    async def test_draining_pool_is_degraded(self, mock_thin_wrappers):
        _setup_minimal(
            mock_thin_wrappers,
            pool=_pool(status="draining"),
        )
        result = await diagnose_pool.openvdi_diagnose_pool("p1")
        body = result["result"]
        assert body["health"] == "degraded"
        descs = [i["description"] for i in body["issues"]]
        assert any("draining" in d for d in descs)


class TestStuckProvisioning:
    async def test_stuck_desktops_surface_in_result(
        self, mock_thin_wrappers,
    ):
        _setup_minimal(
            mock_thin_wrappers,
            desktops=[
                {
                    "id": "d1",
                    "name": "ENG-099",
                    "status": "provisioning",
                    "created_at": _iso_minutes_ago(20),
                },
            ],
        )
        result = await diagnose_pool.openvdi_diagnose_pool("p1")
        body = result["result"]
        assert len(body["stuck_provisioning"]) == 1
        assert body["health"] == "degraded"


class TestClusterStatus:
    async def test_offline_cluster_makes_unhealthy(
        self, mock_thin_wrappers,
    ):
        _setup_minimal(
            mock_thin_wrappers,
            cluster={
                "id": "c1", "name": "default", "status": "offline",
            },
        )
        result = await diagnose_pool.openvdi_diagnose_pool("p1")
        body = result["result"]
        assert body["health"] == "unhealthy"
        # Cluster issue is severity=error.
        cluster_issues = [
            i for i in body["issues"]
            if "cluster" in i["description"]
        ]
        assert len(cluster_issues) == 1
        assert cluster_issues[0]["severity"] == "error"


class TestAuditEvents:
    async def test_recent_audit_events_surface(self, mock_thin_wrappers):
        _setup_minimal(mock_thin_wrappers)
        mock_thin_wrappers["openvdi_query_audit"].return_value = [
            {
                "id": "a1",
                "actor": "alice",
                "action": "pool.update",
            },
        ]
        result = await diagnose_pool.openvdi_diagnose_pool("p1")
        assert len(result["result"]["recent_audit_events"]) == 1
        # Audit was queried with resource_type=pool + resource_id=p1.
        call = mock_thin_wrappers["openvdi_query_audit"].call_args
        assert call.kwargs["resource_type"] == "pool"
        assert call.kwargs["resource_id"] == "p1"
        assert call.kwargs["limit"] == 50
        assert "since" in call.kwargs


class TestActiveSessions:
    async def test_session_count_reflects_filtered_query(
        self, mock_thin_wrappers,
    ):
        _setup_minimal(mock_thin_wrappers)
        mock_thin_wrappers["openvdi_list_sessions"].return_value = [
            {"id": "s1"}, {"id": "s2"}, {"id": "s3"},
        ]
        result = await diagnose_pool.openvdi_diagnose_pool("p1")
        assert result["result"]["active_session_count"] == 3
        # Sessions were filtered to status=active for this pool.
        call = mock_thin_wrappers["openvdi_list_sessions"].call_args
        assert call.kwargs["pool_id"] == "p1"
        assert call.kwargs["status"] == "active"


class TestFailurePropagation:
    async def test_fetch_pool_failure_returns_failure_result(
        self, mock_thin_wrappers,
    ):
        mock_thin_wrappers["openvdi_get_pool"].side_effect = BrokerError(
            http_status=404,
            code="NOT_FOUND",
            message="pool not found",
        )
        result = await diagnose_pool.openvdi_diagnose_pool("missing")
        assert result["ok"] is False
        assert result["error_code"] == "NOT_FOUND"
        assert result["failed_at_step"] == "fetch_pool"
