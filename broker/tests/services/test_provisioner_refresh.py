"""Tests for refresh_desktop + delete_desktop_on_logoff (M4-06).

Provider is fully mocked at the HypervisorProvider Protocol boundary.
DB is real (transactional fixture from tests/_db.py) so commit() inside
the function under test exercises the actual SQL. Each test sets up a
Cluster + Template + Pool + Desktop fixture, runs the function, and
asserts on both the desktop row's final state and the order/arguments
of provider calls.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.models import Cluster, Desktop, DesktopStatus, Pool, PoolType, Template
from app.providers.base import VMRef, VMStatus
from app.providers.exceptions import ProviderTaskError
from app.services import provisioner as provisioner_module
from app.services.provisioner import (
    DesktopNotFound,
    InvalidDesktopState,
    delete_desktop_on_logoff,
    refresh_desktop,
)

# Re-export the transactional db_session fixture so tests in this file
# pick it up. pytest discovers fixtures by name in the test's module
# namespace and parents (conftest); a sibling-import is the easiest
# wiring without touching conftest.
from tests._db import db_session  # noqa: F401


# ── Fixture builders ─────────────────────────────────────────


async def _make_pool(
    db_session,
    *,
    pool_type: PoolType = PoolType.NONPERSISTENT,
    refresh_on_logoff: bool = True,
    delete_on_logoff: bool = False,
) -> Pool:
    """Insert a Cluster + Template + Pool. Returns the Pool."""
    cluster = Cluster(
        name=f"test-cluster-{uuid4().hex[:8]}",
        provider_type="proxmox",
        api_url="https://test.example.com:8006",
        token_id="test@pve!unit",
        token_secret="ciphertext-placeholder",
    )
    db_session.add(cluster)
    await db_session.flush()

    template = Template(
        cluster_id=cluster.id,
        name=f"test-tpl-{uuid4().hex[:8]}",
        pve_vmid=9000,
        pve_node="pve1",
        os_type="windows11",
    )
    db_session.add(template)
    await db_session.flush()

    pool = Pool(
        name=f"test-pool-{uuid4().hex[:8]}",
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
    db_session.add(pool)
    await db_session.flush()
    return pool


async def _make_desktop(
    db_session,
    pool: Pool,
    *,
    status: DesktopStatus = DesktopStatus.ASSIGNED,
    assigned_user: str | None = "alice",
    assignment_type: str | None = "floating",
) -> Desktop:
    desktop = Desktop(
        pool_id=pool.id,
        pve_vmid=5001,
        pve_node="pve1",
        name="TEST-001",
        status=status,
        assigned_user=assigned_user,
        assignment_type=assignment_type,
    )
    db_session.add(desktop)
    await db_session.flush()
    return desktop


def _vmstatus(power_state: str) -> VMStatus:
    return VMStatus(
        ref=VMRef(
            provider_type="proxmox",
            data={"node": "pve1", "vmid": 5001},
        ),
        name="TEST-001",
        power_state=power_state,  # type: ignore[arg-type]
        cpu_cores=2,
        memory_bytes=4 * 1024**3,
        disk_bytes=40 * 1024**3,
        uptime_seconds=0,
        is_template=False,
        guest_agent_configured=True,
        lock=None,
        tags=frozenset(),
        raw={},
    )


def _make_provider(power_state: str = "running") -> MagicMock:
    """Build a HypervisorProvider stub. Default sets up the happy-path
    behavior; tests override per-method."""
    provider = MagicMock()
    provider.provider_type = "proxmox"
    provider.get_vm_status = AsyncMock(return_value=_vmstatus(power_state))
    provider.shutdown_vm = AsyncMock(return_value=MagicMock())
    provider.stop_vm = AsyncMock(return_value=MagicMock())
    provider.start_vm = AsyncMock(return_value=MagicMock())
    provider.destroy_vm = AsyncMock(return_value=MagicMock())
    provider.rollback_snapshot = AsyncMock(return_value=MagicMock())
    provider.wait_for_task = AsyncMock()
    provider.agent_ping = AsyncMock(return_value=True)
    return provider


# ── refresh_desktop tests ───────────────────────────────────


async def test_refresh_happy_path(db_session):
    """Running VM → shutdown → wait stopped → rollback → start →
    agent up → 'available' + floating cleared."""
    pool = await _make_pool(db_session)
    desktop = await _make_desktop(db_session, pool)
    provider = _make_provider(power_state="running")
    # _wait_for_stopped polls get_vm_status after the shutdown task
    # completes. First call is the pre-shutdown check (running); second
    # is the post-shutdown poll (stopped).
    provider.get_vm_status.side_effect = [
        _vmstatus("running"),
        _vmstatus("stopped"),
    ]

    result = await refresh_desktop(
        session=db_session, provider=provider, desktop_id=desktop.id,
    )

    assert result is not None
    assert result.status == DesktopStatus.AVAILABLE
    assert result.assigned_user is None
    assert result.assignment_type is None
    assert result.error_message is None
    provider.shutdown_vm.assert_called_once()
    provider.rollback_snapshot.assert_called_once_with(
        VMRef(provider_type="proxmox",
              data={"node": "pve1", "vmid": 5001}),
        "openvdi-base",
    )
    provider.start_vm.assert_called_once()
    provider.agent_ping.assert_called()


async def test_refresh_skips_shutdown_when_already_stopped(db_session):
    """power_state='stopped' before refresh → shutdown_vm not called."""
    pool = await _make_pool(db_session)
    desktop = await _make_desktop(db_session, pool)
    provider = _make_provider(power_state="stopped")
    # All get_vm_status calls return stopped (Step 2 check + _wait_for_stopped).
    provider.get_vm_status.return_value = _vmstatus("stopped")

    result = await refresh_desktop(
        session=db_session, provider=provider, desktop_id=desktop.id,
    )

    assert result is not None
    assert result.status == DesktopStatus.AVAILABLE
    provider.shutdown_vm.assert_not_called()
    provider.rollback_snapshot.assert_called_once()


async def test_refresh_rollback_fails_marks_error(db_session):
    """ProviderTaskError on rollback → status=ERROR + floating cleared."""
    pool = await _make_pool(db_session)
    desktop = await _make_desktop(db_session, pool)
    provider = _make_provider(power_state="stopped")
    provider.get_vm_status.return_value = _vmstatus("stopped")
    provider.rollback_snapshot.side_effect = ProviderTaskError(
        "rollback failed: storage full", "proxmox", {},
    )

    result = await refresh_desktop(
        session=db_session, provider=provider, desktop_id=desktop.id,
    )

    assert result is not None
    assert result.status == DesktopStatus.ERROR
    assert "rollback failed" in result.error_message
    # Per R3: floating assignment cleared even on failure.
    assert result.assigned_user is None
    assert result.assignment_type is None


async def test_refresh_agent_timeout_marks_error(db_session, monkeypatch):
    """agent_ping never returns True → asyncio.TimeoutError → ERROR.

    Squeeze the timeout constants so the test completes in ~1s instead
    of the production 90s budget.
    """
    monkeypatch.setattr(provisioner_module, "_AGENT_POLL_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(provisioner_module, "_AGENT_POLL_INTERVAL_SECONDS", 0.05)

    pool = await _make_pool(db_session)
    desktop = await _make_desktop(db_session, pool)
    provider = _make_provider(power_state="stopped")
    provider.get_vm_status.return_value = _vmstatus("stopped")
    provider.agent_ping.return_value = False  # never responds

    result = await refresh_desktop(
        session=db_session, provider=provider, desktop_id=desktop.id,
    )

    assert result is not None
    assert result.status == DesktopStatus.ERROR
    assert "agent" in result.error_message.lower()


async def test_refresh_persistent_pool_raises_invalid_state(db_session):
    pool = await _make_pool(db_session, pool_type=PoolType.PERSISTENT)
    desktop = await _make_desktop(db_session, pool)

    with pytest.raises(InvalidDesktopState, match="non-persistent"):
        await refresh_desktop(
            session=db_session, provider=_make_provider(),
            desktop_id=desktop.id,
        )


async def test_refresh_pool_flag_disabled_raises(db_session):
    pool = await _make_pool(db_session, refresh_on_logoff=False)
    desktop = await _make_desktop(db_session, pool)

    with pytest.raises(InvalidDesktopState, match="refresh_on_logoff=false"):
        await refresh_desktop(
            session=db_session, provider=_make_provider(),
            desktop_id=desktop.id,
        )


async def test_refresh_unknown_desktop_raises_not_found(db_session):
    with pytest.raises(DesktopNotFound):
        await refresh_desktop(
            session=db_session, provider=_make_provider(),
            desktop_id=uuid4(),
        )


async def test_refresh_in_flight_desktop_rejected(db_session):
    """A desktop already in PROVISIONING is the in-flight-cycle signal —
    refusing to start a second cycle prevents racing log lines."""
    pool = await _make_pool(db_session)
    desktop = await _make_desktop(
        db_session, pool, status=DesktopStatus.PROVISIONING,
    )
    with pytest.raises(InvalidDesktopState, match="provisioning"):
        await refresh_desktop(
            session=db_session, provider=_make_provider(),
            desktop_id=desktop.id,
        )


# ── delete_desktop_on_logoff tests ───────────────────────────


async def test_delete_happy_path(db_session):
    """Running VM → hard stop → wait stopped → destroy → row deleted."""
    pool = await _make_pool(
        db_session, refresh_on_logoff=False, delete_on_logoff=True,
    )
    desktop = await _make_desktop(db_session, pool)
    desktop_id = desktop.id
    provider = _make_provider(power_state="running")
    provider.get_vm_status.side_effect = [
        _vmstatus("running"),
        _vmstatus("stopped"),
    ]

    result = await delete_desktop_on_logoff(
        session=db_session, provider=provider, desktop_id=desktop_id,
    )

    assert result is None
    provider.stop_vm.assert_called_once()
    provider.destroy_vm.assert_called_once_with(
        VMRef(provider_type="proxmox",
              data={"node": "pve1", "vmid": 5001}),
        purge=True,
    )
    # Row is gone.
    # Use a fresh query (not session.get) — the deleted instance is
    # cached in the session's identity map; a SELECT bypasses that.
    fresh = await db_session.execute(
        text("SELECT id FROM desktops WHERE id = :id"),
        {"id": desktop_id},
    )
    assert fresh.first() is None


async def test_delete_destroy_fails_marks_error(db_session):
    """ProviderTaskError on destroy → row stays, status=ERROR."""
    pool = await _make_pool(
        db_session, refresh_on_logoff=False, delete_on_logoff=True,
    )
    desktop = await _make_desktop(db_session, pool)
    desktop_id = desktop.id
    provider = _make_provider(power_state="stopped")
    provider.get_vm_status.return_value = _vmstatus("stopped")
    provider.destroy_vm.side_effect = ProviderTaskError(
        "destroy failed: VM locked", "proxmox", {},
    )

    await delete_desktop_on_logoff(
        session=db_session, provider=provider, desktop_id=desktop_id,
    )

    survived = await db_session.get(Desktop, desktop_id)
    assert survived is not None
    assert survived.status == DesktopStatus.ERROR
    assert "destroy failed" in survived.error_message


async def test_delete_concurrent_caller_rejected(db_session):
    """status=DELETING → InvalidDesktopState; the other caller owns it."""
    pool = await _make_pool(
        db_session, refresh_on_logoff=False, delete_on_logoff=True,
    )
    desktop = await _make_desktop(
        db_session, pool, status=DesktopStatus.DELETING,
    )
    with pytest.raises(InvalidDesktopState, match="already being deleted"):
        await delete_desktop_on_logoff(
            session=db_session, provider=_make_provider(),
            desktop_id=desktop.id,
        )


async def test_delete_pool_flag_disabled_raises(db_session):
    pool = await _make_pool(db_session, delete_on_logoff=False)
    desktop = await _make_desktop(db_session, pool)
    with pytest.raises(InvalidDesktopState, match="delete_on_logoff=false"):
        await delete_desktop_on_logoff(
            session=db_session, provider=_make_provider(),
            desktop_id=desktop.id,
        )
