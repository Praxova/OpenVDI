# OpenVDI Architecture

## Overview

OpenVDI is an open-source Virtual Desktop Infrastructure platform. It provides connection brokering, desktop pool management, template-based provisioning, and session tracking — the core capabilities needed to deliver managed virtual desktops at scale.

OpenVDI is **hypervisor-agnostic**. The first supported provider is Proxmox VE, but the broker, workers, and API layer never call a specific hypervisor's API directly. They talk to an abstract `HypervisorProvider` interface (see `providers.md`). This is an early architectural decision intended to avoid the Omnissa/Horizon trap — where a broker is so tightly bound to vCenter that a hypervisor migration requires a rewrite.

The project is a product of Praxova, hosted at horizonspecialists.com, targeting organizations migrating off Broadcom/VMware Horizon due to 5-6x licensing cost increases.

## Design Philosophy

1. **OpenVDI is a management layer on top of a hypervisor, not a modification to it.** The hypervisor remains the source of truth for VM state. OpenVDI owns pool definitions, user entitlements, session tracking, and connection brokering. If OpenVDI goes down, VMs keep running — you just can't broker new connections.

2. **Hypervisor-agnostic by construction.** The broker, provisioner, services, and workers depend only on the `HypervisorProvider` Protocol defined in `providers.md`. Concrete providers (Proxmox today; vSphere, Hyper-V, Nutanix, XCP-ng, OpenStack in the future) are loaded by provider type at runtime. Proxmox is the first provider, not the only one. See `providers.md` for the interface and `providers/proxmox.md` for the Proxmox implementation.
3. **Users never touch the hypervisor API directly.** OpenVDI authenticates users against AD/LDAP and maintains a service account to each hypervisor with scoped privileges on VDI-managed VMs. This is the same model VMware Horizon uses — users auth to the Connection Server, not to vCenter.

4. **Provider-native tagging provides recovery and visibility.** VDI-managed VMs are tagged with metadata (e.g. `openvdi-managed`, `openvdi-pool-engineering`, `openvdi-type-nonpersistent`, `openvdi-user-jsmith`). Providers that support tags expose them through `VMStatus.tags`; this gives visibility in the native hypervisor UI and provides a recovery path if the OpenVDI database is lost. Tag tokens on Proxmox are restricted to `[a-z0-9_-]` — colons and equals signs are rejected — so tag values that embed a pool name or username go through a slug transform (lowercase; non-`[a-z0-9_-]` replaced with `-`; runs collapsed; leading/trailing `-` stripped). The authoritative metadata store is the OpenVDI database; the VM description field carries a human-readable `key=value` summary for DR when a username slugifies lossily. See `database-schema.md` → *VM Tagging Convention*.

5. **Protocol-agnostic connection brokering.** The broker returns a typed `ConsoleTicket` — it doesn't care whether the client connects via noVNC, SPICE, KasmVNC, WebMKS, or RDP. The portal knows how to render each kind. v0 supports noVNC only; the data shape is ready for more.
## Cloning Model

OpenVDI uses a consistent cloning model across all pool types. This is a deliberate architectural decision — do not deviate without updating this document first.

**All desktops are linked clones of a hypervisor-native VM template, cloned from the template's current state (no source snapshot).**

The concrete shape varies per provider (Proxmox linked clones, vSphere linked or instant clones, Hyper-V differencing disks, etc.), but the contract is the same from the broker's perspective: clone a template, get a fast-provisioned desktop, and manage its lifecycle through the provider interface.

Concretely for the Proxmox provider (see `providers/proxmox.md` for the full mapping):

- Source VMs are always Proxmox templates (`qm template <vmid>` has been run, or the VM was created `--template 1`). Cloning from a non-template VM is out of scope.
- The `POST /nodes/{node}/qemu/{vmid}/clone` call is made with `full=0` (or omitted, same effect). For template sources, Proxmox creates a linked clone by default.
- The `snapname` parameter is **never passed** when cloning. Linked clones from a template reference the template's base disk directly; passing `snapname` tells Proxmox to clone from a named snapshot on the template, which is a different (and unneeded) flow.
- Per-desktop snapshots (named `openvdi-base`) are created on individual cloned desktops, not on the template. They are used by the non-persistent pool refresh cycle (`rollback_snapshot`), never by the clone operation itself.

Linked clones are fast (seconds, not minutes), space-efficient (copy-on-write), and sufficient for VDI workloads. The tradeoff — that the template VM must remain available as the backing source — is acceptable because OpenVDI treats templates as long-lived objects registered explicitly.

Persistent and non-persistent pools use the same clone mechanism. The difference is what happens after the clone: persistent desktops are assigned to a named user and preserved across sessions; non-persistent desktops get an `openvdi-base` snapshot taken after first boot, and are rolled back (or destroyed) on logoff.

## Architecture Diagram

```
┌─────────────────────────────────────┐  ┌──────────────────────────────────┐
│  Web Portal (React)                 │  │  AI Agents                       │
│  - User desktop launcher            │  │  (Claude Desktop / Code,         │
│  - Console renderer (noVNC v0)      │  │   Praxova IT Agent, custom)      │
│  - Admin dashboard                  │  │                                  │
└──────────────────┬──────────────────┘  └──────────────┬───────────────────┘
                   │ REST API (JWT)                      │ MCP protocol (stdio)
                   │                            ┌────────▼────────────────────┐
                   │                            │  openvdi-admin MCP server   │
                   │                            │  (mcp/openvdi-admin/)       │
                   │                            │  - 37 thin-wrapper tools    │
                   │                            │  - 6 intent tools           │
                   │                            └────────┬────────────────────┘
                   │                                     │ REST API (JWT,
                   │                                     │  service-account auth)
┌──────────────────▼─────────────────────────────────────▼──────────────────────┐
│  OpenVDI Broker (FastAPI)                                                     │
│                                                     │
│  ┌──────────┐ ┌──────────┐ ┌─────────────────────┐ │
│  │ Auth     │ │ Pool Mgr │ │ Session Manager     │ │
│  │ (LDAP/AD)│ │          │ │                     │ │
│  └──────────┘ └──────────┘ └─────────────────────┘ │
│                                                     │
│  ┌──────────────────────────────────────────────┐   │
│  │ Services Layer                               │   │
│  │ - Broker (connection assignment)             │   │
│  │ - Provisioner (clone, snapshot, lifecycle)   │   │
│  │ - VMID/handle Allocator                      │   │
│  │ - Session Tracker (state machine)            │   │
│  │                                              │   │
│  │ depends on: HypervisorProvider Protocol      │   │
│  └──────────────────────────────────────────────┘   │
│                                                     │
│  ┌──────────────────────────────────────────────┐   │
│  │ Background Workers                           │   │
│  │ - Pool Provisioner (maintain warm spares)    │   │
│  │ - Session Monitor (guest agent polling)      │   │
│  │ - Health Checker (cluster/node/storage)      │   │
│  │ - Task Tracker (async provider tasks)        │   │
│  │                                              │   │
│  │ depends on: HypervisorProvider Protocol      │   │
│  └──────────────────────────────────────────────┘   │
│                                                     │
│  ┌──────────────────────────────────────────────┐   │
│  │ Provider Layer  (broker/app/providers/)      │   │
│  │                                              │   │
│  │   base.py      Protocol + shared types       │   │
│  │                                              │   │
│  │   proxmox/     ProxmoxProvider (v0)          │   │
│  │   vsphere/     (future)                      │   │
│  │   hyperv/      (future)                      │   │
│  └──────────────────────────────────────────────┘   │
│                                                     │
│  ┌──────────┐                                       │
│  │PostgreSQL│                                       │
│  └──────────┘                                       │
└──────────────────┬──────────────────────────────────┘
                   │ Hypervisor-specific API
                   │ (Proxmox REST :8006, vSphere SOAP, ...)
┌──────────────────▼──────────────────────────────────┐
│  Hypervisor Cluster (Proxmox VE in v0)              │
│  - Virtual Machines + Templates                     │
│  - Guest Agent (QEMU / VMware Tools / ...)          │
│  - Console proxies (noVNC / WebMKS / ...)           │
│  - Storage (local, Ceph, NFS, ZFS, vSAN, ...)       │
└─────────────────────────────────────────────────────┘
```

## Technical Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| Hypervisor (v0) | Proxmox VE 9.x | KVM-based, full REST API (480 endpoints), open source |
| Hypervisor abstraction | `HypervisorProvider` Protocol | Avoid vendor lock-in; see `providers.md` |
| Backend | Python / FastAPI | Async, consistent with Praxova tooling |
| Database | PostgreSQL | Robust, JSONB support, familiar |
| Frontend | React + Vite | Rich SPA, noVNC integration |
| HTTP client (Proxmox provider) | httpx (async) | Native async HTTP, connection pooling |
| Auth | ldap3 | Direct AD/LDAP authentication |
| Display protocol (v0) | noVNC | Browser-native, no client install, broadest provider support |
| Display protocol (target) | KasmVNC | WebRTC transport, GPU encode, WAN quality |
| Proxmox API spec tooling | pve-spec-query MCP | 6 tools, 480 endpoints indexed |
| Infrastructure-as-Code | OpenTofu/Terraform | Deployment automation |

## Protocol Roadmap

- **v0 (MVP):** noVNC via the hypervisor's native WebSocket VNC proxy (on Proxmox, the `vncproxy` endpoint). LAN-only, browser-based, no client install. Providers that cannot natively produce a noVNC-compatible WebSocket (e.g. Hyper-V) are out of scope for v0.
- **v1:** KasmVNC (GPLv2, WebRTC transport, NVENC support). WAN-capable, better compression. Runs inside the VM template, so it becomes less provider-dependent.
- **v2:** Custom protocol, deep KasmVNC fork, or RDP-via-Guacamole bridge if specific requirements emerge.

## MCP Surface

OpenVDI exposes a Model Context Protocol (MCP) server — `openvdi-admin` —
that AI agents use to drive the broker. The MCP is the productization
layer: the broker stays free and open source, paid agents (Praxova IT
Agent, custom installer agents) are where Praxova captures value.

The MCP is operational, not in the data path. Every MCP tool call
becomes one or more REST requests to the broker; broker responses flow
back to the agent. If the MCP goes down, agents lose their tool surface
but the broker, portal, and existing sessions are unaffected.

### Layering

```
AI Agent ─MCP protocol→ openvdi-admin MCP ─REST(JWT)→ Broker ─→ Hypervisor
```

The MCP is sibling to the portal in dependency posture: both consume the
broker's REST API; neither talks to the hypervisor directly. Adding a
second hypervisor provider (vSphere, Hyper-V) would require no MCP
changes — the MCP wraps broker endpoints, not provider endpoints.

### Tool catalog

Two layers, ~43 tools total:

- **37 thin wrappers** — one per admin endpoint. Naming: `openvdi_<verb>_<resource>` (e.g. `openvdi_list_clusters`, `openvdi_create_pool`, `openvdi_power_desktop`). Thin wrappers can do everything the broker can do.
- **6 intent tools** — composed from thin wrappers; bake in domain knowledge for high-value workflows: `openvdi_smoke_test`, `openvdi_deploy_pool`, `openvdi_diagnose_user`, `openvdi_diagnose_pool`, `openvdi_health_check`, `openvdi_reset_test_environment`.

Intent tools NEVER call the broker directly — they go through the thin
wrappers. This guarantees that improvements to a thin wrapper propagate
to every intent tool that uses it.

### Authentication

The MCP authenticates as a regular AD service account that's a member
of `OPENVDI_LDAP_ADMIN_GROUP`. No new "service account" concept — it
authenticates exactly the way an admin user does from the portal. The
broker's audit log attributes every action to the service account; if
the operator wants per-agent attribution, they configure separate AD
service accounts per agent product.

### Safety posture

Every destructive tool defaults to a dry-run preview (`confirm=False`);
the agent must explicitly pass `confirm=True` to execute. The MCP also
honors a global `OPENVDI_MCP_READ_ONLY=true` env switch that blocks
every destructive tool — useful for diagnostic-only deployments.

The MCP has no persistent state and no audit log of its own. The
broker's audit log is the audit trail; agents are correlated via
`X-Request-ID` headers so operators can grep one UUID across MCP and
broker log streams.

### Productization

The MCP itself is Apache 2.0 (matching the OpenVDI broker and
Praxova's IT Agent) and stays free during beta. The agents that
drive it (Praxova IT Agent's OpenVDI tool server, customer installer
agents) are where Praxova charges. Customers running their own agent
on top of the MCP pay nothing for the MCP layer.

### Scope

v0 ships the operational MCP only — the running-broker case. A separate
`openvdi-installer` MCP for new-customer onboarding (operator
prerequisites, service account creation, broker bring-up) is M6+ work.
The two MCPs have different threat models and authentication stories
that deserve separate design.

## Key Technical Risks

| Risk | Mitigation |
|------|-----------|
| WAN display quality (SPICE/noVNC are LAN-oriented) | KasmVNC for v1; noVNC acceptable for MVP LAN scope |
| NVIDIA vGPU licensing on Proxmox | Defer to Phase 2; target SR-IOV on Intel GPUs and full passthrough first |
| No instant clones in Proxmox | Linked clones from templates; warm spare pool compensates for clone time |
| noVNC proxy requires direct browser→hypervisor connectivity | LAN-only for MVP; reverse proxy for WAN in v1 |
| Guest agent not installed in template | Make it a mandatory template requirement; validate on template registration |
| LVM lock orphans after unclean shutdown (Proxmox) | Boot-time `lvm-lock-cleanup.service` on each PVE node; documented in deployment guide |
| Lowest-common-denominator abstraction | `ProviderCapabilities` + `provider_opts` preserve provider-specific strengths instead of flattening them |

## Competitive Position

| Product | Status | Weakness |
|---------|--------|----------|
| UDS Enterprise (Virtual Cable) | Active, ~$60/user | Clunky UI, Spanish company, expensive |
| Deskpool | Dead | Company unresponsive |
| PVE-VDIClient | Active | Thin SPICE launcher, not a platform |
| openVDI (PAzter1101) | Very early | One-person project, no docs |
| Apache Guacamole | Active | HTML5 gateway only, no pool management |
| Omnissa Horizon (post-Broadcom) | Active but uncertain | Tightly bound to vCenter; hypervisor migration is a rewrite |
| **OpenVDI** | **Building** | **Open source, modern, hypervisor-agnostic, Horizon-expert-built** |
