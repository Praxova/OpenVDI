# OpenVDI Database Schema

## Overview

PostgreSQL database storing pool definitions, desktop inventory, session state, user entitlements, and audit history. The hypervisor remains the source of truth for VM runtime state; this database owns the VDI management layer.

The schema carries hypervisor-agnostic fields in the core columns and uses a `provider_type` discriminator plus an opaque `provider_config` JSONB blob for provider-specific configuration. Column names like `pve_vmid` and `pve_node` are intentionally retained for Milestone 1+2 and marked as "Proxmox provider only" in this document; they'll be generalized (or moved into `provider_config`) before a second provider lands. Doing the full generalization now would gold-plate fields we don't yet know the shape of.

## Entity Relationship Summary

```
clusters 1──* templates 1──* pools 1──* desktops 1──* sessions
                                   1──* entitlements

audit_log (standalone)
session_metrics (future, linked to sessions)
```

## Schema

```sql
-- ============================================================
-- Hypervisor cluster connections
-- ============================================================
CREATE TABLE clusters (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(128) NOT NULL UNIQUE,

    -- Provider discriminator. Matches HypervisorProvider.provider_type.
    -- First supported value: 'proxmox'. Future: 'vsphere', 'hyperv', etc.
    provider_type   VARCHAR(32) NOT NULL DEFAULT 'proxmox',

    api_url         VARCHAR(512) NOT NULL,       -- https://pve1.example.com:8006 (or provider equivalent)
    token_id        VARCHAR(256) NOT NULL,       -- user@realm!tokenid (Proxmox) or provider equivalent
    token_secret    VARCHAR(256) NOT NULL,       -- Fernet-encrypted at rest; key from OPENVDI_ENCRYPTION_KEY env var
    verify_ssl      BOOLEAN DEFAULT TRUE,
    node_filter     VARCHAR(512),                -- optional: limit to specific nodes

    -- Provider-specific configuration not worth a first-class column.
    -- Interpreted by the concrete provider implementation. Examples:
    --   Proxmox: {"realm": "pve"}
    --   vSphere: {"datacenter": "DC1", "cluster": "Prod"}
    provider_config JSONB DEFAULT '{}'::jsonb,

    status          VARCHAR(32) DEFAULT 'pending',-- pending, active, maintenance, offline
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- Golden image templates
-- ============================================================
CREATE TABLE templates (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cluster_id      UUID NOT NULL REFERENCES clusters(id),
    name            VARCHAR(256) NOT NULL,

    -- Proxmox provider: VMID of the template. Other providers store the
    -- equivalent native ID (vSphere MoRef, Hyper-V name+host, etc.) here
    -- or in provider_config until a cross-provider shape emerges.
    pve_vmid        INTEGER NOT NULL,
    pve_node        VARCHAR(128) NOT NULL,        -- node where template lives

    os_type         VARCHAR(32) NOT NULL,         -- windows11, windows10, ubuntu24, rhel9
    description     TEXT,
    cpu_cores       INTEGER NOT NULL DEFAULT 2,
    memory_mb       INTEGER NOT NULL DEFAULT 4096,
    disk_gb         INTEGER NOT NULL DEFAULT 60,
    gpu_required    BOOLEAN DEFAULT FALSE,
    tags            JSONB DEFAULT '[]',           -- arbitrary metadata

    -- Provider-specific settings for this template (rare; most providers
    -- don't need any). Available for future use.
    provider_config JSONB DEFAULT '{}'::jsonb,

    status          VARCHAR(32) DEFAULT 'active', -- active, building, error, retired
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE(cluster_id, pve_vmid)
);

-- ============================================================
-- Desktop pools
-- ============================================================
CREATE TYPE pool_type AS ENUM ('persistent', 'nonpersistent');
CREATE TYPE pool_status AS ENUM (
    'active', 'disabled', 'provisioning', 'error', 'draining'
);

CREATE TABLE pools (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(128) NOT NULL UNIQUE,
    display_name    VARCHAR(256) NOT NULL,
    description     TEXT,
    pool_type       pool_type NOT NULL,
    template_id     UUID NOT NULL REFERENCES templates(id),
    cluster_id      UUID NOT NULL REFERENCES clusters(id),

    -- Capacity
    min_spare       INTEGER NOT NULL DEFAULT 1,  -- warm spares for nonpersistent
    max_size        INTEGER NOT NULL DEFAULT 10, -- max VMs in pool

    -- VMID range (Proxmox provider). For non-Proxmox providers, this is
    -- reinterpreted or ignored by the provider's handle allocator.
    vmid_range_start INTEGER NOT NULL,           -- e.g. 5000
    vmid_range_end   INTEGER NOT NULL,           -- e.g. 5099
    -- Application-layer validation ensures ranges don't overlap.

    -- VM naming
    name_prefix     VARCHAR(32) NOT NULL,        -- e.g. "ENG" -> ENG-001, ENG-002

    -- Placement
    target_nodes    VARCHAR(512),                -- comma-sep node list, null = any
    target_storage  VARCHAR(128),                -- storage for clones, null = same as template

    -- VM overrides (null = inherit from template)
    cpu_cores       INTEGER,
    memory_mb       INTEGER,

    -- Proxmox native "pool" (organizational grouping in PVE UI).
    -- Other providers have their own equivalent concept surfaced via
    -- provider_config.
    pve_pool_id     VARCHAR(128),

    -- Provider-specific pool settings. Examples:
    --   Proxmox: {"vmid_allocation": "sequential"}
    --   vSphere: {"resource_pool": "VDI-Prod", "folder": "Engineering"}
    provider_config JSONB DEFAULT '{}'::jsonb,

    -- Behavior
    auto_logoff_min INTEGER DEFAULT 0,           -- 0 = disabled
    delete_on_logoff BOOLEAN DEFAULT FALSE,      -- nonpersistent: destroy VM on logoff
    refresh_on_logoff BOOLEAN DEFAULT TRUE,      -- nonpersistent: revert to snapshot

    status          pool_status DEFAULT 'active',
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),

    -- Constraints
    CONSTRAINT vmid_range_valid CHECK (vmid_range_start < vmid_range_end),
    CONSTRAINT max_size_within_range CHECK (max_size <= (vmid_range_end - vmid_range_start + 1))
);

-- ============================================================
-- Individual desktops (VMs managed by OpenVDI)
-- ============================================================
CREATE TYPE desktop_status AS ENUM (
    'provisioning', 'available', 'assigned', 'connected',
    'disconnected', 'error', 'deleting', 'maintenance'
);

CREATE TABLE desktops (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pool_id         UUID NOT NULL REFERENCES pools(id),

    -- Proxmox provider: VMID and node. Other providers use the same
    -- columns or shift to provider-specific storage in a future schema
    -- version; for v0 we keep them as-is and let the provider encode
    -- its VMRef accordingly.
    pve_vmid        INTEGER NOT NULL,
    pve_node        VARCHAR(128) NOT NULL,

    name            VARCHAR(256) NOT NULL,       -- e.g. ENG-003

    -- Assignment
    --   assigned_user: AD username (null = unassigned).
    --   assignment_type: 'persistent' (survives session end) or 'floating'
    --     (cleared on session end by the session tracker; the desktop
    --     returns to the pool's warm-spare population). Null when
    --     assigned_user is null.
    assigned_user   VARCHAR(256),
    assignment_type VARCHAR(32),

    -- State
    status          desktop_status DEFAULT 'provisioning',
    power_state     VARCHAR(32) DEFAULT 'stopped', -- running, stopped, paused
    spice_enabled   BOOLEAN DEFAULT FALSE,

    -- Tracking
    last_connected  TIMESTAMPTZ,
    last_disconnected TIMESTAMPTZ,
    provisioned_at  TIMESTAMPTZ,
    error_message   TEXT,

    -- Provider async task tracking. For Proxmox: the UPID.
    -- For other providers: their native async task identifier.
    pve_task_upid   VARCHAR(512),

    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE(pve_vmid)                             -- scoped per-provider; globally unique within a single-provider v0
);

-- ============================================================
-- User sessions
-- ============================================================
CREATE TYPE session_status AS ENUM (
    'connecting', 'active', 'disconnected', 'ended'
);

CREATE TABLE sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    desktop_id      UUID NOT NULL REFERENCES desktops(id),
    username        VARCHAR(256) NOT NULL,
    protocol        VARCHAR(32) NOT NULL,         -- novnc, spice, kasmvnc, webmks, rdp
    client_ip       INET,

    status          session_status DEFAULT 'connecting',
    connected_at    TIMESTAMPTZ,
    disconnected_at TIMESTAMPTZ,
    ended_at        TIMESTAMPTZ,

    -- Connection details (ephemeral, cleared after session). Shape is
    -- ConsoleTicket (see providers.md) serialized as JSON.
    connection_info JSONB,

    -- Guest agent telemetry (populated by session monitor)
    os_user         VARCHAR(256),               -- actual OS login username
    os_info         JSONB,                      -- osinfo snapshot
    vm_ip_address   INET,                       -- in-VM IP from guest agent
    last_heartbeat  TIMESTAMPTZ,                -- last successful agent poll
    idle_since      TIMESTAMPTZ,                -- future: idle detection

    created_at      TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- Entitlements (who can access which pools)
-- ============================================================
CREATE TABLE entitlements (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pool_id         UUID NOT NULL REFERENCES pools(id),
    principal_type  VARCHAR(32) NOT NULL,         -- 'user' or 'group'
    principal_name  VARCHAR(256) NOT NULL,        -- AD user or group name
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE(pool_id, principal_type, principal_name)
);

-- ============================================================
-- Audit log
-- ============================================================
CREATE TABLE audit_log (
    id              BIGSERIAL PRIMARY KEY,
    timestamp       TIMESTAMPTZ DEFAULT now(),
    actor           VARCHAR(256),                 -- username or 'system'
    action          VARCHAR(128) NOT NULL,
    resource_type   VARCHAR(64),                  -- pool, desktop, session, template
    resource_id     UUID,
    details         JSONB,
    client_ip       INET
);

-- ============================================================
-- Session metrics (future — OpenVDI agent telemetry)
-- ============================================================
CREATE TABLE session_metrics (
    id              BIGSERIAL PRIMARY KEY,
    session_id      UUID NOT NULL REFERENCES sessions(id),
    timestamp       TIMESTAMPTZ DEFAULT now(),
    cpu_percent     REAL,
    memory_percent  REAL,
    disk_io_read    BIGINT,
    disk_io_write   BIGINT,
    network_rx      BIGINT,
    network_tx      BIGINT,
    active_apps     JSONB
);

-- ============================================================
-- Indexes
-- ============================================================
CREATE INDEX idx_clusters_provider_type ON clusters(provider_type);
CREATE INDEX idx_desktops_pool ON desktops(pool_id);
CREATE INDEX idx_desktops_assigned ON desktops(assigned_user) WHERE assigned_user IS NOT NULL;
CREATE INDEX idx_desktops_status ON desktops(status);
CREATE INDEX idx_desktops_vmid ON desktops(pve_vmid);
CREATE INDEX idx_sessions_desktop ON sessions(desktop_id);
CREATE INDEX idx_sessions_user ON sessions(username);
CREATE INDEX idx_sessions_active ON sessions(status) WHERE status IN ('connecting', 'active');
CREATE INDEX idx_entitlements_pool ON entitlements(pool_id);
CREATE INDEX idx_entitlements_principal ON entitlements(principal_name);
CREATE INDEX idx_audit_timestamp ON audit_log(timestamp);
CREATE INDEX idx_audit_resource ON audit_log(resource_type, resource_id);
CREATE INDEX idx_session_metrics_session ON session_metrics(session_id, timestamp);
```

## VMID Allocation Strategy (Proxmox Provider)

Each pool is assigned a non-overlapping integer range (e.g. 5000-5099). Within that range, VMIDs are allocated using a **lowest-available** strategy: at clone time, the allocator picks the smallest integer in the range not currently used by any `desktops` row for that pool. Deleted desktops free their VMID for reuse.

- Range is specified at pool creation time.
- Application layer validates no overlap with existing pools.
- At pool creation (`POST /pools`), the allocator scans Proxmox for any VMs already present in the range. If any are found, the pool creation is rejected with `CONFLICT`.
- At clone time, the allocator trusts the DB. If a collision is discovered when Proxmox rejects the clone (409), the allocator retries once with the next candidate VMID.
- `max_size` is constrained to fit within the range via `CHECK` constraint.
- Concurrency: the allocator acquires a Postgres transaction-scoped advisory lock keyed on the pool ID (`pg_advisory_xact_lock(hashtext('pool:' || pool_id::text))`) before selecting and inserting. This serializes allocations on a per-pool basis without global locking.
- Provides visual grouping in Proxmox UI (all Engineering desktops are 5000-5099).

For non-Proxmox providers, handle allocation is the provider's concern; the `vmid_range_*` columns may be ignored by the provider and the allocator delegated to `provider_config`. The shape will be formalized when a second provider is added.

## VM Tagging Convention

VDI-managed VMs are tagged for visibility and disaster recovery. Tags are discovery/UI metadata; the OpenVDI **database is authoritative**. The VM description field carries a human-readable `key=value` summary as a DR fallback for the cases where a username slugifies lossily (see below).

**Proxmox constraint:** tag tokens must match `[a-z0-9_-]`. Colons and equals signs are rejected with HTTP 400 `invalid format - invalid characters in tag`. All tag values are lowercased and any character outside `[a-z0-9_-]` is replaced with `-`; runs of `-` are collapsed, and leading/trailing `-` are stripped. This slug transform is applied in a single helper shared across the code that writes tags.

All providers that support tags use the same tag vocabulary:

```
openvdi-managed                  # always applied to every OpenVDI-managed desktop
openvdi-pool-{pool_slug}         # always applied
openvdi-type-{persistent|nonpersistent}   # always applied
openvdi-user-{username_slug}     # only for assigned desktops (persistent or floating)
```

**Constraints that fall out of this:**

1. Pool names are validated at `POST /pools` / `PUT /pools` to match `[a-z0-9_-]` directly, so `pool_name == pool_slug` — no information loss on the pool dimension. Same pattern Kubernetes uses for resource names.
2. Usernames are NOT constrained (AD/LDAP is authoritative; OpenVDI cannot dictate `sAMAccountName` format). The tag value is therefore lossy for usernames containing `.`, spaces, or non-ASCII characters. Recovery from tags alone gets e.g. `alton-bobbitt`; the unmodified `alton.bobbitt` is preserved in the VM description field.

**VM description format** (set at provisioning time, freeform string — Proxmox does not validate its contents):

```
OpenVDI: pool=engineering type=nonpersistent assigned=alton.bobbitt
```

Providers without native tag support either simulate tags in a provider-specific way or skip tagging (visibility-only degradation). The description field is universal and every provider populates it.

## Notes

- `token_secret` in the clusters table is encrypted at rest via application-layer **Fernet** symmetric encryption (`cryptography.fernet`). The encryption key is loaded from the `OPENVDI_ENCRYPTION_KEY` environment variable at broker startup; the broker refuses to start if the key is missing or malformed. Encryption and decryption are handled exclusively in the service layer — models store ciphertext, endpoints never return the plaintext to the client.
- `clusters.status` values: `pending` (pre-first-ping: newly registered or just updated; awaiting background health check), `active` (last ping succeeded), `maintenance` (admin-disabled), `offline` (last ping failed). Transitions: `pending → active | offline` on first ping completion; `active ↔ offline` on subsequent ping outcomes; `* → maintenance` on admin action.
- `desktops.assigned_user` semantics: populated for both persistent and non-persistent desktops while a session is active. On session end, `assignment_type='floating'` desktops have `assigned_user` cleared (the desktop returns to the available pool); `assignment_type='persistent'` desktops retain `assigned_user`. Per-pool assignment applies — a user may simultaneously hold one desktop per pool they are entitled to.
- `connection_info` in sessions is ephemeral — cleared when session ends to avoid leaking tickets. The shape is a `ConsoleTicket` (see `providers.md`) serialized to JSON; the portal reads the `kind` field to route to the correct renderer.
- `session_metrics` table is for future OpenVDI in-VM agent; not used in MVP.
- All timestamps are TIMESTAMPTZ (UTC).
- The Proxmox-flavored column names (`pve_vmid`, `pve_node`, `pve_task_upid`, `pve_pool_id`) are retained for the v0 single-provider case. When a second provider lands, these columns will be either renamed (generic) or moved into per-desktop JSONB — decided based on what the second provider's data model looks like, not speculatively.
