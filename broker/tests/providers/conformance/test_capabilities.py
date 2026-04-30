"""Conformance — capabilities() returns the expected shape."""
from __future__ import annotations

import pytest

from app.providers.base import ConsoleKind, ProviderCapabilities


pytestmark = pytest.mark.conformance


@pytest.mark.asyncio
async def test_capabilities_returns_expected_type(capabilities):
    assert isinstance(capabilities, ProviderCapabilities)


@pytest.mark.asyncio
async def test_capabilities_provider_type_matches(
    capabilities, conformance_config,
):
    assert (
        capabilities.provider_type == conformance_config["_provider_type"]
    )


@pytest.mark.asyncio
async def test_capabilities_console_kinds_non_empty(capabilities):
    """Every provider must support at least one console kind."""
    assert len(capabilities.console_kinds) > 0
    assert all(
        isinstance(k, ConsoleKind) for k in capabilities.console_kinds
    )


@pytest.mark.asyncio
async def test_capabilities_proxmox_supports_novnc(capabilities):
    """Per docs/providers.md → v0 scope: noVNC-compatible providers
    only. Proxmox claims noVNC."""
    if capabilities.provider_type == "proxmox":
        assert ConsoleKind.NOVNC in capabilities.console_kinds


@pytest.mark.asyncio
async def test_capabilities_idempotent(provider):
    """capabilities() may be called multiple times; the Protocol
    docstring says implementations should memoize. We don't enforce
    identity equality (some providers may rebuild the dataclass) but
    the values must match across calls."""
    a = await provider.capabilities()
    b = await provider.capabilities()
    assert a == b


@pytest.mark.asyncio
async def test_capabilities_required_fields_populated(capabilities):
    """All ProviderCapabilities fields exist (frozen dataclass — they
    must be set at construction). The values themselves are
    provider-specific; here we just assert shape."""
    assert isinstance(capabilities.linked_clones, bool)
    assert isinstance(capabilities.full_clones, bool)
    assert isinstance(capabilities.snapshots, bool)
    assert isinstance(capabilities.guest_agent, bool)
    assert isinstance(capabilities.live_migration, bool)
    assert isinstance(capabilities.supports_pool_tags, bool)
    assert isinstance(capabilities.supports_resource_pools, bool)
