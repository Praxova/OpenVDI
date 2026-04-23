"""Abstract hypervisor provider interface and shared types.

Every concrete hypervisor provider implements `HypervisorProvider` and
uses only the shared types defined here. The broker, workers, and API
layer depend only on this module (and `providers.exceptions`) — they
never import from a concrete provider package.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar, Literal, Protocol


# ── Identity & references ─────────────────────────────────────

@dataclass(frozen=True)
class VMRef:
    """Opaque VM reference. Provider-specific contents.

    Providers set `provider_type` so the broker can sanity-check that a
    VMRef came from the expected provider when passing it back. The
    `data` field is provider-defined (tuple, dict, str).
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
    linked_clones: bool
    full_clones: bool
    snapshots: bool
    guest_agent: bool
    live_migration: bool
    console_kinds: frozenset[ConsoleKind]
    supports_pool_tags: bool
    supports_resource_pools: bool


# ── Cluster & placement ───────────────────────────────────────

@dataclass(frozen=True)
class NodeInfo:
    node: str
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
    storage_type: str
    shared: bool
    total_bytes: int
    used_bytes: int
    content_types: frozenset[str]


# ── VMs ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class CloneRequest:
    """Abstract clone request.

    Clone mode is always 'linked if source is a template, full otherwise'
    for Proxmox; other providers map this to their closest equivalent.
    Pool configuration may restrict acceptable providers to those whose
    native clone semantics match VDI requirements (fast, space-efficient).
    """
    source_ref: VMRef
    new_name: str
    target_node: str | None = None
    target_storage: str | None = None
    target_pool: str | None = None
    description: str | None = None
    # Provider-specific overrides (e.g. Proxmox 'newid' for VMID allocation).
    # The broker's VMID allocator chooses these and passes them through
    # opaquely.
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
    guest_agent_configured: bool
    lock: str | None
    tags: frozenset[str]
    raw: dict[str, Any]


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
    created_at: int | None
    parent: str | None
    includes_ram: bool


# ── Guest agent ───────────────────────────────────────────────

@dataclass(frozen=True)
class GuestUser:
    username: str
    login_time: int | None
    domain: str | None


@dataclass(frozen=True)
class OSInfo:
    name: str
    version: str
    kernel_release: str | None
    architecture: str | None


@dataclass(frozen=True)
class NetworkInterface:
    name: str
    mac_address: str | None
    ip_addresses: list[str]
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
    websocket_url: str = ""
    password: str = ""
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
    gateway_host: str | None = None
    gateway_token: str | None = None


ConsoleTicket = NoVNCTicket | WebMKSTicket | SpiceTicket | RDPTicket


# ── Task tracking ─────────────────────────────────────────────

TaskState = Literal["running", "stopped"]


@dataclass(frozen=True)
class TaskStatus:
    state: TaskState
    success: bool | None
    error_message: str | None
    raw: dict[str, Any]


# ── Provider Protocol ─────────────────────────────────────────

class HypervisorProvider(Protocol):
    """Abstract hypervisor provider interface.

    One instance per registered cluster. Implementations manage their own
    HTTP/RPC client(s), connection pools, authentication, and retry logic.
    The broker treats instances as stateful objects with the lifetime of
    the cluster registration.
    """

    provider_type: ClassVar[str]

    # ── Capabilities & lifecycle ──────────────────────────────

    async def capabilities(self) -> ProviderCapabilities:
        """Static capability declaration. May be called repeatedly;
        implementations should memoize."""
        ...

    async def ping(self) -> bool:
        """Quick liveness check against the provider's API."""
        ...

    async def close(self) -> None:
        """Release connections and resources. Called at broker shutdown
        or cluster deregistration."""
        ...

    # ── Cluster & placement ───────────────────────────────────

    async def list_nodes(self) -> list[NodeInfo]: ...
    async def get_node_status(self, node: str) -> NodeStatus: ...
    async def list_storage(self, node: str) -> list[StorageInfo]: ...

    # ── VM lifecycle ──────────────────────────────────────────

    async def clone_vm(self, req: CloneRequest) -> TaskHandle:
        """Clone a template into a new desktop VM.

        The source VM (req.source_ref) MUST be a template. Providers that
        cannot guarantee efficient cloning from non-template VMs should
        raise ProviderError rather than fall back to full copy.
        """
        ...

    async def start_vm(self, ref: VMRef) -> TaskHandle: ...

    async def stop_vm(self, ref: VMRef) -> TaskHandle:
        """Forceful stop (pull the plug). No guest coordination."""
        ...

    async def shutdown_vm(
        self, ref: VMRef, timeout_seconds: int = 120, force: bool = False
    ) -> TaskHandle:
        """Graceful guest-coordinated shutdown. If `force` is True and the
        guest doesn't respond within `timeout_seconds`, the provider
        escalates to a hard stop."""
        ...

    async def reboot_vm(self, ref: VMRef) -> TaskHandle: ...

    async def destroy_vm(self, ref: VMRef, purge: bool = True) -> TaskHandle:
        """Delete the VM and its owned storage. VM must be stopped.

        Providers are responsible for retrying transient lock/contention
        failures internally according to their own semantics, and for
        raising ProviderLockError if retries are exhausted.
        """
        ...

    async def get_vm_status(self, ref: VMRef) -> VMStatus: ...
    async def list_vms(self, node: str | None = None) -> list[VMStatus]: ...

    async def configure_vm(
        self, ref: VMRef, config: VMConfig
    ) -> TaskHandle | None:
        """Apply config changes. Returns a TaskHandle for async changes,
        None for changes applied synchronously by the provider."""
        ...

    # ── Snapshots ─────────────────────────────────────────────
    # Operate on desktop VMs, never on templates. Consumers must check
    # capabilities().snapshots before calling; providers without snapshot
    # support raise NotImplementedError.

    async def create_snapshot(
        self,
        ref: VMRef,
        name: str,
        description: str | None = None,
        include_ram: bool = False,
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
        ...

    async def agent_get_users(self, ref: VMRef) -> list[GuestUser]: ...
    async def agent_get_osinfo(self, ref: VMRef) -> OSInfo: ...
    async def agent_get_network(self, ref: VMRef) -> list[NetworkInterface]: ...

    async def agent_exec(
        self,
        ref: VMRef,
        command: list[str],
        input_data: str | None = None,
    ) -> int:
        """Execute `command` in the guest. Returns a PID (or provider
        equivalent) to be passed to agent_exec_status."""
        ...

    async def agent_exec_status(self, ref: VMRef, pid: int) -> ExecStatus: ...

    # ── Console tickets ───────────────────────────────────────

    async def get_console_ticket(
        self, ref: VMRef, kind: ConsoleKind
    ) -> ConsoleTicket:
        """Issue a console ticket. Must raise NotImplementedError if
        `kind` is not in capabilities().console_kinds. Returns the
        appropriate ConsoleTicket subtype matching `kind`."""
        ...

    # ── Task tracking ─────────────────────────────────────────

    async def get_task_status(self, handle: TaskHandle) -> TaskStatus:
        """Non-blocking current status of an async task."""
        ...

    async def wait_for_task(
        self,
        handle: TaskHandle,
        timeout_seconds: int = 600,
        poll_interval: float = 1.0,
    ) -> TaskStatus:
        """Poll until the task completes or `timeout_seconds` elapses.

        Raises ProviderTimeoutError on timeout.
        Raises ProviderTaskError if the task completes unsuccessfully.
        Returns the final TaskStatus on success.

        Default timeout of 600s is tuned for clone operations. Callers
        MUST override for faster ops (start/stop: 30s) or longer ones
        (full clones on slow storage: 1800s+).
        """
        ...
