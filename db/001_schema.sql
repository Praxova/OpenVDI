-- OpenVDI schema — initial creation.
-- Not idempotent: running on an already-populated DB will fail.
-- For a clean reset, run db/drop_all.sql first.
-- Source of truth: docs/database-schema.md

BEGIN;

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
    token_secret    VARCHAR(256) NOT NULL,       -- Fernet ciphertext; key in OPENVDI_ENCRYPTION_KEY
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
    assigned_user   VARCHAR(256),                -- AD username (null = unassigned)
    assignment_type VARCHAR(32),                 -- 'persistent' (survives session end) or
                                                 -- 'floating' (cleared by session tracker on session end)

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

COMMIT;
