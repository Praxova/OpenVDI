"""Conftest for the provider conformance suite.

Provides the --provider command-line option, loads conformance.yaml,
constructs a HypervisorProvider, and exposes per-test fixtures
(template ref, vmid allocator, cleaned-cloned-vm).

The suite is opt-in: tests are decorated with @pytest.mark.conformance
and the default pytest run deselects them via pyproject.toml's
addopts. To run, pass --provider=<provider_type>.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
import yaml

from app.providers import get_provider_class
from app.providers.base import (
    CloneRequest,
    HypervisorProvider,
    ProviderCapabilities,
    VMRef,
)

logger = logging.getLogger(__name__)


# ── Pytest options ────────────────────────────────────────────


def pytest_addoption(parser):
    """Register the --provider CLI option that opts into the suite."""
    parser.addoption(
        "--provider",
        action="store",
        default=None,
        help=(
            "Provider type to test (e.g. 'proxmox'). Required to run "
            "the conformance suite; without it, conformance tests are "
            "deselected via the @pytest.mark.conformance marker."
        ),
    )


def pytest_collection_modifyitems(config, items):
    """If --provider is set, ENABLE conformance tests by clearing the
    default 'not conformance' deselect (set in pyproject.toml addopts).
    Without --provider, the addopts deselect is left intact and the
    suite is silently skipped from default runs.
    """
    if config.getoption("--provider") is None:
        return
    config.option.markexpr = ""


# ── Configuration loader ─────────────────────────────────────


@pytest.fixture(scope="session")
def conformance_config(pytestconfig) -> dict:
    """Load broker/tests/conformance.yaml and return the dict for the
    requested --provider. Skips the entire suite if config is missing
    or the provider type isn't in the file."""
    provider_type = pytestconfig.getoption("--provider")
    if provider_type is None:
        pytest.skip("--provider not specified")

    # __file__ → broker/tests/providers/conformance/conftest.py.
    # parents[2] → broker/tests/.
    path = Path(__file__).resolve().parents[2] / "conformance.yaml"
    if not path.exists():
        pytest.skip(
            f"{path} not found — copy "
            f"{path.parent}/conformance.yaml.example and fill in "
            f"credentials"
        )

    with path.open() as f:
        full_config = yaml.safe_load(f)

    if not isinstance(full_config, dict) or provider_type not in full_config:
        pytest.skip(
            f"--provider={provider_type} but no '{provider_type}' "
            f"block in {path}"
        )

    block = dict(full_config[provider_type])
    block["_provider_type"] = provider_type
    return block


# ── Provider factory ─────────────────────────────────────────


@pytest_asyncio.fixture(scope="session")
async def provider(
    conformance_config,
) -> AsyncGenerator[HypervisorProvider, None]:
    """Construct one HypervisorProvider for the entire suite session.

    Session-scoped because constructing the provider opens HTTP/auth
    state per cluster — wasteful per-test. Tests share one provider;
    isolation comes from per-test VMID allocation.

    Construction is direct kwargs (api_url, token_id, ...) — bypasses
    the broker's `cluster_service.construct_provider` so the suite
    never touches the broker DB.
    """
    provider_type = conformance_config["_provider_type"]
    provider_class = get_provider_class(provider_type)

    instance = provider_class(
        api_url=conformance_config["api_url"],
        token_id=conformance_config["token_id"],
        token_secret=conformance_config["token_secret"],
        verify_ssl=conformance_config.get("verify_ssl", False),
    )

    yield instance

    await instance.close()


# ── VMID allocator ───────────────────────────────────────────


class _VMIDAllocator:
    """Hands out integers from the configured test range. NOT thread
    safe; tests run sequentially per pytest's default. If the range
    is exhausted, fails the next test that calls .next() with a
    clear message."""

    def __init__(self, start: int, end: int) -> None:
        self._cursor = start
        self._end = end

    def next(self) -> int:
        if self._cursor > self._end:
            pytest.fail(
                f"Conformance test VMID range exhausted "
                f"({self._cursor} > {self._end}). Clean up stragglers "
                f"from prior runs and adjust test_pool_vmid_range in "
                f"conformance.yaml if needed."
            )
        vmid = self._cursor
        self._cursor += 1
        return vmid


@pytest.fixture(scope="session")
def vmid_allocator(conformance_config) -> _VMIDAllocator:
    rng = conformance_config["test_pool_vmid_range"]
    return _VMIDAllocator(rng["start"], rng["end"])


# ── Capabilities + skip helpers ─────────────────────────────


@pytest_asyncio.fixture(scope="session")
async def capabilities(provider) -> ProviderCapabilities:
    """Memoize the provider's capability declaration. The Protocol
    docstring guarantees memoization on the provider side; we pin
    one query per session."""
    return await provider.capabilities()


@pytest.fixture
def skip_if_no_snapshots(capabilities):
    if not capabilities.snapshots:
        pytest.skip("provider does not support snapshots")


@pytest.fixture
def skip_if_no_guest_agent(capabilities):
    if not capabilities.guest_agent:
        pytest.skip("provider does not support guest agent")


# ── Template + cloned VM fixtures ───────────────────────────


@pytest.fixture
def template_ref(conformance_config, provider) -> VMRef:
    """The VMRef of the test template. Read-only — never modified by
    tests. Shape of `data` matches what the provider's helpers produce
    (Proxmox: dict {"node", "vmid"}). When other providers land, this
    fixture grows a per-provider-type branch.
    """
    return _make_vm_ref(
        provider.provider_type,
        conformance_config["default_node"],
        conformance_config["test_template_vmid"],
    )


@pytest_asyncio.fixture
async def cloned_vm(
    provider, conformance_config, template_ref, vmid_allocator,
) -> AsyncGenerator[VMRef, None]:
    """Clone from the test template. Yield the new VMRef. On teardown,
    destroy the VM (best-effort).

    Each test that uses this fixture gets a fresh VM. Tests can power
    it on, modify config, snapshot it, etc.; the fixture takes care
    of cleanup regardless of test outcome.
    """
    vmid = vmid_allocator.next()
    new_name = f"conformance-{vmid}"
    timeout = conformance_config.get("task_timeout_seconds", 600)

    clone_handle = await provider.clone_vm(CloneRequest(
        source_ref=template_ref,
        new_name=new_name,
        target_node=conformance_config["default_node"],
        target_storage=conformance_config.get("test_storage"),
        provider_opts={"newid": vmid},
    ))
    await provider.wait_for_task(clone_handle, timeout_seconds=timeout)

    new_ref = _make_vm_ref(
        provider.provider_type,
        conformance_config["default_node"],
        vmid,
    )

    try:
        yield new_ref
    finally:
        await _best_effort_destroy(provider, new_ref, timeout=timeout)


# ── Internal helpers ────────────────────────────────────────


def _make_vm_ref(provider_type: str, node: str, vmid: int) -> VMRef:
    """Build a VMRef whose `data` shape matches the named provider's
    convention. Centralized so a future provider with a different
    shape (e.g. vSphere MoRef strings) only needs a branch here.
    """
    if provider_type == "proxmox":
        return VMRef(
            provider_type=provider_type,
            data={"node": node, "vmid": int(vmid)},
        )
    raise NotImplementedError(
        f"_make_vm_ref: unknown provider_type {provider_type!r} — "
        f"add a branch when the provider lands"
    )


async def _best_effort_destroy(
    provider: HypervisorProvider, ref: VMRef, *, timeout: int,
) -> None:
    """Stop + destroy a VM. Logs and continues on any failure —
    cleanup must not mask test failures."""
    try:
        status = await provider.get_vm_status(ref)
        if status.power_state != "stopped":
            stop_handle = await provider.stop_vm(ref)
            await provider.wait_for_task(stop_handle, timeout_seconds=120)
    except Exception as e:
        logger.warning(
            "best-effort stop failed for %r: %s: %s",
            ref, type(e).__name__, e,
        )

    try:
        destroy_handle = await provider.destroy_vm(ref, purge=True)
        await provider.wait_for_task(
            destroy_handle, timeout_seconds=timeout,
        )
    except Exception as e:
        logger.warning(
            "best-effort destroy failed for %r: %s: %s — "
            "manual cleanup needed",
            ref, type(e).__name__, e,
        )
