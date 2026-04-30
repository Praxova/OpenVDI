"""Conformance — VM lifecycle operations.

Each test gets a fresh cloned VM via the cloned_vm fixture. Tests
exercise the full lifecycle: power on/off/reboot/shutdown, status
queries, configure, list_vms, destroy.
"""
from __future__ import annotations

import asyncio

import pytest

from app.providers.base import VMConfig, VMRef, VMStatus
from app.providers.exceptions import ProviderNotFoundError


pytestmark = pytest.mark.conformance


@pytest.mark.asyncio
async def test_clone_succeeds_and_returns_stopped_vm(provider, cloned_vm):
    """The cloned_vm fixture clones a VM. Verify it exists in stopped
    state."""
    status = await provider.get_vm_status(cloned_vm)
    assert isinstance(status, VMStatus)
    assert status.power_state == "stopped"
    # The new VM should reference the same node + vmid we cloned to.
    assert status.ref == cloned_vm


@pytest.mark.asyncio
async def test_get_vm_status_returns_expected_fields(provider, cloned_vm):
    status = await provider.get_vm_status(cloned_vm)
    assert status.name  # non-empty
    assert status.cpu_cores > 0
    assert status.memory_bytes > 0
    assert isinstance(status.is_template, bool)
    assert status.is_template is False  # we cloned, didn't template-make
    assert isinstance(status.tags, frozenset)


@pytest.mark.asyncio
async def test_start_then_status_running(provider, cloned_vm):
    handle = await provider.start_vm(cloned_vm)
    await provider.wait_for_task(handle, timeout_seconds=120)
    status = await provider.get_vm_status(cloned_vm)
    assert status.power_state == "running"


@pytest.mark.asyncio
async def test_stop_then_status_stopped(provider, cloned_vm):
    """Hard-stop a running VM."""
    start_handle = await provider.start_vm(cloned_vm)
    await provider.wait_for_task(start_handle, timeout_seconds=120)
    stop_handle = await provider.stop_vm(cloned_vm)
    await provider.wait_for_task(stop_handle, timeout_seconds=60)
    status = await provider.get_vm_status(cloned_vm)
    assert status.power_state == "stopped"


@pytest.mark.asyncio
async def test_shutdown_with_force_falls_back_to_stop(
    provider, cloned_vm, capabilities,
):
    """Shutdown with force=True should escalate to stop if the guest
    doesn't respond. We don't have a guest yet (template likely needs
    the agent up first); test that the operation completes regardless
    of the underlying path."""
    if not capabilities.guest_agent:
        pytest.skip("guest agent required for shutdown")
    start_handle = await provider.start_vm(cloned_vm)
    await provider.wait_for_task(start_handle, timeout_seconds=120)
    # Wait for agent to come up before requesting shutdown.
    await _wait_for_agent(provider, cloned_vm, timeout=120)
    handle = await provider.shutdown_vm(
        cloned_vm, timeout_seconds=60, force=True,
    )
    await provider.wait_for_task(handle, timeout_seconds=180)
    status = await provider.get_vm_status(cloned_vm)
    assert status.power_state == "stopped"


@pytest.mark.asyncio
async def test_reboot_keeps_vm_running(provider, cloned_vm, capabilities):
    if not capabilities.guest_agent:
        pytest.skip("reboot via agent requires guest agent")
    start_handle = await provider.start_vm(cloned_vm)
    await provider.wait_for_task(start_handle, timeout_seconds=120)
    await _wait_for_agent(provider, cloned_vm, timeout=120)

    handle = await provider.reboot_vm(cloned_vm)
    await provider.wait_for_task(handle, timeout_seconds=180)

    # Reboot completes; VM should be running.
    status = await provider.get_vm_status(cloned_vm)
    assert status.power_state == "running"


@pytest.mark.asyncio
async def test_list_vms_includes_cloned(provider, cloned_vm):
    vms = await provider.list_vms()
    refs = {v.ref for v in vms}
    assert cloned_vm in refs


@pytest.mark.asyncio
async def test_configure_vm_applies_changes(provider, cloned_vm):
    """configure_vm changes CPU + memory; subsequent get_vm_status
    reflects the change."""
    new_cpus = 4
    new_mem_mb = 2048
    config = VMConfig(cpu_cores=new_cpus, memory_mb=new_mem_mb)
    handle = await provider.configure_vm(cloned_vm, config)
    if handle is not None:
        await provider.wait_for_task(handle, timeout_seconds=60)
    status = await provider.get_vm_status(cloned_vm)
    assert status.cpu_cores == new_cpus
    assert status.memory_bytes == new_mem_mb * 1024 * 1024


@pytest.mark.asyncio
async def test_destroy_removes_vm(provider, cloned_vm, conformance_config):
    """Destroy a stopped VM. Subsequent get_vm_status raises
    ProviderNotFoundError."""
    timeout = conformance_config.get("task_timeout_seconds", 600)
    handle = await provider.destroy_vm(cloned_vm, purge=True)
    await provider.wait_for_task(handle, timeout_seconds=timeout)
    with pytest.raises(ProviderNotFoundError):
        await provider.get_vm_status(cloned_vm)


@pytest.mark.asyncio
async def test_get_vm_status_unknown_vm_raises(
    provider, conformance_config,
):
    """Querying a VMID outside the test range yields
    ProviderNotFoundError."""
    bogus = VMRef(
        provider_type=provider.provider_type,
        data={
            "node": conformance_config["default_node"],
            "vmid": 999_999,
        },
    )
    with pytest.raises(ProviderNotFoundError):
        await provider.get_vm_status(bogus)


# ── Helpers ─────────────────────────────────────────────────


async def _wait_for_agent(provider, ref, *, timeout: int) -> None:
    """Poll agent_ping until True or timeout. Used in lifecycle tests
    that need the guest up to send shutdown/reboot."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if await provider.agent_ping(ref):
            return
        await asyncio.sleep(2.0)
    pytest.fail(f"agent did not respond on {ref!r} within {timeout}s")
