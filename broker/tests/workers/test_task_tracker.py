"""Tests for TaskTrackerWorker.

Mocks the provider's get_task_status. Each test sets up a desktop
with a UPID, runs the worker tick, asserts on resulting state.

The session-factory monkeypatch covers both the worker's tick (which
fetches in-flight rows) AND the completion handlers in
services/task_tracker.py (which open their own sessions for
_apply_task_success / _mark_task_error). Both modules need patching.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Cluster,
    Desktop,
    DesktopStatus,
    Pool,
    PoolType,
    Template,
)
from app.providers.base import TaskHandle, TaskStatus
from app.providers.exceptions import ProviderError
from app.services.task_tracker import (
    DesktopTaskKind,
    record_desktop_task,
)
from app.workers.task_tracker import TaskTrackerWorker

# Re-export the transactional db_session fixture (M4-06).
from tests._db import db_session  # noqa: F401


class _TestSessionFactory:
    def __init__(self, connection) -> None:
        self._connection = connection

    def __call__(self) -> AsyncSession:
        return AsyncSession(
            bind=self._connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )


@pytest_asyncio.fixture
async def patch_session_factory(db_session, monkeypatch):
    """Patch async_session_factory in BOTH modules the worker reaches:
      - app.workers.task_tracker (tick's in-flight fetch)
      - app.services.task_tracker (_apply_task_success / _mark_task_error)
    """
    factory = _TestSessionFactory(db_session.bind)
    monkeypatch.setattr(
        "app.workers.task_tracker.async_session_factory", factory,
    )
    monkeypatch.setattr(
        "app.services.task_tracker.async_session_factory", factory,
    )
    return factory


# ── Topology builder ────────────────────────────────────────


async def _make_topology(
    db,
    *,
    cluster_status: str = "active",
    desktop_status: DesktopStatus = DesktopStatus.PROVISIONING,
    pve_task_upid: str | None = "UPID:pve1:00001234:65000000:qmstart:5001:root@pam:",
    pve_task_kind: str | None = DesktopTaskKind.START.value,
):
    cluster = Cluster(
        name=f"cluster-{uuid4().hex[:8]}",
        provider_type="proxmox",
        api_url="https://test.example.com:8006",
        token_id="x@pve!y",
        token_secret="ciphertext",
        status=cluster_status,
    )
    db.add(cluster)
    await db.flush()
    template = Template(
        cluster_id=cluster.id,
        name=f"tpl-{uuid4().hex[:8]}",
        pve_vmid=9000,
        pve_node="pve1",
        os_type="windows11",
    )
    db.add(template)
    await db.flush()
    pool = Pool(
        name=f"pool-{uuid4().hex[:8]}",
        display_name="P",
        pool_type=PoolType.NONPERSISTENT,
        template_id=template.id,
        cluster_id=cluster.id,
        vmid_range_start=5000,
        vmid_range_end=5099,
        name_prefix="TEST",
    )
    db.add(pool)
    await db.flush()
    desktop = Desktop(
        pool_id=pool.id,
        pve_vmid=5001,
        pve_node="pve1",
        name="TEST-001",
        status=desktop_status,
        pve_task_upid=pve_task_upid,
        pve_task_kind=pve_task_kind,
    )
    db.add(desktop)
    await db.flush()
    await db.commit()
    return cluster, pool, desktop


def _make_provider(
    *,
    state: str = "stopped",
    success: bool | None = True,
    error_message: str | None = None,
    raise_on_status: Exception | None = None,
) -> MagicMock:
    provider = MagicMock()
    provider.provider_type = "proxmox"
    if raise_on_status is not None:
        provider.get_task_status = AsyncMock(side_effect=raise_on_status)
    else:
        provider.get_task_status = AsyncMock(return_value=TaskStatus(
            state=state,  # type: ignore[arg-type]
            success=success,
            error_message=error_message,
            raw={},
        ))
    return provider


def _make_app(cluster_id, provider) -> FastAPI:
    app = FastAPI()
    app.state.providers = {cluster_id: provider}
    return app


# ── Tests ────────────────────────────────────────────────────


async def test_no_in_flight_no_op(db_session, patch_session_factory):
    """Empty DB → tick is a no-op."""
    worker = TaskTrackerWorker()
    app = _make_app(uuid4(), MagicMock())
    await worker.tick(app)


async def test_running_task_left_alone(
    db_session, patch_session_factory,
):
    cluster, _, desktop = await _make_topology(db_session)
    desktop_id = desktop.id
    provider = _make_provider(state="running", success=None)
    worker = TaskTrackerWorker()
    await worker.tick(_make_app(cluster.id, provider))
    refreshed = await db_session.get(Desktop, desktop_id)
    await db_session.refresh(refreshed)
    assert refreshed.pve_task_upid is not None
    assert refreshed.status == DesktopStatus.PROVISIONING


async def test_start_task_success_clears_upid_sets_running(
    db_session, patch_session_factory,
):
    cluster, _, desktop = await _make_topology(
        db_session, pve_task_kind=DesktopTaskKind.START.value,
    )
    desktop_id = desktop.id
    provider = _make_provider(state="stopped", success=True)
    worker = TaskTrackerWorker()
    await worker.tick(_make_app(cluster.id, provider))
    refreshed = await db_session.get(Desktop, desktop_id)
    await db_session.refresh(refreshed)
    assert refreshed.pve_task_upid is None
    assert refreshed.pve_task_kind is None
    assert refreshed.power_state == "running"


async def test_shutdown_task_success_sets_stopped(
    db_session, patch_session_factory,
):
    cluster, _, desktop = await _make_topology(
        db_session, pve_task_kind=DesktopTaskKind.SHUTDOWN.value,
    )
    desktop_id = desktop.id
    provider = _make_provider(state="stopped", success=True)
    worker = TaskTrackerWorker()
    await worker.tick(_make_app(cluster.id, provider))
    refreshed = await db_session.get(Desktop, desktop_id)
    await db_session.refresh(refreshed)
    assert refreshed.power_state == "stopped"


async def test_destroy_task_success_removes_row(
    db_session, patch_session_factory,
):
    cluster, _, desktop = await _make_topology(
        db_session,
        desktop_status=DesktopStatus.DELETING,
        pve_task_kind=DesktopTaskKind.DESTROY.value,
    )
    desktop_id = desktop.id
    provider = _make_provider(state="stopped", success=True)
    worker = TaskTrackerWorker()
    await worker.tick(_make_app(cluster.id, provider))
    # Row gone — query directly to bypass the test session's identity map.
    fresh = await db_session.execute(
        text("SELECT id FROM desktops WHERE id = :id"),
        {"id": desktop_id},
    )
    assert fresh.first() is None


async def test_task_failure_marks_error(
    db_session, patch_session_factory,
):
    cluster, _, desktop = await _make_topology(db_session)
    desktop_id = desktop.id
    provider = _make_provider(
        state="stopped",
        success=False,
        error_message="qmstart: VM 5001: kvm: -drive: error reading config",
    )
    worker = TaskTrackerWorker()
    await worker.tick(_make_app(cluster.id, provider))
    refreshed = await db_session.get(Desktop, desktop_id)
    await db_session.refresh(refreshed)
    assert refreshed.status == DesktopStatus.ERROR
    assert refreshed.pve_task_upid is None
    assert "kvm" in refreshed.error_message


async def test_provider_error_marks_error(
    db_session, patch_session_factory,
):
    cluster, _, desktop = await _make_topology(db_session)
    desktop_id = desktop.id
    provider = _make_provider(
        raise_on_status=ProviderError("connection refused", "proxmox"),
    )
    worker = TaskTrackerWorker()
    await worker.tick(_make_app(cluster.id, provider))
    refreshed = await db_session.get(Desktop, desktop_id)
    await db_session.refresh(refreshed)
    assert refreshed.status == DesktopStatus.ERROR


async def test_invalid_kind_marks_error(
    db_session, patch_session_factory,
):
    cluster, _, desktop = await _make_topology(
        db_session, pve_task_kind="garbage_kind",
    )
    desktop_id = desktop.id
    provider = _make_provider()
    worker = TaskTrackerWorker()
    await worker.tick(_make_app(cluster.id, provider))
    refreshed = await db_session.get(Desktop, desktop_id)
    await db_session.refresh(refreshed)
    assert refreshed.status == DesktopStatus.ERROR
    assert "unknown task kind" in refreshed.error_message


async def test_offline_cluster_skipped(
    db_session, patch_session_factory,
):
    cluster, _, _desktop = await _make_topology(
        db_session, cluster_status="offline",
    )
    provider = _make_provider()
    worker = TaskTrackerWorker()
    await worker.tick(_make_app(cluster.id, provider))
    # Provider never consulted — the worker's _fetch_in_flight filters
    # to active clusters only.
    provider.get_task_status.assert_not_called()


async def test_provider_missing_for_cluster_skipped(
    db_session, patch_session_factory,
):
    """Cluster active in DB but no provider on this broker — skip."""
    cluster, _, _desktop = await _make_topology(db_session)
    worker = TaskTrackerWorker()
    app = FastAPI()
    app.state.providers = {}  # no provider for this cluster
    # No exception, no state change.
    await worker.tick(app)


async def test_per_desktop_exception_does_not_abort_tick(
    db_session, patch_session_factory, caplog,
):
    """One desktop's failure shouldn't prevent others from being polled."""
    cluster, pool, _desktop1 = await _make_topology(db_session)
    desktop2 = Desktop(
        pool_id=pool.id,
        pve_vmid=5002,
        pve_node="pve1",
        name="TEST-002",
        status=DesktopStatus.PROVISIONING,
        pve_task_upid="UPID:pve1:00002:65000000:qmstart:5002:root@pam:",
        pve_task_kind=DesktopTaskKind.START.value,
    )
    db_session.add(desktop2)
    await db_session.commit()

    # First desktop: get_task_status raises an UNEXPECTED exception
    # (RuntimeError, not ProviderError) — exercises the per-desktop
    # exception handler in tick().
    call_count = 0

    async def buggy(handle):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("unexpected")
        return TaskStatus(
            state="stopped", success=True, error_message=None, raw={},
        )

    provider = MagicMock()
    provider.provider_type = "proxmox"
    provider.get_task_status = buggy
    worker = TaskTrackerWorker()
    with caplog.at_level(logging.ERROR):
        await worker.tick(_make_app(cluster.id, provider))
    # Both desktops were attempted (call_count == 2).
    assert call_count == 2


# ── record_desktop_task ─────────────────────────────────────


async def test_record_desktop_task_writes_upid_and_kind(
    db_session, patch_session_factory,
):
    """record_desktop_task is a pure DB write; no scheduling, no
    BackgroundTasks dependency."""
    _cluster, _pool, desktop = await _make_topology(
        db_session, pve_task_upid=None, pve_task_kind=None,
    )
    handle = TaskHandle(
        provider_type="proxmox",
        data={"node": "pve1", "upid": "UPID:pve1:99999"},
    )
    await record_desktop_task(
        session=db_session,
        desktop=desktop,
        kind=DesktopTaskKind.DESTROY,
        task_handle=handle,
    )
    await db_session.commit()
    refreshed = await db_session.get(Desktop, desktop.id)
    await db_session.refresh(refreshed)
    assert refreshed.pve_task_upid == "UPID:pve1:99999"
    assert refreshed.pve_task_kind == "destroy"
