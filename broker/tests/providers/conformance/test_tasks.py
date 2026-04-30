"""Conformance — TaskHandle, get_task_status, wait_for_task semantics.

Tasks are produced by every long-running provider operation (clone,
destroy, snapshot, power transitions). The conformance tests verify
the polling primitives work consistently.
"""
from __future__ import annotations

import pytest

from app.providers.base import CloneRequest, TaskStatus, VMRef
from app.providers.exceptions import (
    ProviderTaskError,
    ProviderTimeoutError,
)


pytestmark = pytest.mark.conformance


@pytest.mark.asyncio
async def test_wait_for_task_returns_success_status(provider, cloned_vm):
    """wait_for_task on a completed start operation returns
    TaskStatus(state=stopped, success=True)."""
    handle = await provider.start_vm(cloned_vm)
    status = await provider.wait_for_task(handle, timeout_seconds=120)
    assert isinstance(status, TaskStatus)
    assert status.state == "stopped"
    assert status.success is True
    assert status.error_message is None


@pytest.mark.asyncio
async def test_get_task_status_running_then_stopped(provider, cloned_vm):
    """A long-running operation produces a handle we can poll while
    it's running; status transitions from running to stopped."""
    start_handle = await provider.start_vm(cloned_vm)
    await provider.wait_for_task(start_handle, timeout_seconds=120)

    # Hard stop generates a quick task; clone would be slower but the
    # VM doesn't exist yet at clone time. Stop is fine for this test.
    stop_handle = await provider.stop_vm(cloned_vm)
    # Poll once; on a fast cluster it may already be stopped.
    initial = await provider.get_task_status(stop_handle)
    assert initial.state in ("running", "stopped")
    final = await provider.wait_for_task(stop_handle, timeout_seconds=60)
    assert final.state == "stopped"


@pytest.mark.asyncio
async def test_wait_for_task_timeout_raises(
    provider, conformance_config, template_ref, vmid_allocator,
):
    """wait_for_task with an aggressively short timeout on a clone
    raises ProviderTimeoutError. Clone is the slowest standard
    operation; 1-second timeout against any non-empty template is
    guaranteed to time out."""
    # Use the allocator so the leftover VM lands in the configured
    # range — no risk of collision with admin VMs outside the range.
    vmid = vmid_allocator.next()
    handle = await provider.clone_vm(CloneRequest(
        source_ref=template_ref,
        new_name=f"timeout-test-{vmid}",
        target_node=conformance_config["default_node"],
        target_storage=conformance_config.get("test_storage"),
        provider_opts={"newid": vmid},
    ))
    try:
        with pytest.raises(ProviderTimeoutError):
            await provider.wait_for_task(handle, timeout_seconds=1)
    finally:
        # Best-effort: wait for the actual completion + cleanup.
        new_ref = VMRef(
            provider_type=provider.provider_type,
            data={
                "node": conformance_config["default_node"],
                "vmid": vmid,
            },
        )
        try:
            await provider.wait_for_task(handle, timeout_seconds=300)
            destroy_handle = await provider.destroy_vm(
                new_ref, purge=True,
            )
            await provider.wait_for_task(
                destroy_handle, timeout_seconds=300,
            )
        except Exception:
            pass


@pytest.mark.asyncio
async def test_wait_for_task_failure_raises_task_error(
    provider, template_ref,
):
    """A clone with a duplicate newid (the template's own VMID)
    produces a failed task; wait_for_task raises ProviderTaskError."""
    template_vmid = template_ref.data["vmid"]
    handle = await provider.clone_vm(CloneRequest(
        source_ref=template_ref,
        new_name="duplicate-newid-test",
        provider_opts={"newid": template_vmid},
    ))
    with pytest.raises(ProviderTaskError):
        await provider.wait_for_task(handle, timeout_seconds=120)


@pytest.mark.asyncio
async def test_get_task_status_returns_typed_result(provider, cloned_vm):
    """get_task_status returns a TaskStatus with the correct shape."""
    handle = await provider.start_vm(cloned_vm)
    status = await provider.get_task_status(handle)
    assert isinstance(status, TaskStatus)
    # state is one of the literal values.
    assert status.state in ("running", "stopped")
    # Wait for completion before the cleanup fixture runs.
    await provider.wait_for_task(handle, timeout_seconds=120)
