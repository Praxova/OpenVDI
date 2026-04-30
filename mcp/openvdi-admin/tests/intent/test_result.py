"""Tests for intent/_result.py — StepTracker + result shapes."""
from __future__ import annotations

import pytest

from openvdi_admin.errors import BrokerError
from openvdi_admin.intent._result import StepTracker


class TestStepTracker:
    async def test_step_records_success(self):
        tracker = StepTracker()
        async with tracker.step("foo") as step:
            step["details"] = {"x": 1}
        result = tracker.success_result(operation="op")
        assert len(result["steps"]) == 1
        assert result["steps"][0]["name"] == "foo"
        assert result["steps"][0]["ok"] is True
        assert result["steps"][0]["details"] == {"x": 1}
        assert result["steps"][0]["duration_ms"] >= 0

    async def test_step_marks_ok_false_on_exception(self):
        tracker = StepTracker()
        with pytest.raises(RuntimeError):
            async with tracker.step("explode"):
                raise RuntimeError("boom")
        # The step record is still in the list with ok=False.
        # Access via the public method so we don't reach into
        # _steps from outside the package.
        assert tracker.last_failed_step() == "explode"

    async def test_step_duration_recorded_even_on_exception(self):
        tracker = StepTracker()
        with pytest.raises(RuntimeError):
            async with tracker.step("explode"):
                raise RuntimeError("boom")
        # success_result reads steps for total_duration; check the
        # exploded step has a duration field via that aggregation.
        result = tracker.success_result(operation="op")
        assert result["total_duration_ms"] >= 0

    async def test_record_created_accumulates(self):
        tracker = StepTracker()
        tracker.record_created("pool", "p1")
        tracker.record_created("entitlement", "e1")
        # Indirectly check via failure_result rollback_hint.
        err = BrokerError(http_status=500, code="X", message="x")
        result = tracker.failure_result(
            operation="op",
            error=err,
            failed_at_step="something",
            rollback_suggestion=None,
        )
        created = result["rollback_hint"]["created_resources"]
        assert created == [
            {"type": "pool", "id": "p1"},
            {"type": "entitlement", "id": "e1"},
        ]


class TestLastFailedStep:
    async def test_returns_unknown_when_all_succeeded(self):
        tracker = StepTracker()
        async with tracker.step("a"):
            pass
        async with tracker.step("b"):
            pass
        assert tracker.last_failed_step() == "unknown"

    async def test_returns_first_failed_in_reverse_order(self):
        tracker = StepTracker()
        async with tracker.step("a"):
            pass
        with pytest.raises(RuntimeError):
            async with tracker.step("b"):
                raise RuntimeError("boom")
        assert tracker.last_failed_step() == "b"

    async def test_returns_unknown_for_empty_tracker(self):
        tracker = StepTracker()
        assert tracker.last_failed_step() == "unknown"


class TestSuccessResult:
    async def test_shape_with_no_steps(self):
        tracker = StepTracker()
        result = tracker.success_result(
            operation="op", result={"k": "v"},
        )
        assert result == {
            "ok": True,
            "operation": "op",
            "result": {"k": "v"},
            "steps": [],
            "total_duration_ms": 0,
        }

    async def test_total_duration_sums_steps(self):
        tracker = StepTracker()
        async with tracker.step("a"):
            pass
        async with tracker.step("b"):
            pass
        result = tracker.success_result(operation="op")
        # Sum of two non-negative durations.
        per_step_total = sum(
            s["duration_ms"] for s in result["steps"]
        )
        assert result["total_duration_ms"] == per_step_total


class TestFailureResult:
    async def test_shape_with_rollback_hint(self):
        tracker = StepTracker()
        tracker.record_created("pool", "p1")
        err = BrokerError(
            http_status=409,
            code="POOL_FULL",
            message="full",
            details={"max_size": 5},
        )
        result = tracker.failure_result(
            operation="deploy_pool",
            error=err,
            failed_at_step="provision_pool",
            rollback_suggestion=(
                "openvdi_delete_pool('p1', confirm=True)"
            ),
        )
        assert result["ok"] is False
        assert result["operation"] == "deploy_pool"
        assert result["error_code"] == "POOL_FULL"
        assert result["error_message"] == "full"
        assert result["error_details"] == {"max_size": 5}
        assert result["failed_at_step"] == "provision_pool"
        assert result["rollback_hint"]["suggested_cleanup"] == (
            "openvdi_delete_pool('p1', confirm=True)"
        )

    async def test_shape_without_rollback_hint(self):
        tracker = StepTracker()
        # No record_created → rollback_hint is None.
        err = BrokerError(
            http_status=400, code="INVALID_REQUEST", message="bad",
        )
        result = tracker.failure_result(
            operation="op",
            error=err,
            failed_at_step="step",
            rollback_suggestion=None,
        )
        assert result["rollback_hint"] is None
