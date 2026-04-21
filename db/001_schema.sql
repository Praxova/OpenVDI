-- OpenVDI database schema
-- Run: psql -h localhost -U openvdi -d openvdi -f db/001_schema.sql

BEGIN;

-- ============================================================
-- Proxmox cluster connections
-- ============================================================
CREATE TABLE clusters (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(128) NOT NULL UNIQUE,
    api_url         VARCHAR(512) NOT NULL,
    token_id        VARCHAR(256) NOT NULL,
    token_secret    VARCHAR(256) NOT NULL,
    verify_ssl      BOOLEAN DEFAULT TRUE,
    node_filter     VARCHAR(512),
    status          VARCHAR(32) DEFAULT 'active',
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
    pve_vmid        INTEGER NOT NULL,
    pve_node        VARCHAR(128) NOT NULL,
    os_type         VARCHAR(32) NOT NULL,
    description     TEXT,
    cpu_cores       INTEGER NOT NULL DEFAULT 2,
    memory_mb       INTEGER NOT NULL DEFAULT 4096,
    disk_gb         INTEGER NOT NULL DEFAULT 60,
    gpu_required    BOOLEAN DEFAULT FALSE,
    tags            JSONB DEFAULT '[]',
    status          VARCHAR(32) DEFAULT 'active',
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
    min_spare       INTEGER NOT NULL DEFAULT 1,
    max_size        INTEGER NOT NULL DEFAULT 10,

    -- VMID range
    vmid_range_start INTEGER NOT NULL,
    vmid_range_end   INTEGER NOT NULL,

    -- VM naming
    name_prefix     VARCHAR(32) NOT NULL,

    -- Placement
    target_nodes    VARCHAR(512),
    target_storage  VARCHAR(128),

    -- VM overrides (null = inherit from template)
    cpu_cores       INTEGER,
    memory_mb       INTEGER,

    -- Proxmox pool
    pve_pool_id     VARCHAR(128),

    -- Behavior
    auto_logoff_min INTEGER DEFAULT 0,
    delete_on_logoff BOOLEAN DEFAULT FALSE,
    refresh_on_logoff BOOLEAN DEFAULT TRUE,

    status          pool_status DEFAULT 'active',
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),

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
    pve_vmid        INTEGER NOT NULL,
    pve_node        VARCHAR(128) NOT NULL,
    name            VARCHAR(256) NOT NULL,

    assigned_user   VARCHAR(256),
    assignment_type VARCHAR(32),

    status          desktop_status DEFAULT 'provisioning',
    power_state     VARCHAR(32) DEFAULT 'stopped',
    spice_enabled   BOOLEAN DEFAULT FALSE,

    last_connected  TIMESTAMPTZ,
    last_disconnected TIMESTAMPTZ,
    provisioned_at  TIMESTAMPTZ,
    error_message   TEXT,

    pve_task_upid   VARCHAR(512),

    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE(pve_vmid)
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
    protocol        VARCHAR(32) NOT NULL,
    client_ip       INET,

    status          session_status DEFAULT 'connecting',
    connected_at    TIMESTAMPTZ,
    disconnected_at TIMESTAMPTZ,
    ended_at        TIMESTAMPTZ,

    connection_info JSONB,

    os_user         VARCHAR(256),
    os_info         JSONB,
    vm_ip_address   INET,
    last_heartbeat  TIMESTAMPTZ,
    idle_since      TIMESTAMPTZ,

    created_at      TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- Entitlements (who can access which pools)
-- ============================================================
CREATE TABLE entitlements (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pool_id         UUID NOT NULL REFERENCES pools(id),
    principal_type  VARCHAR(32) NOT NULL,
    principal_name  VARCHAR(256) NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE(pool_id, principal_type, principal_name)
);

-- ============================================================
-- Audit log
-- ============================================================
CREATE TABLE audit_log (
    id              BIGSERIAL PRIMARY KEY,
    timestamp       TIMESTAMPTZ DEFAULT now(),
    actor           VARCHAR(256),
    action          VARCHAR(128) NOT NULL,
    resource_type   VARCHAR(64),
    resource_id     UUID,
    details         JSONB,
    client_ip       INET
);

-- ============================================================
-- Session metrics (future)
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
