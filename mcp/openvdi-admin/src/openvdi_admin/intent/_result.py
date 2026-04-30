"""Shared shape for intent-tool results.

The IntentResult shape is the contract between intent tools and
agents (per S3 — structured error envelope). Intent tools that
orchestrate multiple operations may catch BrokerError selectively
and return an `ok=False` result so the agent can branch on
error_code instead of unwrapping a tool exception.

Shape:
  Success: {ok: true, operation, result, steps, total_duration_ms}
  Failure: {ok: false, operation, error_code, error_message,
            error_details, failed_at_step, steps, rollback_hint,
            total_duration_ms}

`StepTracker` is the helper for accumulating per-step timing and
outcomes within an intent body. Use the async-context-manager
`step()` API; on success the step record gains ok=True; on
exception the record has ok=False (the caller's outer try/except
catches the BrokerError that escaped).
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from openvdi_admin.errors import BrokerError


class StepTracker:
    """Accumulates per-step results across an intent-tool body.

    Usage:
        tracker = StepTracker()
        try:
            async with tracker.step("create_pool") as step:
                pool = await openvdi_create_pool(...)
                step["details"] = {"pool_id": pool["id"]}
                tracker.record_created("pool", pool["id"])
            return tracker.success_result(
                operation="deploy_pool",
                result={"pool_id": pool["id"]},
            )
        except BrokerError as exc:
            return tracker.failure_result(
                operation="deploy_pool",
                error=exc,
                failed_at_step=tracker.last_failed_step(),
                rollback_suggestion=...,
            )
    """

    def __init__(self) -> None:
        self._steps: list[dict[str, Any]] = []
        self._created: list[dict[str, str]] = []

    @asynccontextmanager
    async def step(self, name: str) -> AsyncIterator[dict[str, Any]]:
        """Record a named step. On normal exit marks ok=True; on
        exception leaves ok=False so the caller's outer try/except
        can interpret the partial state."""
        start = time.monotonic()
        record: dict[str, Any] = {"name": name, "ok": False}
        self._steps.append(record)
        try:
            yield record
            record["ok"] = True
        finally:
            record["duration_ms"] = int(
                (time.monotonic() - start) * 1000,
            )

    def record_created(self, type_: str, id_: str) -> None:
        """Track a resource created during the orchestration. Used
        in rollback_hint to surface what would need cleanup."""
        self._created.append({"type": type_, "id": id_})

    def last_failed_step(self) -> str:
        """Return the name of the most recent step that has ok=False,
        or 'unknown' if every recorded step succeeded.

        Failure paths use this to populate `failed_at_step` on the
        IntentResult without reaching into private state.
        """
        for step in reversed(self._steps):
            if not step.get("ok"):
                return step["name"]
        return "unknown"

    def success_result(
        self,
        operation: str,
        result: Any | None = None,
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "operation": operation,
            "result": result,
            "steps": self._steps,
            "total_duration_ms": sum(
                s.get("duration_ms", 0) for s in self._steps
            ),
        }

    def failure_result(
        self,
        operation: str,
        error: BrokerError,
        failed_at_step: str,
        rollback_suggestion: str | None = None,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "operation": operation,
            "error_code": error.code,
            "error_message": error.message,
            "error_details": error.details,
            "failed_at_step": failed_at_step,
            "steps": self._steps,
            "rollback_hint": (
                {
                    "created_resources": self._created,
                    "suggested_cleanup": rollback_suggestion,
                }
                if self._created
                else None
            ),
            "total_duration_ms": sum(
                s.get("duration_ms", 0) for s in self._steps
            ),
        }
