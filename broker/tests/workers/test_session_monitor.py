"""Tests for SessionMonitorWorker.

Drives the worker by calling tick() directly — no WorkerRunner
involved. Real DB (transactional fixture from tests/_db.py); mocked
provider; the worker's `async_session_factory` is monkeypatched so
the per-step sessions opened inside tick() participate in the test's
outer transaction (savepoint-nested commits, rolled back at teardown).

`refresh_desktop` and `delete_desktop_on_logoff` are mocked at the
import boundary inside session_monitor — the dispatch contract is
what we're testing, not M4-06's machinery (covered by
test_provisioner_refresh.py).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Cluster,
    Desktop,
    DesktopStatus,
    Pool,
    PoolType,
    Session,
    SessionStatus,
    Template,
)
from app.providers.base import GuestUser, VMRef, VMStatus
from app.providers.exceptions import ProviderError, ProviderNotFoundError
from app.workers.session_monitor import (
    LOGOFF_STREAK_THRESHOLD,
    SessionMonitorWorker,
)

# Re-export the transactional db_session fixture (M4-06).
from tests._db import db_session  # noqa: F401


# ── Patch the worker's session factory so its per-step sessions
#    participate in the test's outer transaction. Without this, the
#    worker would open fresh connections from the engine pool that
#    can't see the test's not-yet-committed (savepoint-only) fixtures.


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
    """Make the worker's `async_session_factory` reuse the test's
    bound connection. Autouse'd by every test in this module via
    explicit fixture request."""
    bind = db_session.bind  # the AsyncConnection from tests/_db.py
    factory = _TestSessionFactory(bind)
    monkeypatch.setattr(
        "app.workers.session_monitor.async_session_factory", factory,
    )
    return factory


# ── Topology builder ────────────────────────────────────────


async def _make_full_topology(
    db,
    *,
    pool_type: PoolType = PoolType.NONPERSISTENT,
    refresh_on_logoff: bool = True,
    delete_on_logoff: bool = False,
    cluster_status: str = "active",
    desktop_status: DesktopStatus = DesktopStatus.CONNECTED,
    assigned_user: str | None = "alice",
    assignment_type: str | None = "floating",
    session_status: SessionStatus = SessionStatus.ACTIVE,
):
    """Insert a full Cluster→Template→Pool→Desktop→Session chain.
    Returns (cluster, pool, desktop, session)."""
    cluster = Cluster(
        name=f"test-cluster-{uuid4().hex[:8]}",
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
        display_name="Test Pool",
        pool_type=pool_type,
        template_id=template.id,
        cluster_id=cluster.id,
        vmid_range_start=5000,
        vmid_range_end=5099,
        name_prefix="TEST",
        refresh_on_logoff=refresh_on_logoff,
        delete_on_logoff=delete_on_logoff,
    )
    db.add(pool)
    await db.flush()
    desktop = Desktop(
        pool_id=pool.id,
        pve_vmid=5001,
        pve_node="pve1",
        name="TEST-001",
        status=desktop_status,
        assigned_user=assigned_user,
        assignment_type=assignment_type,
    )
    db.add(desktop)
    await db.flush()
    session_row = Session(
        desktop_id=desktop.id,
        username=assigned_user or "unknown",
        protocol="novnc",
        status=session_status,
        connected_at=datetime.now(timezone.utc),
    )
    db.add(session_row)
    await db.flush()
    await db.commit()
    return cluster, pool, desktop, session_row


def _vmstatus(power_state: str) -> VMStatus:
    return VMStatus(
        ref=VMRef(
            provider_type="proxmox",
            data={"node": "pve1", "vmid": 5001},
        ),
        name="TEST-001",
        power_state=power_state,  # type: ignore[arg-type]
        cpu_cores=2,
        memory_bytes=0,
        disk_bytes=0,
        uptime_seconds=0,
        is_template=False,
        guest_agent_configured=True,
        lock=None,
        tags=frozenset(),
        raw={},
    )


def _make_provider(
    *,
    power_state: str = "running",
    users: list | None = None,
    network: list | None = None,
    raise_on_status: Exception | None = None,
    raise_on_users: Exception | None = None,
) -> MagicMock:
    provider = MagicMock()
    provider.provider_type = "proxmox"
    if raise_on_status is not None:
        provider.get_vm_status = AsyncMock(side_effect=raise_on_status)
    else:
        provider.get_vm_status = AsyncMock(
            return_value=_vmstatus(power_state),
        )
    if raise_on_users is not None:
        provider.agent_get_users = AsyncMock(side_effect=raise_on_users)
    else:
        provider.agent_get_users = AsyncMock(return_value=users or [])
    provider.agent_get_network = AsyncMock(return_value=network or [])
    return provider


def _make_app(cluster_id, provider) -> FastAPI:
    app = FastAPI()
    app.state.providers = {cluster_id: provider}
    return app


# ── Tests ────────────────────────────────────────────────────


async def test_logged_in_updates_telemetry(
    db_session, patch_session_factory,
):
    """Agent reports a user → session.last_heartbeat + os_user updated."""
    cluster, _pool, _desktop, session = await _make_full_topology(db_session)
    provider = _make_provider(users=[
        GuestUser(username="alice", login_time=None, domain="EXAMPLE"),
    ])
    worker = SessionMonitorWorker()
    app = _make_app(cluster.id, provider)
    await worker.tick(app)
    refreshed = await db_session.get(Session, session.id)
    await db_session.refresh(refreshed)
    assert refreshed.last_heartbeat is not None
    assert refreshed.os_user == "alice"


async def test_empty_users_increments_streak(
    db_session, patch_session_factory,
):
    cluster, _, desktop, _ = await _make_full_topology(db_session)
    provider = _make_provider(users=[])
    worker = SessionMonitorWorker()
    app = _make_app(cluster.id, provider)
    await worker.tick(app)
    assert worker._empty_streaks[desktop.id] == 1


async def test_logoff_after_3_empty_polls_dispatches_refresh(
    db_session, patch_session_factory, monkeypatch,
):
    """3 consecutive empty polls trigger refresh_desktop for a
    refresh_on_logoff=true non-persistent pool."""
    refresh_mock = AsyncMock()
    monkeypatch.setattr(
        "app.workers.session_monitor.refresh_desktop", refresh_mock,
    )
    cluster, _pool, desktop, _ = await _make_full_topology(db_session)
    provider = _make_provider(users=[])
    worker = SessionMonitorWorker()
    app = _make_app(cluster.id, provider)

    for _ in range(LOGOFF_STREAK_THRESHOLD):
        await worker.tick(app)

    refresh_mock.assert_called_once()
    args = refresh_mock.call_args
    assert args.kwargs["desktop_id"] == desktop.id
    assert args.kwargs["provider"] is provider


async def test_logoff_dispatches_delete_when_flag_set(
    db_session, patch_session_factory, monkeypatch,
):
    delete_mock = AsyncMock()
    monkeypatch.setattr(
        "app.workers.session_monitor.delete_desktop_on_logoff",
        delete_mock,
    )
    cluster, _, _desktop, _ = await _make_full_topology(
        db_session,
        refresh_on_logoff=False,
        delete_on_logoff=True,
    )
    provider = _make_provider(users=[])
    worker = SessionMonitorWorker()
    app = _make_app(cluster.id, provider)
    for _ in range(LOGOFF_STREAK_THRESHOLD):
        await worker.tick(app)
    delete_mock.assert_called_once()


async def test_logoff_neither_flag_just_ends_session(
    db_session, patch_session_factory, monkeypatch,
):
    """Non-persistent pool with neither flag: session ends, no
    refresh / delete called."""
    refresh_mock = AsyncMock()
    delete_mock = AsyncMock()
    monkeypatch.setattr(
        "app.workers.session_monitor.refresh_desktop", refresh_mock,
    )
    monkeypatch.setattr(
        "app.workers.session_monitor.delete_desktop_on_logoff",
        delete_mock,
    )
    cluster, _, _desktop, session = await _make_full_topology(
        db_session, refresh_on_logoff=False, delete_on_logoff=False,
    )
    provider = _make_provider(users=[])
    worker = SessionMonitorWorker()
    app = _make_app(cluster.id, provider)
    for _ in range(LOGOFF_STREAK_THRESHOLD):
        await worker.tick(app)
    refresh_mock.assert_not_called()
    delete_mock.assert_not_called()
    refreshed_session = await db_session.get(Session, session.id)
    await db_session.refresh(refreshed_session)
    assert refreshed_session.status == SessionStatus.ENDED


async def test_logoff_persistent_pool_just_ends_session(
    db_session, patch_session_factory, monkeypatch,
):
    """Persistent pool: session ends; desktop stays assigned."""
    refresh_mock = AsyncMock()
    monkeypatch.setattr(
        "app.workers.session_monitor.refresh_desktop", refresh_mock,
    )
    cluster, _, desktop, session = await _make_full_topology(
        db_session,
        pool_type=PoolType.PERSISTENT,
        assignment_type="persistent",
    )
    provider = _make_provider(users=[])
    worker = SessionMonitorWorker()
    app = _make_app(cluster.id, provider)
    for _ in range(LOGOFF_STREAK_THRESHOLD):
        await worker.tick(app)
    refresh_mock.assert_not_called()
    refreshed_session = await db_session.get(Session, session.id)
    await db_session.refresh(refreshed_session)
    assert refreshed_session.status == SessionStatus.ENDED
    refreshed_desktop = await db_session.get(Desktop, desktop.id)
    await db_session.refresh(refreshed_desktop)
    # transition_to_ended sets persistent → DISCONNECTED + retains.
    assert refreshed_desktop.status == DesktopStatus.DISCONNECTED
    assert refreshed_desktop.assigned_user == "alice"


async def test_user_logs_back_in_resets_streak(
    db_session, patch_session_factory,
):
    cluster, _, desktop, _ = await _make_full_topology(db_session)
    worker = SessionMonitorWorker()

    # First tick: empty users.
    provider1 = _make_provider(users=[])
    await worker.tick(_make_app(cluster.id, provider1))
    assert worker._empty_streaks[desktop.id] == 1

    # Second tick: user back.
    provider2 = _make_provider(users=[
        GuestUser(username="alice", login_time=None, domain=None),
    ])
    await worker.tick(_make_app(cluster.id, provider2))
    assert desktop.id not in worker._empty_streaks


async def test_vm_stopped_ends_session_no_dispatch(
    db_session, patch_session_factory, monkeypatch,
):
    refresh_mock = AsyncMock()
    monkeypatch.setattr(
        "app.workers.session_monitor.refresh_desktop", refresh_mock,
    )
    cluster, _, _, session = await _make_full_topology(db_session)
    provider = _make_provider(power_state="stopped")
    worker = SessionMonitorWorker()
    app = _make_app(cluster.id, provider)
    await worker.tick(app)
    refreshed = await db_session.get(Session, session.id)
    await db_session.refresh(refreshed)
    assert refreshed.status == SessionStatus.ENDED
    refresh_mock.assert_not_called()
    # agent_get_users was NOT called (skip remaining checks).
    provider.agent_get_users.assert_not_called()


async def test_agent_unreachable_clears_heartbeat_no_streak(
    db_session, patch_session_factory,
):
    cluster, _, desktop, session = await _make_full_topology(db_session)
    provider = _make_provider(
        raise_on_users=ProviderError("agent timeout", "proxmox"),
    )
    worker = SessionMonitorWorker()
    app = _make_app(cluster.id, provider)
    await worker.tick(app)
    refreshed = await db_session.get(Session, session.id)
    await db_session.refresh(refreshed)
    assert refreshed.last_heartbeat is None
    # Streak NOT incremented (agent unreachable ≠ user logged off).
    assert worker._empty_streaks.get(desktop.id, 0) == 0


async def test_offline_cluster_skipped(
    db_session, patch_session_factory,
):
    cluster, _, _, _ = await _make_full_topology(
        db_session, cluster_status="offline",
    )
    provider = _make_provider(users=[])
    worker = SessionMonitorWorker()
    app = _make_app(cluster.id, provider)
    await worker.tick(app)
    # Provider was never even consulted.
    provider.get_vm_status.assert_not_called()


async def test_vm_gone_in_proxmox_ends_session(
    db_session, patch_session_factory,
):
    cluster, _, _, session = await _make_full_topology(db_session)
    provider = _make_provider(
        raise_on_status=ProviderNotFoundError(
            "VM 5001 not found", "proxmox",
        ),
    )
    worker = SessionMonitorWorker()
    app = _make_app(cluster.id, provider)
    await worker.tick(app)
    refreshed = await db_session.get(Session, session.id)
    await db_session.refresh(refreshed)
    assert refreshed.status == SessionStatus.ENDED


async def test_streak_gc_for_desktop_no_longer_iterated(
    db_session, patch_session_factory,
):
    cluster, _, desktop, _ = await _make_full_topology(db_session)
    worker = SessionMonitorWorker()
    # Pre-populate the streak as if from a prior tick.
    worker._empty_streaks[desktop.id] = 1
    # Now flip the desktop out of monitored states.
    desktop.status = DesktopStatus.AVAILABLE
    await db_session.commit()
    provider = _make_provider(users=[])
    app = _make_app(cluster.id, provider)
    await worker.tick(app)
    # Streak GC'd.
    assert desktop.id not in worker._empty_streaks


async def test_os_user_mismatch_logs_warning(
    db_session, patch_session_factory, caplog,
):
    cluster, _, _, _ = await _make_full_topology(db_session)
    provider = _make_provider(users=[
        GuestUser(username="bob", login_time=None, domain=None),
    ])
    worker = SessionMonitorWorker()
    app = _make_app(cluster.id, provider)
    with caplog.at_level(logging.WARNING):
        await worker.tick(app)
    mismatch = [r for r in caplog.records if "mismatch" in r.message]
    assert len(mismatch) == 1
