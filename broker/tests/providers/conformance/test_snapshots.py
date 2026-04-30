"""Conformance — snapshot operations (create / list / rollback / delete).

Skipped on providers where capabilities().snapshots == False.
"""
from __future__ import annotations

import pytest

from app.providers.base import SnapshotInfo
from app.providers.exceptions import ProviderError


pytestmark = pytest.mark.conformance


@pytest.mark.asyncio
async def test_create_snapshot_succeeds(
    provider, cloned_vm, skip_if_no_snapshots,
):
    """create_snapshot returns a TaskHandle that completes
    successfully."""
    handle = await provider.create_snapshot(
        cloned_vm,
        "test-snap",
        description="conformance test snapshot",
    )
    await provider.wait_for_task(handle, timeout_seconds=120)
    snaps = await provider.list_snapshots(cloned_vm)
    names = {s.name for s in snaps}
    assert "test-snap" in names


@pytest.mark.asyncio
async def test_list_snapshots_includes_description(
    provider, cloned_vm, skip_if_no_snapshots,
):
    handle = await provider.create_snapshot(
        cloned_vm,
        "with-desc",
        description="this is a description",
    )
    await provider.wait_for_task(handle, timeout_seconds=120)
    snaps = await provider.list_snapshots(cloned_vm)
    by_name = {s.name: s for s in snaps}
    assert by_name["with-desc"].description == "this is a description"


@pytest.mark.asyncio
async def test_list_snapshots_returns_typed_results(
    provider, cloned_vm, skip_if_no_snapshots,
):
    snaps = await provider.list_snapshots(cloned_vm)
    for s in snaps:
        assert isinstance(s, SnapshotInfo)
        assert s.name


@pytest.mark.asyncio
async def test_rollback_snapshot_works(
    provider, cloned_vm, skip_if_no_snapshots,
):
    """Snapshot the current state, then verify rollback completes
    without error. Detailed state-verification (e.g. file content
    inside the VM) is out of scope — rollback's correctness is
    assured by Proxmox's own behavior; the test asserts the operation
    completes."""
    handle = await provider.create_snapshot(cloned_vm, "before-change")
    await provider.wait_for_task(handle, timeout_seconds=120)
    rollback_handle = await provider.rollback_snapshot(
        cloned_vm, "before-change",
    )
    await provider.wait_for_task(rollback_handle, timeout_seconds=120)
    # No exception = pass.


@pytest.mark.asyncio
async def test_delete_snapshot_removes_it(
    provider, cloned_vm, skip_if_no_snapshots,
):
    create_handle = await provider.create_snapshot(cloned_vm, "to-delete")
    await provider.wait_for_task(create_handle, timeout_seconds=120)
    delete_handle = await provider.delete_snapshot(cloned_vm, "to-delete")
    await provider.wait_for_task(delete_handle, timeout_seconds=60)
    snaps = await provider.list_snapshots(cloned_vm)
    names = {s.name for s in snaps}
    assert "to-delete" not in names


@pytest.mark.asyncio
async def test_delete_nonexistent_snapshot_raises(
    provider, cloned_vm, skip_if_no_snapshots,
):
    """delete_snapshot on a name that doesn't exist raises some
    ProviderError subtype. Don't pin to NotFound — different providers
    may surface this differently as long as it's caught by the
    ProviderError base."""
    with pytest.raises(ProviderError):
        handle = await provider.delete_snapshot(
            cloned_vm, "no-such-snapshot",
        )
        await provider.wait_for_task(handle, timeout_seconds=60)
