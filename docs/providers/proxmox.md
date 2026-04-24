# OpenVDI Proxmox Provider

## Overview

The Proxmox provider is the first concrete implementation of the `HypervisorProvider` interface defined in `providers.md`. It wraps the Proxmox VE REST API (480 endpoints) for VDI-relevant operations, using httpx async with connection pooling.

One instance of `ProxmoxProvider` exists per registered Proxmox cluster. It implements every method of `HypervisorProvider` and is registered under `provider_type = "proxmox"`.

This document is the authoritative reference for the `ProxmoxProvider` class (`broker/app/providers/proxmox/provider.py`). When implementing or modifying the provider, consult the `pve-spec-query` MCP server for authoritative API schemas — specific spec lookups are referenced inline with each method group below. Do not guess at API parameters; look them up.

## Relationship to the Provider Interface

`ProxmoxProvider` is the concrete class. It MAY have additional helper methods (for the test script, for admin introspection, etc.) but all broker-callable surface comes through the `HypervisorProvider` Protocol.

Internally the provider is split into:

- `broker/app/providers/proxmox/provider.py` — the `ProxmoxProvider` class, implementing `HypervisorProvider`.
- `broker/app/providers/proxmox/client.py` — the low-level `_ProxmoxClient` HTTP helper (httpx session, auth header, request/response shaping). Internal to the provider.
- `broker/app/providers/proxmox/params.py` — explicit snake_case ↔ kebab-case translation.
- `broker/app/providers/proxmox/exceptions.py` — Proxmox-local exception subclasses that extend the base `ProviderError` hierarchy.
- `broker/app/providers/proxmox/types.py` — helpers for packing/unpacking `VMRef.data` (a `(node, vmid)` tuple) and `TaskHandle.data` (the Proxmox UPID string plus the node it belongs to).

The separation matters: the broker sees only `ProxmoxProvider` as an opaque `HypervisorProvider`. Everything in `client.py`, `params.py`, etc. is implementation detail.

## Capabilities

```python
ProviderCapabilities(
    provider_type="proxmox",
    linked_clones=True,
    full_clones=True,
    snapshots=True,
    guest_agent=True,
    live_migration=True,          # not used in v0 but reported accurately
    console_kinds=frozenset({
        ConsoleKind.NOVNC,
        ConsoleKind.SPICE,
    }),
    supports_pool_tags=True,
    supports_resource_pools=True,
)
```

## VMRef and TaskHandle Encoding

```python
# VMRef for a Proxmox VM:
VMRef(provider_type="proxmox", data={"node": "pve1", "vmid": 5003})

# TaskHandle for a Proxmox async task:
TaskHandle(
    provider_type="proxmox",
    data={"node": "pve1", "upid": "UPID:pve1:00001234:..."},
)
```

Both contain the node because Proxmox task status is looked up on a specific node (tasks are node-local even in a cluster).

## Authentication

The Proxmox provider uses an API token, not ticket-based auth. API tokens are stateless and don't require CSRF tokens for write operations.

```
Authorization: PVEAPIToken=user@realm!tokenid=uuid-secret
```

### Service Account Setup

Create a dedicated `openvdi@pve` user with an API token. Required privileges, applied via a role bound to `/`:

| Privilege | Why it's needed |
|-----------|-----------------|
| `VM.Clone` | Clone templates into pool VMs |
| `VM.Allocate` | Create new VMIDs |
| `VM.Config.*` | Configure cloned VMs (cpu, memory, disk, net, options) |
| `VM.PowerMgmt` | Start, stop, shutdown, reboot desktops |
| `VM.Snapshot` | Create/rollback/delete the `openvdi-base` snapshot |
| `VM.Console` | Generate VNC/SPICE tickets |
| `VM.Monitor` | Guest agent access (ping, get-users, exec, etc.) |
| `VM.Audit` | Read VM state |
| `Datastore.AllocateSpace` | Space for cloned disks |
| `Datastore.Audit` | Read storage info |
| `Sys.Audit` | Read task status for tasks not owned by the token user |
| `SDN.Use` | Attach clones to SDN-defined networks (required in PVE 8+ when SDN is configured) |

This is least-privilege relative to what a sysadmin token would have — notably it excludes `Sys.Modify`, `Datastore.Allocate`, and cluster-level config rights.

## API Parameter Name Translation

**Proxmox uses kebab-case for some parameter names that don't fit Python's snake_case convention.** The Proxmox provider accepts Python-idiomatic snake_case arguments internally and must translate them when serializing to the API. Getting this wrong is silent: Proxmox ignores unknown parameters rather than returning an error.

Known translations (non-exhaustive — always verify against spec):

| Python | Proxmox API |
|--------|-------------|
| `generate_password` | `generate-password` |
| `input_data` | `input-data` |
| `target_node` | `target` |
| `force_stop` | `forceStop` |
| `keep_active` | `keepActive` |
| `skip_lock` | `skiplock` |

Implement as an explicit mapping in `params.py` (not a generic `_`→`-` converter — the latter would break `pve_vmid`, `exit_status`, etc.).

## Method-by-Method Mapping

The sections below document how each `HypervisorProvider` method is realized against the Proxmox API. Inline `pve-spec-query` references are the authoritative lookup; always use them when touching a method.

### Cluster & Placement

```
# pve-spec-query refs:
#   pve_get_endpoint_detail("/nodes", "GET")
#   pve_get_endpoint_detail("/nodes/{node}/status", "GET")
#   pve_get_endpoint_detail("/nodes/{node}/storage", "GET")
```

| Interface method | Proxmox API |
|------------------|-------------|
| `list_nodes()` | `GET /nodes` |
| `get_node_status(node)` | `GET /nodes/{node}/status` |
| `list_storage(node)` | `GET /nodes/{node}/storage` |

`StorageInfo.content_types` is populated from the Proxmox `content` field (a comma-separated string like `images,rootdir`), normalized to a frozenset.

### VM Lifecycle

```
# pve-spec-query refs:
#   pve_get_endpoint_detail("/nodes/{node}/qemu/{vmid}/clone", "POST")
#   pve_get_endpoint_detail("/nodes/{node}/qemu/{vmid}/status/start", "POST")
#   pve_get_endpoint_detail("/nodes/{node}/qemu/{vmid}/status/stop", "POST")
#   pve_get_endpoint_detail("/nodes/{node}/qemu/{vmid}/status/shutdown", "POST")
#   pve_get_endpoint_detail("/nodes/{node}/qemu/{vmid}/status/reboot", "POST")
#   pve_get_endpoint_detail("/nodes/{node}/qemu/{vmid}", "DELETE")
#   pve_get_endpoint_detail("/nodes/{node}/qemu/{vmid}/status/current", "GET")
#   pve_get_endpoint_detail("/nodes/{node}/qemu", "GET")
#   pve_get_endpoint_detail("/nodes/{node}/qemu/{vmid}/config", "POST")
```

**`clone_vm(req: CloneRequest) -> TaskHandle`**

Calls `POST /nodes/{node}/qemu/{vmid}/clone`.

Clone semantics (per `architecture.md` → *Cloning Model*):
- `req.source_ref` MUST reference a Proxmox template. The provider validates this via `get_vm_status` before issuing the clone if `OPENVDI_STRICT_TEMPLATE_CHECK` is set; otherwise the Proxmox API will reject it when the source isn't a template and the provider surfaces the error.
- `full` is NOT passed. Proxmox defaults to linked clones for template sources.
- `snapname` is NOT passed. Linked clones from a template reference the template's base disk directly.
- Cloning from a non-template VM is explicitly unsupported at the provider level — the provider raises `ProviderCapabilityError`.

Parameter mapping:
- `req.source_ref.data["node"]` → URL node
- `req.source_ref.data["vmid"]` → URL vmid
- `req.provider_opts["newid"]` → body `newid` (the broker's VMID allocator supplies this)
- `req.new_name` → body `name`
- `req.target_storage` → body `storage`
- `req.target_pool` → body `pool`
- `req.target_node` → body `target` (translated from snake_case)
- `req.description` → body `description`

Returns a `TaskHandle` wrapping the UPID.

**`start_vm(ref) -> TaskHandle`** — `POST /nodes/{node}/qemu/{vmid}/status/start`.

**`stop_vm(ref) -> TaskHandle`** — `POST /nodes/{node}/qemu/{vmid}/status/stop`. Immediate forceful stop (equivalent to pulling the plug). No guest coordination.

**`shutdown_vm(ref, timeout_seconds, force) -> TaskHandle`** — `POST /nodes/{node}/qemu/{vmid}/status/shutdown`. Graceful ACPI shutdown. Requires guest OS to respond to ACPI power button; requires guest agent for full-speed clean shutdown on Windows.
- `timeout_seconds` → body `timeout`
- `force` → body `forceStop` (translated from `force_stop`)

**`reboot_vm(ref) -> TaskHandle`** — `POST /nodes/{node}/qemu/{vmid}/status/reboot`.

**`destroy_vm(ref, purge=True) -> TaskHandle`** — `DELETE /nodes/{node}/qemu/{vmid}`. VM must be stopped first. `purge=True` also removes from replication/backup jobs.

**Internal retry policy**: destroy can fail with HTTP 500 and a message mentioning lock contention (e.g. `trying to acquire lock '...' failed`) when the VM's storage is mid-operation. The provider retries up to 3 times with exponential backoff starting at 5 seconds. If retries are exhausted, raises `ProviderLockError`. 4xx errors are NOT retried.

**`get_vm_status(ref) -> VMStatus`** — `GET /nodes/{node}/qemu/{vmid}/status/current`.

Field mapping from Proxmox response:
- Proxmox `status` → `VMStatus.power_state` (values match: `running`/`stopped`/`paused`)
- Proxmox `name` → `VMStatus.name`
- Proxmox `cpus` → `VMStatus.cpu_cores`
- Proxmox `maxmem` → `VMStatus.memory_bytes`
- Proxmox `maxdisk` → `VMStatus.disk_bytes`
- Proxmox `uptime` → `VMStatus.uptime_seconds`
- Proxmox `template` (if present, 0/1) → `VMStatus.is_template`
- Proxmox `agent` → `VMStatus.guest_agent_configured` (configured in VM settings; NOT a liveness check — use `agent_ping` for that)
- Proxmox `lock` → `VMStatus.lock`
- Proxmox `tags` (semicolon-separated string) → `VMStatus.tags` (frozenset of strings)
- Full response → `VMStatus.raw`

**`list_vms(node=None) -> list[VMStatus]`** — `GET /nodes/{node}/qemu?full=1`. When `node` is None, the provider iterates nodes and aggregates.

**`configure_vm(ref, config) -> TaskHandle`** — `POST /nodes/{node}/qemu/{vmid}/config` for async config changes on stopped VMs (the default path for post-clone customization). The PUT variant (synchronous, returns None) is used internally by the provider when it detects a hot-applicable change on a running VM.

### Snapshots

```
# pve-spec-query refs:
#   pve_get_endpoint_detail("/nodes/{node}/qemu/{vmid}/snapshot", "POST")
#   pve_get_endpoint_detail("/nodes/{node}/qemu/{vmid}/snapshot", "GET")
#   pve_get_endpoint_detail("/nodes/{node}/qemu/{vmid}/snapshot/{snapname}", "DELETE")
#   pve_get_endpoint_detail(
#       "/nodes/{node}/qemu/{vmid}/snapshot/{snapname}/rollback", "POST")
```

**Snapshots operate on CLONED DESKTOP VMs, never on templates.** Templates have no OpenVDI-managed snapshots. The `openvdi-base` snapshot is per-desktop, created after initial provisioning for use in non-persistent refresh-on-logoff rollbacks. See `session-tracking.md` → *Snapshot Model*.

> **Implementation status.** The Protocol methods `create_snapshot`, `rollback_snapshot`, `list_snapshots`, `delete_snapshot`, and `configure_vm` are introduced on the `ProxmoxProvider` in Milestone 2. No signature changes from the Protocol definition in `providers.md` — this is an implementation delta. They are exercised by the M2 provisioner when taking the `openvdi-base` snapshot at provisioning time, and by pool-level VM overrides (`cpu_cores`, `memory_mb`) applied via `configure_vm` between clone completion and first start.

| Interface method | Proxmox API |
|------------------|-------------|
| `create_snapshot(ref, name, description, include_ram)` | `POST /nodes/{node}/qemu/{vmid}/snapshot` with `snapname=name`, `vmstate=include_ram` |
| `rollback_snapshot(ref, name)` | `POST /nodes/{node}/qemu/{vmid}/snapshot/{snapname}/rollback` |
| `list_snapshots(ref)` | `GET /nodes/{node}/qemu/{vmid}/snapshot` (including synthetic `current` entry) |
| `delete_snapshot(ref, name)` | `DELETE /nodes/{node}/qemu/{vmid}/snapshot/{snapname}` |

`SnapshotInfo.created_at` is Proxmox's `snaptime` (unix epoch) when present; `None` for the synthetic `current` entry.

**Error surface for missing snapshots.** `rollback_snapshot` and `delete_snapshot` called against a non-existent `snapname` do NOT raise `ProviderNotFoundError`. Proxmox accepts the HTTP request and returns HTTP 200 with a UPID; the async task subsequently fails with a message like `"snapshot 'x' does not exist"`. The surfaced exception is therefore `ProviderTaskError` via `wait_for_task`, not `ProviderNotFoundError` via the initial request. This is a general Proxmox pattern for operations on named sub-resources of a VM — the VM URL is validated synchronously but the sub-resource identity is validated at task-execution time. The provider does NOT pattern-match on the task error string to reclassify the failure — Proxmox error message wording is not an API stability guarantee, and papering over it would silently break on a Proxmox minor-version bump. Callers that need to distinguish missing-snapshot from other task failures must substring-match locally.

### Guest Agent

```
# pve-spec-query refs:
#   pve_get_endpoint_detail("/nodes/{node}/qemu/{vmid}/agent/ping", "POST")
#   pve_get_endpoint_detail("/nodes/{node}/qemu/{vmid}/agent/get-users", "GET")
#   pve_get_endpoint_detail("/nodes/{node}/qemu/{vmid}/agent/get-osinfo", "GET")
#   pve_get_endpoint_detail(
#       "/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces", "GET")
#   pve_get_endpoint_detail("/nodes/{node}/qemu/{vmid}/agent/exec", "POST")
#   pve_get_endpoint_detail("/nodes/{node}/qemu/{vmid}/agent/exec-status", "GET")
```

**`agent_ping(ref) -> bool`** — `POST /nodes/{node}/qemu/{vmid}/agent/ping`. Proxmox returns HTTP 500 when the guest agent is unreachable; the provider catches this specific shape and returns `False`. Other errors (404, 500 from actual server problems) raise normally.

**`agent_get_users(ref) -> list[GuestUser]`** — `GET /nodes/{node}/qemu/{vmid}/agent/get-users`.

Field mapping per user:
- Proxmox `user` → `GuestUser.username`
- Proxmox `login-time` → `GuestUser.login_time`
- Proxmox `domain` (Windows only) → `GuestUser.domain`

**`agent_get_osinfo(ref) -> OSInfo`** — `GET /nodes/{node}/qemu/{vmid}/agent/get-osinfo`. Maps to `OSInfo` fields; the Proxmox response has a nested `result` object containing the actual data.

**`agent_get_network(ref) -> list[NetworkInterface]`** — `GET /nodes/{node}/qemu/{vmid}/agent/network-get-interfaces`. Flattens the nested Proxmox structure into the shared `NetworkInterface` shape.

**`agent_exec(ref, command, input_data) -> int`** — `POST /nodes/{node}/qemu/{vmid}/agent/exec`. The Proxmox API expects `command` in `string-alist` format; the provider takes a Python `list[str]` and serializes appropriately. `input_data` → `input-data` (translated). Returns PID.

**`agent_exec_status(ref, pid) -> ExecStatus`** — `GET /nodes/{node}/qemu/{vmid}/agent/exec-status`. Maps Proxmox `exited`, `exitcode`, `out-data`, `err-data` to the shared `ExecStatus` shape.

### Console Tickets

```
# pve-spec-query refs:
#   pve_get_endpoint_detail("/nodes/{node}/qemu/{vmid}/vncproxy", "POST")
#   pve_get_endpoint_detail("/nodes/{node}/qemu/{vmid}/spiceproxy", "POST")
```

**`get_console_ticket(ref, kind)` dispatches based on `kind`:**

For `ConsoleKind.NOVNC`:
- `POST /nodes/{node}/qemu/{vmid}/vncproxy` with `websocket=1` and `generate-password=1` (translated from `generate_password`).
- Proxmox response includes `port`, `ticket`, `password`, `cert`, `upid`, `user`.
- The provider constructs the full browser websocket URL:
  `wss://{node}:{port}/api2/json/nodes/{node}/qemu/{vmid}/vncwebsocket?port={port}&vncticket={urlencoded ticket}`
- Returns `NoVNCTicket(websocket_url=..., password=..., cert_pem=...)`.
- **Note on URL form**: this is the PVE 9.x form. If it's ever observed to differ from older clusters, the `vncwebsocket` endpoint path is what varies; the query parameters are stable.

For `ConsoleKind.SPICE`:
- `POST /nodes/{node}/qemu/{vmid}/spiceproxy`.
- Returns `SpiceTicket` populated from the Proxmox response.

For other `ConsoleKind` values: `ProviderCapabilityError`.

### Task Tracking

```
# pve-spec-query refs:
#   pve_get_endpoint_detail("/nodes/{node}/tasks/{upid}/status", "GET")
```

**SPEC QUIRK — READ THIS**: the Proxmox OpenAPI spec documents only `pid` and `status` as task status response fields, but the actual API also returns `exitstatus` containing `"OK"` or an error string. This field is required for task success detection. DO NOT rely on the spec field list alone.

**`get_task_status(handle) -> TaskStatus`** — `GET /nodes/{node}/tasks/{upid}/status`.

Mapping:
- Proxmox `status == "running"` → `TaskStatus(state="running", success=None, error_message=None, raw=...)`
- Proxmox `status == "stopped"` AND `exitstatus == "OK"` → `TaskStatus(state="stopped", success=True, error_message=None, raw=...)`
- Proxmox `status == "stopped"` AND `exitstatus != "OK"` → `TaskStatus(state="stopped", success=False, error_message=exitstatus, raw=...)`

`exitstatus` is ONLY present when `status == "stopped"`. When status is still `"running"`, the provider MUST NOT read `exitstatus`.

**`wait_for_task(handle, timeout_seconds=600, poll_interval=1.0) -> TaskStatus`**.

Default timeout of 600 seconds is tuned for clone tasks on thin-provisioned LVM. Callers should override:
- Fast operations (start, stop): 30s
- Full clones on slow storage: 1800s+

Raises `ProviderTimeoutError` if task doesn't complete in time. Raises `ProviderTaskError` if task completes unsuccessfully (`success=False`), with the Proxmox `exitstatus` string as the error message.

## Error Handling

Provider-local exception subclasses extend the base hierarchy from `providers.base`:

```python
# broker/app/providers/proxmox/exceptions.py

from providers.base import (
    ProviderError, ProviderAuthError, ProviderNotFoundError,
    ProviderTimeoutError, ProviderTaskError, ProviderLockError,
    ProviderCapabilityError,
)

class ProxmoxError(ProviderError):
    """Base for Proxmox-specific errors. Used when the internal client
    needs to signal an error that doesn't cleanly map to one of the
    broader categories above. Public surface should avoid this when
    possible."""
    def __init__(self, status_code: int, message: str, endpoint: str):
        super().__init__(message, provider_type="proxmox",
                         detail={"status_code": status_code, "endpoint": endpoint})
```

HTTP-status-to-exception mapping within the provider:

| HTTP / condition | Raised as |
|------------------|-----------|
| 401 / 403 | `ProviderAuthError` |
| 404 | `ProviderNotFoundError` |
| 500 + body contains lock-related message | `ProviderLockError` (after internal retries exhausted) |
| Other 5xx | Retried internally; `ProxmoxError` if retries exhausted |
| Network error / timeout | Retried internally; `ProviderTimeoutError` if retries exhausted |
| 4xx (non-auth) | `ProxmoxError` — client bug or validation failure |

### Retry Policy (internal to provider)

| Error class | Retry? | Policy |
|-------------|--------|--------|
| `ProviderAuthError` (401/403) | No | Auth problems don't self-heal |
| `ProviderNotFoundError` (404) | No | Target doesn't exist |
| `ProviderLockError` (500 + lock msg) | Yes | Exponential backoff, 3 attempts |
| Other 5xx | Yes | Exponential backoff, 3 attempts |
| Network error / timeout | Yes | Exponential backoff, 3 attempts |
| 4xx (non-auth) | No | Client bug or validation failure |

## LVM Lock Cleanup (Operational Note)

On PVE nodes using LVM-thin storage (default for local-lvm), an unclean shutdown can leave orphaned lock files in `/run/lock/lvm/` with no owning process. The next clone or VM operation touching that volume group fails with `can't lock file /run/lock/lvm/P_global`.

This is not something the Proxmox provider can fix from the API side — `/run/lock/lvm/` is on the hypervisor host. The project ships a `systemd` unit (`lvm-lock-cleanup.service`) intended to be installed on each PVE node:

```ini
[Unit]
Description=Clean orphaned LVM lock files at boot
Before=lvm2-monitor.service
After=local-fs.target

[Service]
Type=oneshot
ExecStart=/bin/sh -c '/usr/bin/rm -f /run/lock/lvm/P_* /run/lock/lvm/V_*'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

The provider does not assume this service is present. Clone failures with lock errors surface as `ProviderLockError` to the broker, which logs them to audit and alerts operators; the fix is manual (`rm -f /run/lock/lvm/P_* /run/lock/lvm/V_*` followed by `systemctl restart lvm2-monitor`) if the boot-time service isn't installed.

## Connection Management

- One `httpx.AsyncClient` per provider instance (i.e. per cluster).
- SSL verification configurable via the cluster's `verify_ssl` setting.
- Connection pool: max 20 connections, max 10 keepalive.
- Request timeout: 30 seconds for normal operations, 300 seconds for synchronous long operations. Async operations return a TaskHandle and are polled via `wait_for_task` with its own timeout.
- Retry policy per the table above.
- The provider's `close()` method disposes the AsyncClient cleanly.

## Proxmox API Reference

The `pve-spec-query` MCP server (6 tools, 480 endpoints) is the authoritative reference for all Proxmox API details. Specific method groups above include inline `pve-spec-query` tool invocations as comments — use these when implementing or modifying the corresponding provider methods.

Tool cheat sheet:
- `pve_search_endpoints(query, limit=10)` — find endpoints by keyword. **Use `limit=10` or higher**; the default of 5 hides relevant results.
- `pve_get_endpoint_detail(path, method)` — full parameter schema for a specific path + HTTP method.
- `pve_search_parameters(query)` — find endpoints that accept a given parameter.
- `pve_get_subdomain_doc(subdomain)` — full docs for a /nodes/{node} sub-domain (warning: `qemu` is 200KB+).
- `pve_list_api_domains()` — top-level API surface.
- `pve_get_domain_overview(domain)` — overview of a top-level domain.

Use concise keyword terms with `pve_search_endpoints` (e.g. `"clone"`, `"upid"`, `"snapshot"`) rather than natural-language phrases.
