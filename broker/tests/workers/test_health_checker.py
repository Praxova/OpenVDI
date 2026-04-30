"""Tests for HealthCheckerWorker.

Covers: provider sync (new / updated / unchanged / deleted),
construct-failure resilience, cluster ping status transitions,
storage-low warnings, orphan reconciliation in both directions, and
stuck-provisioning detection.

Real Postgres via tests/_db.py (M4-06). construct_provider and
ping_and_update_status are mocked at the worker's import boundary so
tests don't need real Proxmox credentials.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Cluster,
    ClusterStatus,
    Desktop,
    DesktopStatus,
    Pool,
    PoolType,
    Template,
)
from app.providers.base import (
    NodeInfo,
    StorageInfo,
    VMRef,
    VMStatus,
)
from app.workers.health_checker import (
    STUCK_PROVISIONING_MINUTES,
    HealthCheckerWorker,
)

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
    """Patch async_session_factory in BOTH the worker module and
    cluster_service (which ping_and_update_status uses internally).

    Also clears the `clusters` table inside the test's transaction. The
    dev DB has a persistent seed cluster (M4-01's 002_seed_data.sql)
    that the worker's _load_clusters would otherwise pick up alongside
    each test's cluster — leading to double-iteration and false-positive
    log assertions. Deletion happens inside the outer transaction; the
    fixture's rollback at teardown restores the seed cluster.
    """
    # Clear clusters first so test fixtures land in a clean DB. CASCADE
    # is necessary because Pool/Desktop/etc reference clusters via FKs.
    await db_session.execute(
        text("DELETE FROM auth_tokens"),
    )
    await db_session.execute(
        text("DELETE FROM clusters"),
    )
    await db_session.commit()

    factory = _TestSessionFactory(db_session.bind)
    monkeypatch.setattr(
        "app.workers.health_checker.async_session_factory", factory,
    )
    monkeypatch.setattr(
        "app.services.cluster_service.async_session_factory", factory,
    )
    return factory


# ── Fixture builders ────────────────────────────────────────


async def _make_cluster(
    db,
    *,
    status: str = "active",
):
    cluster = Cluster(
        name=f"cluster-{uuid4().hex[:8]}",
        provider_type="proxmox",
        api_url="https://test.example.com:8006",
        token_id="x@pve!y",
        token_secret="ciphertext",
        status=status,
    )
    db.add(cluster)
    await db.flush()
    await db.commit()
    return cluster


async def _make_pool_with_desktop(
    db,
    cluster: Cluster,
    *,
    desktop_status: DesktopStatus = DesktopStatus.AVAILABLE,
    updated_at: datetime | None = None,
):
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
    )
    db.add(desktop)
    await db.flush()
    await db.commit()
    if updated_at is not None:
        # Set updated_at directly via SQL to bypass SQLAlchemy's
        # onupdate=func.now() semantics. The ORM would assign
        # CURRENT_TIMESTAMP via the trigger when we commit.
        await db.execute(
            text(
                "UPDATE desktops SET updated_at = :ts WHERE id = :id"
            ),
            {"ts": updated_at, "id": desktop.id},
        )
        await db.commit()
        await db.refresh(desktop)
    return pool, desktop


def _make_provider(
    *,
    nodes: list | None = None,
    storages_by_node: dict | None = None,
    vms: list | None = None,
) -> MagicMock:
    provider = MagicMock()
    provider.provider_type = "proxmox"
    provider.close = AsyncMock()

    nodes = nodes if nodes is not None else [
        NodeInfo(
            node="pve1", display_name="pve1", status="online",
            cpu_cores=8, memory_bytes=16 * 1024**3,
        ),
    ]
    provider.list_nodes = AsyncMock(return_value=nodes)

    async def list_storage(node: str):
        return (storages_by_node or {}).get(node, [])
    provider.list_storage = AsyncMock(side_effect=list_storage)

    provider.list_vms = AsyncMock(return_value=vms or [])
    return provider


def _make_app(
    initial_providers: dict | None = None,
    initial_constructed_at: dict | None = None,
) -> FastAPI:
    app = FastAPI()
    app.state.providers = initial_providers or {}
    app.state.provider_constructed_at = initial_constructed_at or {}
    return app


def _patch_cluster_service(
    monkeypatch,
    *,
    construct=None,
    ping_returns=ClusterStatus.ACTIVE,
):
    """Replace construct_provider + ping_and_update_status with mocks."""
    if construct is None:
        construct = AsyncMock()
    monkeypatch.setattr(
        "app.workers.health_checker.construct_provider", construct,
    )
    monkeypatch.setattr(
        "app.workers.health_checker.ping_and_update_status",
        AsyncMock(return_value=ping_returns),
    )
    return construct


# ── Provider sync tests ─────────────────────────────────────


async def test_new_cluster_constructs_provider(
    db_session, patch_session_factory, monkeypatch,
):
    """A cluster in DB but not in app.state.providers → constructed."""
    cluster = await _make_cluster(db_session)
    new_provider = _make_provider()
    _patch_cluster_service(
        monkeypatch, construct=AsyncMock(return_value=new_provider),
    )
    worker = HealthCheckerWorker()
    app = _make_app()
    await worker.tick(app)
    assert cluster.id in app.state.providers
    assert cluster.id in app.state.provider_constructed_at


async def test_updated_cluster_reconstructs_provider(
    db_session, patch_session_factory, monkeypatch,
):
    """cluster.updated_at > constructed_at → old closed, new
    constructed (W13 update path)."""
    cluster = await _make_cluster(db_session)
    old_provider = _make_provider()
    new_provider = _make_provider()
    _patch_cluster_service(
        monkeypatch, construct=AsyncMock(return_value=new_provider),
    )
    worker = HealthCheckerWorker()
    # Simulate broker constructed the old provider 1 hour ago.
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    app = _make_app(
        initial_providers={cluster.id: old_provider},
        initial_constructed_at={cluster.id: one_hour_ago},
    )
    await worker.tick(app)
    old_provider.close.assert_called_once()
    assert app.state.providers[cluster.id] is new_provider


async def test_unchanged_cluster_skips_reconstruction(
    db_session, patch_session_factory, monkeypatch,
):
    cluster = await _make_cluster(db_session)
    existing_provider = _make_provider()
    construct_mock = AsyncMock()
    _patch_cluster_service(monkeypatch, construct=construct_mock)
    worker = HealthCheckerWorker()
    # Constructed in the future — sync says "no need."
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    app = _make_app(
        initial_providers={cluster.id: existing_provider},
        initial_constructed_at={cluster.id: future},
    )
    await worker.tick(app)
    construct_mock.assert_not_called()


async def test_deleted_cluster_provider_removed(
    db_session, patch_session_factory, monkeypatch,
):
    """Cluster removed from DB → provider closed and removed from
    app.state.providers."""
    _patch_cluster_service(monkeypatch)
    stale_id = uuid4()
    stale_provider = _make_provider()
    worker = HealthCheckerWorker()
    app = _make_app(
        initial_providers={stale_id: stale_provider},
        initial_constructed_at={
            stale_id: datetime.now(timezone.utc) - timedelta(hours=1),
        },
    )
    await worker.tick(app)
    assert stale_id not in app.state.providers
    assert stale_id not in app.state.provider_constructed_at
    stale_provider.close.assert_called_once()


async def test_construct_provider_failure_logs_and_continues(
    db_session, patch_session_factory, monkeypatch, caplog,
):
    """Construction failure during sync — log warning, don't crash tick."""
    cluster = await _make_cluster(db_session)
    _patch_cluster_service(
        monkeypatch,
        construct=AsyncMock(side_effect=RuntimeError("decrypt failed")),
    )
    worker = HealthCheckerWorker()
    app = _make_app()
    with caplog.at_level(logging.WARNING):
        await worker.tick(app)
    assert any(
        "failed to construct provider" in r.message
        for r in caplog.records
    )
    assert cluster.id not in app.state.providers


# ── Cluster status / data-gathering gating ───────────────────


async def test_offline_cluster_skipped_for_data_gathering(
    db_session, patch_session_factory, monkeypatch,
):
    """ping_and_update_status returns OFFLINE → skip nodes/storage/vms."""
    cluster = await _make_cluster(db_session, status="offline")
    provider = _make_provider()
    _patch_cluster_service(
        monkeypatch,
        construct=AsyncMock(return_value=provider),
        ping_returns=ClusterStatus.OFFLINE,
    )
    worker = HealthCheckerWorker()
    app = _make_app()
    await worker.tick(app)
    provider.list_nodes.assert_not_called()
    provider.list_vms.assert_not_called()


async def test_active_cluster_runs_data_gathering(
    db_session, patch_session_factory, monkeypatch,
):
    cluster = await _make_cluster(db_session)
    provider = _make_provider()
    _patch_cluster_service(
        monkeypatch, construct=AsyncMock(return_value=provider),
    )
    worker = HealthCheckerWorker()
    app = _make_app()
    await worker.tick(app)
    provider.list_nodes.assert_called()
    provider.list_vms.assert_called()


async def test_maintenance_cluster_skipped_entirely(
    db_session, patch_session_factory, monkeypatch,
):
    """Maintenance clusters not iterated at all."""
    await _make_cluster(db_session, status="maintenance")
    construct_mock = AsyncMock()
    ping_mock = AsyncMock()
    monkeypatch.setattr(
        "app.workers.health_checker.construct_provider", construct_mock,
    )
    monkeypatch.setattr(
        "app.workers.health_checker.ping_and_update_status", ping_mock,
    )
    worker = HealthCheckerWorker()
    app = _make_app()
    await worker.tick(app)
    construct_mock.assert_not_called()
    ping_mock.assert_not_called()


# ── Storage tests ──────────────────────────────────────────


async def test_low_storage_logs_warning(
    db_session, patch_session_factory, monkeypatch, caplog,
):
    await _make_cluster(db_session)
    storages = [
        StorageInfo(
            name="local-lvm", storage_type="lvm-thin", shared=False,
            total_bytes=100 * 1024**3,
            used_bytes=90 * 1024**3,  # 10% free
            content_types=frozenset({"images"}),
        ),
    ]
    provider = _make_provider(storages_by_node={"pve1": storages})
    _patch_cluster_service(
        monkeypatch, construct=AsyncMock(return_value=provider),
    )
    worker = HealthCheckerWorker()
    app = _make_app()
    with caplog.at_level(logging.WARNING):
        await worker.tick(app)
    low = [r for r in caplog.records if "below capacity" in r.message]
    assert len(low) == 1


async def test_high_storage_no_warning(
    db_session, patch_session_factory, monkeypatch, caplog,
):
    await _make_cluster(db_session)
    storages = [
        StorageInfo(
            name="local-lvm", storage_type="lvm-thin", shared=False,
            total_bytes=100 * 1024**3,
            used_bytes=10 * 1024**3,  # 90% free
            content_types=frozenset({"images"}),
        ),
    ]
    provider = _make_provider(storages_by_node={"pve1": storages})
    _patch_cluster_service(
        monkeypatch, construct=AsyncMock(return_value=provider),
    )
    worker = HealthCheckerWorker()
    app = _make_app()
    with caplog.at_level(logging.WARNING):
        await worker.tick(app)
    low = [r for r in caplog.records if "below capacity" in r.message]
    assert len(low) == 0


# ── Orphan reconciliation tests ─────────────────────────────


async def test_db_desktop_missing_in_provider_logs_warning(
    db_session, patch_session_factory, monkeypatch, caplog,
):
    cluster = await _make_cluster(db_session)
    await _make_pool_with_desktop(db_session, cluster)
    provider = _make_provider(vms=[])  # no VMs in provider
    _patch_cluster_service(
        monkeypatch, construct=AsyncMock(return_value=provider),
    )
    worker = HealthCheckerWorker()
    app = _make_app()
    with caplog.at_level(logging.WARNING):
        await worker.tick(app)
    matched = [
        r for r in caplog.records
        if "DB desktop has no matching VM" in r.message
    ]
    assert len(matched) == 1


async def test_provider_orphan_vm_logs_warning(
    db_session, patch_session_factory, monkeypatch, caplog,
):
    """Provider has an openvdi-tagged VM with no corresponding DB row."""
    await _make_cluster(db_session)
    orphan = VMStatus(
        ref=VMRef(
            provider_type="proxmox",
            data={"node": "pve1", "vmid": 5099},
        ),
        name="ORPHAN-001",
        power_state="running",
        cpu_cores=2,
        memory_bytes=0,
        disk_bytes=0,
        uptime_seconds=0,
        is_template=False,
        guest_agent_configured=True,
        lock=None,
        tags=frozenset({"openvdi-managed"}),
        raw={},
    )
    provider = _make_provider(vms=[orphan])
    _patch_cluster_service(
        monkeypatch, construct=AsyncMock(return_value=provider),
    )
    worker = HealthCheckerWorker()
    app = _make_app()
    with caplog.at_level(logging.WARNING):
        await worker.tick(app)
    matched = [
        r for r in caplog.records
        if "openvdi-tagged VM" in r.message and "no DB row" in r.message
    ]
    assert len(matched) == 1


async def test_provider_untagged_vm_not_flagged(
    db_session, patch_session_factory, monkeypatch, caplog,
):
    """A VM in provider WITHOUT the openvdi-managed tag isn't flagged
    as an orphan — it could be an admin-created VM unrelated to OpenVDI."""
    await _make_cluster(db_session)
    untagged = VMStatus(
        ref=VMRef(
            provider_type="proxmox",
            data={"node": "pve1", "vmid": 7777},
        ),
        name="ADMIN-VM",
        power_state="running",
        cpu_cores=2,
        memory_bytes=0,
        disk_bytes=0,
        uptime_seconds=0,
        is_template=False,
        guest_agent_configured=True,
        lock=None,
        tags=frozenset(),  # no openvdi tag
        raw={},
    )
    provider = _make_provider(vms=[untagged])
    _patch_cluster_service(
        monkeypatch, construct=AsyncMock(return_value=provider),
    )
    worker = HealthCheckerWorker()
    app = _make_app()
    with caplog.at_level(logging.WARNING):
        await worker.tick(app)
    matched = [
        r for r in caplog.records
        if "openvdi-tagged VM" in r.message
    ]
    assert len(matched) == 0


# ── Stuck-provisioning tests ────────────────────────────────


async def test_stuck_provisioning_flipped_to_error(
    db_session, patch_session_factory,
):
    cluster = await _make_cluster(db_session)
    long_ago = datetime.now(timezone.utc) - timedelta(
        minutes=STUCK_PROVISIONING_MINUTES + 1,
    )
    _pool, desktop = await _make_pool_with_desktop(
        db_session,
        cluster,
        desktop_status=DesktopStatus.PROVISIONING,
        updated_at=long_ago,
    )
    desktop_id = desktop.id
    worker = HealthCheckerWorker()
    app = _make_app()
    with (
        patch(
            "app.workers.health_checker.construct_provider", AsyncMock(),
        ),
        patch(
            "app.workers.health_checker.ping_and_update_status",
            AsyncMock(return_value=ClusterStatus.OFFLINE),
        ),
    ):
        await worker.tick(app)
    refreshed = await db_session.get(Desktop, desktop_id)
    await db_session.refresh(refreshed)
    assert refreshed.status == DesktopStatus.ERROR
    assert "stuck in 'provisioning'" in refreshed.error_message


async def test_recent_provisioning_not_flipped(
    db_session, patch_session_factory,
):
    cluster = await _make_cluster(db_session)
    recent = datetime.now(timezone.utc) - timedelta(minutes=5)
    _pool, desktop = await _make_pool_with_desktop(
        db_session,
        cluster,
        desktop_status=DesktopStatus.PROVISIONING,
        updated_at=recent,
    )
    desktop_id = desktop.id
    worker = HealthCheckerWorker()
    app = _make_app()
    with (
        patch(
            "app.workers.health_checker.construct_provider", AsyncMock(),
        ),
        patch(
            "app.workers.health_checker.ping_and_update_status",
            AsyncMock(return_value=ClusterStatus.OFFLINE),
        ),
    ):
        await worker.tick(app)
    refreshed = await db_session.get(Desktop, desktop_id)
    await db_session.refresh(refreshed)
    assert refreshed.status == DesktopStatus.PROVISIONING


async def test_stuck_check_with_no_stuck_desktops_no_op(
    db_session, patch_session_factory,
):
    """Empty result set → no DB write, no error."""
    worker = HealthCheckerWorker()
    app = _make_app()
    await worker.tick(app)  # nothing in DB; should not raise
