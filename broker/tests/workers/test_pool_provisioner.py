"""Tests for PoolProvisionerWorker.

The worker's logic (pool selection, capacity check, gating) is what
this file covers. provision_desktop is mocked at the worker's import
site so we don't re-run M2's clone/configure/start cycle for each
test.

Same session-factory monkeypatch pattern as test_session_monitor —
the worker opens its own AsyncSessions inside tick() via
async_session_factory; we patch that to return sessions bound to the
test's transactional connection (savepoint-nested commits, rolled
back at teardown).
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Cluster,
    Desktop,
    DesktopStatus,
    Pool,
    PoolStatus,
    PoolType,
    Template,
)
from app.services.provisioner import PoolInactive
from app.services.vmid_allocator import VMIDRangeExhausted
from app.workers.pool_provisioner import PoolProvisionerWorker

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
    """Patch the worker's async_session_factory so per-step sessions
    participate in the test's outer transaction."""
    factory = _TestSessionFactory(db_session.bind)
    monkeypatch.setattr(
        "app.workers.pool_provisioner.async_session_factory", factory,
    )
    return factory


# ── Fixture builders ────────────────────────────────────────


async def _make_pool(
    db,
    *,
    pool_type: PoolType = PoolType.NONPERSISTENT,
    status: PoolStatus = PoolStatus.ACTIVE,
    cluster_status: str = "active",
    min_spare: int = 2,
    max_size: int = 10,
    name: str | None = None,
):
    """Insert a Cluster + Template + Pool. Returns (cluster, template, pool)."""
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
        name=name or f"pool-{uuid4().hex[:8]}",
        display_name="P",
        pool_type=pool_type,
        template_id=template.id,
        cluster_id=cluster.id,
        vmid_range_start=5000,
        vmid_range_end=5099,
        name_prefix="TEST",
        min_spare=min_spare,
        max_size=max_size,
        status=status,
    )
    db.add(pool)
    await db.flush()
    await db.commit()
    return cluster, template, pool


async def _make_desktop(
    db,
    pool: Pool,
    *,
    status: DesktopStatus = DesktopStatus.AVAILABLE,
    vmid: int | None = None,
):
    desktop = Desktop(
        pool_id=pool.id,
        pve_vmid=vmid if vmid is not None else 5000 + (uuid4().int % 99),
        pve_node="pve1",
        name=f"D-{uuid4().hex[:6]}",
        status=status,
    )
    db.add(desktop)
    await db.flush()
    await db.commit()
    return desktop


def _make_app(cluster_id, provider) -> FastAPI:
    app = FastAPI()
    app.state.providers = {cluster_id: provider}
    return app


def _stub_provision(monkeypatch, *, returns=None, raises=None) -> AsyncMock:
    """Replace provision_desktop in the worker module with a stub.
    Returns the AsyncMock so tests can assert on calls.
    """
    mock = AsyncMock()
    if raises is not None:
        mock.side_effect = raises
    elif returns is not None:
        mock.return_value = returns
    monkeypatch.setattr(
        "app.workers.pool_provisioner.provision_desktop", mock,
    )
    return mock


# ── Tests ────────────────────────────────────────────────────


async def test_no_pools_no_provision(
    db_session, patch_session_factory, monkeypatch,
):
    """Empty DB → tick is a no-op."""
    mock = _stub_provision(monkeypatch)
    worker = PoolProvisionerWorker()
    app = _make_app(uuid4(), MagicMock())
    await worker.tick(app)
    mock.assert_not_called()


async def test_pool_at_min_spare_no_provision(
    db_session, patch_session_factory, monkeypatch,
):
    """available == min_spare → no provision."""
    cluster, _, pool = await _make_pool(
        db_session, min_spare=2, max_size=10,
    )
    await _make_desktop(db_session, pool, status=DesktopStatus.AVAILABLE)
    await _make_desktop(db_session, pool, status=DesktopStatus.AVAILABLE)
    mock = _stub_provision(monkeypatch)
    worker = PoolProvisionerWorker()
    app = _make_app(cluster.id, MagicMock())
    await worker.tick(app)
    mock.assert_not_called()


async def test_pool_below_min_spare_provisions_one(
    db_session, patch_session_factory, monkeypatch,
):
    cluster, _, pool = await _make_pool(
        db_session, min_spare=2, max_size=10,
    )
    await _make_desktop(db_session, pool, status=DesktopStatus.AVAILABLE)
    success_desktop = Desktop(
        id=uuid4(),
        pool_id=pool.id,
        pve_vmid=5050,
        pve_node="pve1",
        name="TEST-001",
        status=DesktopStatus.AVAILABLE,
    )
    mock = _stub_provision(monkeypatch, returns=success_desktop)
    worker = PoolProvisionerWorker()
    app = _make_app(cluster.id, MagicMock())
    await worker.tick(app)
    mock.assert_called_once()


async def test_pool_at_max_size_no_provision(
    db_session, patch_session_factory, monkeypatch,
):
    """total_count == max_size → no provision even if under min_spare."""
    cluster, _, pool = await _make_pool(
        db_session, min_spare=5, max_size=2,
    )
    await _make_desktop(db_session, pool, status=DesktopStatus.PROVISIONING)
    await _make_desktop(db_session, pool, status=DesktopStatus.PROVISIONING)
    mock = _stub_provision(monkeypatch)
    worker = PoolProvisionerWorker()
    app = _make_app(cluster.id, MagicMock())
    await worker.tick(app)
    mock.assert_not_called()


async def test_persistent_pool_skipped(
    db_session, patch_session_factory, monkeypatch,
):
    """Persistent pools never auto-provisioned (W8)."""
    cluster, _, _pool = await _make_pool(
        db_session,
        pool_type=PoolType.PERSISTENT,
        min_spare=5,
        max_size=10,
    )
    mock = _stub_provision(monkeypatch)
    worker = PoolProvisionerWorker()
    app = _make_app(cluster.id, MagicMock())
    await worker.tick(app)
    mock.assert_not_called()


async def test_inactive_pool_skipped(
    db_session, patch_session_factory, monkeypatch,
):
    cluster, _, _pool = await _make_pool(
        db_session,
        status=PoolStatus.DRAINING,
        min_spare=5,
        max_size=10,
    )
    mock = _stub_provision(monkeypatch)
    worker = PoolProvisionerWorker()
    app = _make_app(cluster.id, MagicMock())
    await worker.tick(app)
    mock.assert_not_called()


async def test_offline_cluster_skipped(
    db_session, patch_session_factory, monkeypatch,
):
    cluster, _, _pool = await _make_pool(
        db_session,
        cluster_status="offline",
        min_spare=5,
        max_size=10,
    )
    mock = _stub_provision(monkeypatch)
    worker = PoolProvisionerWorker()
    app = _make_app(cluster.id, MagicMock())
    await worker.tick(app)
    mock.assert_not_called()


async def test_neediest_pool_picked_first(
    db_session, patch_session_factory, monkeypatch,
):
    """Two pools below spec — the larger gap goes first."""
    cluster_a, _, pool_a = await _make_pool(
        db_session, name="aaa", min_spare=5, max_size=10,
    )  # gap=5
    cluster_b, _, pool_b = await _make_pool(
        db_session, name="bbb", min_spare=2, max_size=10,
    )  # gap=2

    captured: dict = {}

    async def stub(
        *,
        session,
        provider,
        pool,
        template,
        assigned_user=None,
        existing_desktop=None,
    ):
        captured["pool_id"] = pool.id
        return Desktop(
            id=uuid4(),
            pool_id=pool.id,
            pve_vmid=5050,
            pve_node="p",
            name="x",
            status=DesktopStatus.AVAILABLE,
        )

    monkeypatch.setattr(
        "app.workers.pool_provisioner.provision_desktop", stub,
    )
    worker = PoolProvisionerWorker()
    app = FastAPI()
    app.state.providers = {
        cluster_a.id: MagicMock(),
        cluster_b.id: MagicMock(),
    }
    await worker.tick(app)
    assert captured["pool_id"] == pool_a.id


async def test_provisioning_status_counts_toward_total(
    db_session, patch_session_factory, monkeypatch,
):
    """In-flight 'provisioning' rows count against max_size, preventing
    double-provision in the next tick."""
    cluster, _, pool = await _make_pool(
        db_session, min_spare=2, max_size=2,
    )
    await _make_desktop(db_session, pool, status=DesktopStatus.AVAILABLE)
    await _make_desktop(db_session, pool, status=DesktopStatus.PROVISIONING)
    mock = _stub_provision(monkeypatch)
    worker = PoolProvisionerWorker()
    app = _make_app(cluster.id, MagicMock())
    await worker.tick(app)
    mock.assert_not_called()


async def test_error_rows_excluded_from_total(
    db_session, patch_session_factory, monkeypatch,
):
    """'error' desktops don't count toward total → next tick can provision."""
    cluster, _, pool = await _make_pool(
        db_session, min_spare=2, max_size=2,
    )
    await _make_desktop(db_session, pool, status=DesktopStatus.ERROR)
    await _make_desktop(db_session, pool, status=DesktopStatus.ERROR)
    success = Desktop(
        id=uuid4(),
        pool_id=pool.id,
        pve_vmid=5050,
        pve_node="p",
        name="x",
        status=DesktopStatus.AVAILABLE,
    )
    mock = _stub_provision(monkeypatch, returns=success)
    worker = PoolProvisionerWorker()
    app = _make_app(cluster.id, MagicMock())
    await worker.tick(app)
    mock.assert_called_once()


async def test_provider_missing_for_cluster_skips_provision(
    db_session, patch_session_factory, monkeypatch, caplog,
):
    """If app.state.providers doesn't have the cluster, log warning + skip."""
    cluster, _, _pool = await _make_pool(
        db_session, min_spare=2, max_size=10,
    )
    mock = _stub_provision(monkeypatch)
    worker = PoolProvisionerWorker()
    app = FastAPI()
    app.state.providers = {}  # empty
    with caplog.at_level(logging.WARNING):
        await worker.tick(app)
    mock.assert_not_called()
    assert any("no provider" in r.message for r in caplog.records)


async def test_provision_desktop_returns_error_logs_warning(
    db_session, patch_session_factory, monkeypatch, caplog,
):
    """provision_desktop returning a Desktop with status='error' is
    expected (M2 contract); worker logs warning and continues."""
    cluster, _, pool = await _make_pool(
        db_session, min_spare=1, max_size=10,
    )
    error_desktop = Desktop(
        id=uuid4(),
        pool_id=pool.id,
        pve_vmid=5050,
        pve_node="p",
        name="x",
        status=DesktopStatus.ERROR,
        error_message="clone task failed: storage full",
    )
    _stub_provision(monkeypatch, returns=error_desktop)
    worker = PoolProvisionerWorker()
    app = _make_app(cluster.id, MagicMock())
    with caplog.at_level(logging.WARNING):
        await worker.tick(app)
    assert any("provisioning failed" in r.message for r in caplog.records)


async def test_pool_inactive_exception_swallowed(
    db_session, patch_session_factory, monkeypatch,
):
    """If pool flips to non-active mid-tick, PoolInactive is logged
    and the tick returns gracefully."""
    cluster, _, _pool = await _make_pool(
        db_session, min_spare=1, max_size=10,
    )
    _stub_provision(
        monkeypatch, raises=PoolInactive("flipped mid-tick"),
    )
    worker = PoolProvisionerWorker()
    app = _make_app(cluster.id, MagicMock())
    # Should not raise.
    await worker.tick(app)


async def test_vmid_exhausted_exception_swallowed(
    db_session, patch_session_factory, monkeypatch,
):
    cluster, _, _pool = await _make_pool(
        db_session, min_spare=1, max_size=10,
    )
    _stub_provision(
        monkeypatch, raises=VMIDRangeExhausted("range full"),
    )
    worker = PoolProvisionerWorker()
    app = _make_app(cluster.id, MagicMock())
    await worker.tick(app)
