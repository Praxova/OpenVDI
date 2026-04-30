"""Conformance — guest agent operations.

Skipped on providers where capabilities().guest_agent == False.

These tests require the test template to have a working agent
installation. See tests/providers/conformance/README.md for setup.
"""
from __future__ import annotations

import asyncio

import pytest

from app.providers.base import NetworkInterface, OSInfo
from app.providers.exceptions import ProviderError


pytestmark = pytest.mark.conformance


# ── Helpers ─────────────────────────────────────────────────


async def _start_and_wait_agent(provider, ref, timeout=180):
    """Power on the VM and wait for agent_ping to succeed."""
    handle = await provider.start_vm(ref)
    await provider.wait_for_task(handle, timeout_seconds=120)
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if await provider.agent_ping(ref):
            return
        await asyncio.sleep(2.0)
    pytest.fail(f"agent did not come up within {timeout}s")


# ── Tests ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_ping_returns_true_after_boot(
    provider, cloned_vm, skip_if_no_guest_agent,
):
    await _start_and_wait_agent(provider, cloned_vm)
    assert await provider.agent_ping(cloned_vm) is True


@pytest.mark.asyncio
async def test_agent_ping_returns_false_on_stopped(
    provider, cloned_vm, skip_if_no_guest_agent,
):
    """Per the Protocol: agent_ping MUST NOT raise on 'agent
    unreachable' — that's a False return. A stopped VM trivially
    has no agent."""
    # cloned_vm starts stopped (per the lifecycle test).
    result = await provider.agent_ping(cloned_vm)
    assert result is False


@pytest.mark.asyncio
async def test_agent_get_users_clean_boot_empty(
    provider, cloned_vm, skip_if_no_guest_agent,
):
    """A freshly-booted VM with no user logged in has empty
    agent_get_users. (Test template MUST be configured for autologon
    DISABLED — see README.)"""
    await _start_and_wait_agent(provider, cloned_vm)
    users = await provider.agent_get_users(cloned_vm)
    assert users == []


@pytest.mark.asyncio
async def test_agent_get_osinfo_returns_typed(
    provider, cloned_vm, skip_if_no_guest_agent,
):
    await _start_and_wait_agent(provider, cloned_vm)
    info = await provider.agent_get_osinfo(cloned_vm)
    assert isinstance(info, OSInfo)
    assert info.name      # non-empty
    assert info.version   # non-empty


@pytest.mark.asyncio
async def test_agent_get_network_returns_at_least_one_interface(
    provider, cloned_vm, skip_if_no_guest_agent,
):
    await _start_and_wait_agent(provider, cloned_vm)
    interfaces = await provider.agent_get_network(cloned_vm)
    assert len(interfaces) >= 1
    for iface in interfaces:
        assert isinstance(iface, NetworkInterface)
        assert iface.name


@pytest.mark.asyncio
async def test_agent_calls_on_stopped_vm_raise_or_return_default(
    provider, cloned_vm, skip_if_no_guest_agent,
):
    """Agent calls on a stopped VM either return a default (empty
    list, sentinel OSInfo) OR raise ProviderError. Don't pin which
    — the contract is "don't crash the broker"; either path is
    acceptable."""
    try:
        users = await provider.agent_get_users(cloned_vm)
        assert users == []  # acceptable: empty
    except ProviderError:
        pass  # also acceptable: raise
