# OpenVDI Hypervisor Provider Interface

## Overview

OpenVDI is hypervisor-agnostic by design. The broker, workers, and API layer never call a specific hypervisor's API — they call a `HypervisorProvider` interface. Each supported hypervisor is one concrete implementation of this interface.

The first implementation is Proxmox VE (see `providers/proxmox.md`). Additional providers (vSphere, Hyper-V, Nutanix AHV, XCP-ng, OpenStack, etc.) are future work and are out of scope for v0.

**This document defines the abstract interface.** Provider-specific mappings live in `providers/<name>.md`.

## Design Principles

1. **The interface reflects VDI operations, not hypervisor operations.** "Clone a desktop from a template," not "create a linked clone using ref-counted disks." Operational semantics that are genuinely hypervisor-specific are left to the provider, with the interface describing *what* is wanted, not *how* it is achieved.

2. **Providers advertise capabilities; the broker adapts.** Not every hypervisor can do everything. A `capabilities()` method lets the broker query what a given provider supports. Pool creation validates that the target provider can actually provide what the pool configuration asks for.

3. **Opaque references.** Providers return opaque `VMRef` handles rather than exposing internal IDs. For Proxmox, `VMRef` wraps `(node, vmid)`; for vSphere, a managed object reference; for Hyper-V, `(host, name)`. The broker never inspects the contents.

4. **Task handles are first-class.** All long-running operations return a `TaskHandle`. The broker never assumes a shape for async tracking — it calls `get_task_status(handle)` and `wait_for_task(handle)`, and the provider handles its own polling, event streams, or whatever mechanism it uses internally.

5. **Lowest-common-denominator is a failure mode, not a goal.** Where Proxmox has capabilities another provider lacks, the Proxmox provider keeps them. The interface exposes them through `capabilities()` and provider-specific option blobs on relevant methods, not by being dumbed down to the intersection of all hypervisors.

## v0 Scope Constraint: noVNC-Compatible Providers Only

The OpenVDI v0 portal renders VM consoles using noVNC in the browser. Providers that can produce a noVNC-compatible WebSocket console are supported; providers that cannot (e.g. Hyper-V natively) will require a bridging layer (Guacamole, websockify, or similar) before they can be first-class providers.

This constraint is an architectural decision, not an interface limitation. The `ConsoleTicket` type is a sum type that can represent other console kinds (WebMKS, SPICE, RDP) — future portal versions may render them. v0 only implements the noVNC renderer.

## Shared Types

These types are defined in `broker/app/providers/base.py` and imported by all providers and consumers.

```python
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, Protocol, ClassVar


# ── Identity & references ─────────────────────────────────────

@dataclass(frozen=True)
class VMRef:
    """Opaque VM reference. Provider-specific contents.

    Providers set `provider_type` so the broker can sanity-check
    that a VMRef came from the expected provider when passing it
    back. The `data` field is provider-defined (tuple, dict, str).
    """
    provider_type: str
    data: Any


@dataclass(frozen=True)
class TaskHandle:
    """Opaque handle for an async operation. Provider-specific."""
    provider_type: str
    data: Any


# ── Capabilities ──────────────────────────────────────────────

class ConsoleKind(str, Enum):
    NOVNC = "novnc"
    WEBMKS = "webmks"
    SPICE = "spice"
    RDP = "rdp"
    KASMVNC = "kasmvnc"


@dataclass(frozen=True)
class ProviderCapabilities:
    """What a provider implementation supports."""
    provider_type: str
    linked_clones: bool          # efficient template → desktop cloning
    full_clones: bool            # fallback when linked isn't available
    snapshots: bool              # create/rollback/delete
    guest_agent: bool            # ping, get-users, exec, etc.
    live_migration: bool         # future-facing
    console_kinds: frozenset[ConsoleKind]
    supports_pool_tags: bool     # metadata tagging of VMs
    supports_resource_pools: bool  # native "pool" organizational grouping


# ── Cluster & placement ───────────────────────────────────────

@dataclass(frozen=True)
class NodeInfo:
    node: str                    # provider-local identifier
    display_name: str
    status: Literal["online", "offline", "maintenance"]
    cpu_cores: int
    memory_bytes: int


@dataclass(frozen=True)
class NodeStatus:
    node: str
    cpu_usage_percent: float
    memory_used_bytes: int
    memory_total_bytes: int
    uptime_seconds: int
    kernel_version: str | None


@dataclass(frozen=True)
class StorageInfo:
    name: str
    storage_type: str            # provider-specific (lvm-thin, nfs, vmfs, ...)
    shared: bool                 # shared across nodes?
    total_bytes: int
    used_bytes: int
    content_types: frozenset[str]  # provider-specific (images, rootdir, ...)


# ── VMs ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class CloneRequest:
    """Abstract clone request.

    Clone mode is always 'linked if source is a template, full otherwise'
    for Proxmox; other providers map this to their closest equivalent.
    Pool configuration may restrict acceptable providers to those whose
    native clone semantics match VDI requirements (fast, space-efficient).
    """
    source_ref: VMRef            # must reference a template
    new_name: str
    target_node: str | None = None
    target_storage: str | None = None
    target_pool: str | None = None
    description: str | None = None
    # Provider-specific overrides (e.g. Proxmox 'newid' for VMID allocation).
    # The broker's VMID allocator chooses these and passes them through opaquely.
    provider_opts: dict[str, Any] | None = None


PowerState = Literal["running", "stopped", "paused", "unknown"]


@dataclass(frozen=True)
class VMStatus:
    ref: VMRef
    name: str
    power_state: PowerState
    cpu_cores: int
    memory_bytes: int
    disk_bytes: int
    uptime_seconds: int
    is_template: bool
    guest_agent_configured: bool  # configured in VM settings, NOT liveness
    lock: str | None              # provider-specific lock identifier
    tags: frozenset[str]          # normalized to a set regardless of provider
    raw: dict[str, Any]           # full provider response for provider-specific reads


@dataclass(frozen=True)
class VMConfig:
    """Post-clone configuration. All fields optional; only set ones are applied."""
    name: str | None = None
    cpu_cores: int | None = None
    memory_mb: int | None = None
    description: str | None = None
    tags: frozenset[str] | None = None
    provider_opts: dict[str, Any] | None = None


# ── Snapshots ─────────────────────────────────────────────────

@dataclass(frozen=True)
class SnapshotInfo:
    name: str
    description: str | None
    created_at: int | None        # unix timestamp; None for synthetic entries
    parent: str | None            # parent snapshot name; None if base
    includes_ram: bool            # vmstate saved


# ── Guest agent ───────────────────────────────────────────────

@dataclass(frozen=True)
class GuestUser:
    username: str
    login_time: int | None        # unix timestamp
    domain: str | None            # Windows-only; None on Linux providers


@dataclass(frozen=True)
class OSInfo:
    name: str                     # "Microsoft Windows 11", "Ubuntu"
    version: str                  # "24H2", "24.04"
    kernel_release: str | None
    architecture: str | None


@dataclass(frozen=True)
class NetworkInterface:
    name: str                     # "eth0", "Ethernet 2"
    mac_address: str | None
    ip_addresses: list[str]       # may be empty; v4 and v6 mixed
    is_up: bool


@dataclass(frozen=True)
class ExecStatus:
    exited: bool
    exit_code: int | None
    stdout: str
    stderr: str


# ── Console tickets ───────────────────────────────────────────

@dataclass(frozen=True)
class NoVNCTicket:
    kind: Literal[ConsoleKind.NOVNC] = ConsoleKind.NOVNC
    # Full wss:// URL the browser connects to, already URL-encoded.
    websocket_url: str = ""
    # Authentication the browser sends via noVNC's password field.
    password: str = ""
    # TLS cert for self-signed acceptance (optional; may be None).
    cert_pem: str | None = None


@dataclass(frozen=True)
class WebMKSTicket:
    kind: Literal[ConsoleKind.WEBMKS] = ConsoleKind.WEBMKS
    host: str = ""
    port: int = 0
    ticket: str = ""


@dataclass(frozen=True)
class SpiceTicket:
    kind: Literal[ConsoleKind.SPICE] = ConsoleKind.SPICE
    host: str = ""
    port: int = 0
    tls_port: int | None = None
    password: str = ""
    proxy: str | None = None


@dataclass(frozen=True)
class RDPTicket:
    kind: Literal[ConsoleKind.RDP] = ConsoleKind.RDP
    host: str = ""
    port: int = 3389
    username: str | None = None
    password: str | None = None
    # If connecting via a Guacamole/RDP gateway, gateway info goes here.
    gateway_host: str | None = None
    gateway_token: str | None = None


ConsoleTicket = NoVNCTicket | WebMKSTicket | SpiceTicket | RDPTicket


# ── Task tracking ─────────────────────────────────────────────

TaskState = Literal["running", "stopped"]


@dataclass(frozen=True)
class TaskStatus:
    state: TaskState
    success: bool | None          # None while running; bool once stopped
    error_message: str | None     # set when success is False
    raw: dict[str, Any]
```

## HypervisorProvider Protocol

```python
class HypervisorProvider(Protocol):
    """Abstract hypervisor provider interface.

    One instance per registered cluster. Implementations manage their
    own HTTP/RPC client(s), connection pools, authentication, and retry
    logic. The broker treats instances as stateful objects with the
    lifetime of the cluster registration.
    """

    provider_type: ClassVar[str]  # "proxmox", "vsphere", etc.

    # ── Capabilities & lifecycle ──────────────────────────────

    async def capabilities(self) -> ProviderCapabilities:
        """Static capability declaration. May be called repeatedly;
        implementations should memoize."""

    async def ping(self) -> bool:
        """Quick liveness check against the provider's API."""

    async def close(self) -> None:
        """Release connections and resources. Called at broker shutdown
        or cluster deregistration."""

    # ── Cluster & placement ───────────────────────────────────

    async def list_nodes(self) -> list[NodeInfo]: ...
    async def get_node_status(self, node: str) -> NodeStatus: ...
    async def list_storage(self, node: str) -> list[StorageInfo]: ...

    # ── VM lifecycle ──────────────────────────────────────────

    async def clone_vm(self, req: CloneRequest) -> TaskHandle:
        """Clone a template into a new desktop VM.

        The source VM (req.source_ref) MUST be a template. Providers
        that cannot guarantee efficient cloning from non-template VMs
        should raise ProviderError rather than fall back to full copy.
        """

    async def start_vm(self, ref: VMRef) -> TaskHandle: ...

    async def stop_vm(self, ref: VMRef) -> TaskHandle:
        """Forceful stop (pull the plug). No guest coordination."""

    async def shutdown_vm(
        self, ref: VMRef, timeout_seconds: int = 120, force: bool = False
    ) -> TaskHandle:
        """Graceful guest-coordinated shutdown. If `force` is True and
        the guest doesn't respond within `timeout_seconds`, the provider
        escalates to a hard stop."""

    async def reboot_vm(self, ref: VMRef) -> TaskHandle: ...

    async def destroy_vm(self, ref: VMRef, purge: bool = True) -> TaskHandle:
        """Delete the VM and its owned storage. VM must be stopped.

        Providers are responsible for retrying transient lock/contention
        failures internally according to their own semantics, and for
        raising ProviderLockError if retries are exhausted."""

    async def get_vm_status(self, ref: VMRef) -> VMStatus: ...
    async def list_vms(self, node: str | None = None) -> list[VMStatus]: ...
    async def configure_vm(self, ref: VMRef, config: VMConfig) -> TaskHandle | None:
        """Apply config changes. Returns a TaskHandle for async changes,
        None for changes applied synchronously by the provider."""

    # ── Snapshots ─────────────────────────────────────────────
    # Operate on desktop VMs, never on templates. Consumers must check
    # capabilities().snapshots before calling; providers without snapshot
    # support raise NotImplementedError.

    async def create_snapshot(
        self, ref: VMRef, name: str,
        description: str | None = None, include_ram: bool = False,
    ) -> TaskHandle: ...

    async def rollback_snapshot(self, ref: VMRef, name: str) -> TaskHandle: ...
    async def list_snapshots(self, ref: VMRef) -> list[SnapshotInfo]: ...
    async def delete_snapshot(self, ref: VMRef, name: str) -> TaskHandle: ...

    # ── Guest agent ───────────────────────────────────────────
    # Consumers must check capabilities().guest_agent. Providers without
    # guest agent support raise NotImplementedError.

    async def agent_ping(self, ref: VMRef) -> bool:
        """Returns True if agent responds. Providers MUST NOT raise on
        'agent unreachable' — that's a False return, not an error."""

    async def agent_get_users(self, ref: VMRef) -> list[GuestUser]: ...
    async def agent_get_osinfo(self, ref: VMRef) -> OSInfo: ...
    async def agent_get_network(self, ref: VMRef) -> list[NetworkInterface]: ...

    async def agent_exec(
        self, ref: VMRef, command: list[str], input_data: str | None = None
    ) -> int:
        """Execute `command` in the guest. Returns a PID (or provider
        equivalent) to be passed to agent_exec_status."""

    async def agent_exec_status(self, ref: VMRef, pid: int) -> ExecStatus: ...

    # ── Console tickets ───────────────────────────────────────

    async def get_console_ticket(
        self, ref: VMRef, kind: ConsoleKind
    ) -> ConsoleTicket:
        """Issue a console ticket. Must raise NotImplementedError if
        `kind` is not in capabilities().console_kinds. Returns the
        appropriate ConsoleTicket subtype matching `kind`."""

    # ── Task tracking ─────────────────────────────────────────

    async def get_task_status(self, handle: TaskHandle) -> TaskStatus:
        """Non-blocking current status of an async task."""

    async def wait_for_task(
        self, handle: TaskHandle,
        timeout_seconds: int = 600, poll_interval: float = 1.0,
    ) -> TaskStatus:
        """Poll until the task completes or `timeout_seconds` elapses.

        Raises ProviderTimeoutError on timeout.
        Raises ProviderTaskError if the task completes unsuccessfully.
        Returns the final TaskStatus on success.

        Default timeout of 600s is tuned for clone operations. Callers
        MUST override for faster ops (start/stop: 30s) or longer ones
        (full clones on slow storage: 1800s+)."""
```

## Exceptions

```python
class ProviderError(Exception):
    """Base class for all provider-layer exceptions."""
    def __init__(self, message: str, provider_type: str, detail: dict | None = None):
        ...

class ProviderAuthError(ProviderError):
    """Authentication or authorization failure."""

class ProviderNotFoundError(ProviderError):
    """Target resource (VM, node, template) does not exist."""

class ProviderTimeoutError(ProviderError):
    """Request or task did not complete in the allowed time."""

class ProviderTaskError(ProviderError):
    """Async task completed with failure. The `detail` dict includes
    the provider's raw error response."""

class ProviderLockError(ProviderError):
    """Target resource is locked by another operation. Typically
    transient; providers SHOULD have exhausted their internal retries
    before raising this."""

class ProviderCapabilityError(ProviderError):
    """Requested operation is not supported by this provider."""
```

Implementations may define provider-local exception subclasses (e.g. `ProxmoxClientError` extending `ProviderError`) for internal use, but the public surface — what the broker catches — is always one of the classes above.

## Provider Registration

Providers self-register at broker startup. The registry is a simple dict keyed by `provider_type`.

```python
# broker/app/providers/__init__.py

_registry: dict[str, type[HypervisorProvider]] = {}

def register_provider(cls: type[HypervisorProvider]) -> type[HypervisorProvider]:
    """Decorator or explicit call. Registers by `cls.provider_type`."""

def get_provider_class(provider_type: str) -> type[HypervisorProvider]:
    """Look up by type string. Raises ValueError if unknown."""

def list_provider_types() -> list[str]:
    """Available providers, for admin UI."""
```

The `clusters` table stores `provider_type` per registered cluster; at broker startup the pool manager instantiates one provider per cluster using that type and the cluster's credentials.

## Consumer Contract

The broker, provisioner, and workers depend ONLY on `HypervisorProvider` and the shared types in this document. They MUST NOT import from `broker/app/providers/proxmox/` or any other concrete provider module. This is enforced by convention and (if useful later) by import linting.

Concretely:
- `services/broker.py`, `services/provisioner.py`, `workers/*.py` — import from `providers.base` only.
- `api/*.py` — may construct `CloneRequest` etc. but never touches provider internals.
- `models/*.py` — persist `provider_type` and `provider_config` on clusters/pools but never import from providers.

## When to Add a New Provider

Adding a provider is a deliberate step, not casual. Guidelines:

1. **Capability fit.** The provider must support linked (or equivalently efficient) cloning, snapshots, guest agent or equivalent, and a noVNC-compatible console. Providers lacking any of these require either a bridging component or explicit v1+ work to unlock.

2. **Parity test suite.** Every new provider MUST pass the provider conformance test suite (`broker/tests/providers/conformance/`) against a live test cluster. Tests assert behavior of the interface, not implementation details.

3. **Provider doc.** A new `providers/<name>.md` documenting the per-method mapping, capabilities, and any provider-specific quirks.

4. **Service account model.** Documented least-privilege service account with the specific role/privilege list for that provider.

The conformance test suite is not in scope for Milestone 1 but is added in Milestone 4 alongside the first meaningful workload against the abstraction.
