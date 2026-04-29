"""baseline (M3 schema state, model-aligned)

Consolidates db/001_schema.sql + db/003_add_task_kind.sql +
db/004_add_pool_status_deleting.sql + db/006_sessions_desktop_id_set_null.sql +
db/007_entitlements_pool_id_cascade.sql into one Alembic revision.

Aligned with broker/app/models/*. Where the M2-era SQL files left
columns nullable that the models declare as required, where FKs
defaulted to NO ACTION but the models declare RESTRICT, where
constraint names auto-derived but the models specify explicit names,
and where session_metrics columns were REAL but the models say Float
— this baseline canonicalizes the model intent. M4-01's onboarding
guidance (drop + recreate the dev DB; do not stamp an existing loose
DB) reflects that this baseline produces a TIGHTER schema than the
historical 001-007 sequence did.

db/002_seed_data.sql is data, not schema, and is not represented here
— it continues to be applied separately via scripts/db-reset.sh.
db/005_cleanup_connection_info_json_null.sql is a one-shot data
cleanup for legacy rows; the M2-09-fix that prevents the regression
lives in the model layer (JSONB(none_as_null=True)) and is reflected
in the column declarations below.

Revision ID: 0001_baseline_m3
Revises: (none — this is the root)
Create Date: 2026-04-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "0001_baseline_m3"
down_revision = None
branch_labels = None
depends_on = None


# ── Enum types ──────────────────────────────────────────────────
# Each column uses `create_type=False` to skip re-creation; the
# enum types themselves are created up front via op.execute. The
# 'deleting' value on pool_status comes from the 004 patch,
# consolidated into the baseline declaration here.

_pool_type = postgresql.ENUM(
    "persistent", "nonpersistent",
    name="pool_type", create_type=False,
)
_pool_status = postgresql.ENUM(
    "active", "disabled", "provisioning", "error", "draining", "deleting",
    name="pool_status", create_type=False,
)
_desktop_status = postgresql.ENUM(
    "provisioning", "available", "assigned", "connected",
    "disconnected", "error", "deleting", "maintenance",
    name="desktop_status", create_type=False,
)
_session_status = postgresql.ENUM(
    "connecting", "active", "disconnected", "ended",
    name="session_status", create_type=False,
)


def upgrade() -> None:
    # ── Enum types ──────────────────────────────────────────────
    op.execute("CREATE TYPE pool_type AS ENUM ('persistent', 'nonpersistent')")
    op.execute(
        "CREATE TYPE pool_status AS ENUM ("
        "'active', 'disabled', 'provisioning', 'error', 'draining', 'deleting'"
        ")"
    )
    op.execute(
        "CREATE TYPE desktop_status AS ENUM ("
        "'provisioning', 'available', 'assigned', 'connected', "
        "'disconnected', 'error', 'deleting', 'maintenance'"
        ")"
    )
    op.execute(
        "CREATE TYPE session_status AS ENUM ("
        "'connecting', 'active', 'disconnected', 'ended'"
        ")"
    )

    # ── clusters ────────────────────────────────────────────────
    # status is VARCHAR(32) (not an enum) per M2-01.
    # provider_type/provider_config carry the abstraction added pre-M2.
    op.create_table(
        "clusters",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column(
            "provider_type", sa.String(32), nullable=False,
            server_default=sa.text("'proxmox'"),
        ),
        sa.Column("api_url", sa.String(512), nullable=False),
        sa.Column("token_id", sa.String(256), nullable=False),
        # Fernet ciphertext; key in OPENVDI_ENCRYPTION_KEY.
        sa.Column("token_secret", sa.String(256), nullable=False),
        sa.Column(
            "verify_ssl", sa.Boolean, nullable=False,
            server_default=sa.text("TRUE"),
        ),
        sa.Column("node_filter", sa.String(512)),
        sa.Column(
            "provider_config", postgresql.JSONB, nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "status", sa.String(32), nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ── templates ───────────────────────────────────────────────
    # FK ondelete=RESTRICT matches the model declaration (the M2 SQL
    # didn't specify; the baseline canonicalizes to model intent).
    op.create_table(
        "templates",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "cluster_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clusters.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("pve_vmid", sa.Integer, nullable=False),
        sa.Column("pve_node", sa.String(128), nullable=False),
        sa.Column("os_type", sa.String(32), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column(
            "cpu_cores", sa.Integer, nullable=False,
            server_default=sa.text("2"),
        ),
        sa.Column(
            "memory_mb", sa.Integer, nullable=False,
            server_default=sa.text("4096"),
        ),
        sa.Column(
            "disk_gb", sa.Integer, nullable=False,
            server_default=sa.text("60"),
        ),
        sa.Column(
            "gpu_required", sa.Boolean, nullable=False,
            server_default=sa.text("FALSE"),
        ),
        sa.Column(
            "tags", postgresql.JSONB, nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "provider_config", postgresql.JSONB, nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "status", sa.String(32), nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "cluster_id", "pve_vmid",
            name="templates_cluster_vmid_uq",
        ),
    )

    # ── pools ───────────────────────────────────────────────────
    # status enum already includes 'deleting' (post-004).
    # CHECK constraints from 001_schema.sql.
    op.create_table(
        "pools",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("display_name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("pool_type", _pool_type, nullable=False),
        sa.Column(
            "template_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("templates.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "cluster_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clusters.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "min_spare", sa.Integer, nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "max_size", sa.Integer, nullable=False,
            server_default=sa.text("10"),
        ),
        sa.Column("vmid_range_start", sa.Integer, nullable=False),
        sa.Column("vmid_range_end", sa.Integer, nullable=False),
        sa.Column("name_prefix", sa.String(32), nullable=False),
        sa.Column("target_nodes", sa.String(512)),
        sa.Column("target_storage", sa.String(128)),
        sa.Column("cpu_cores", sa.Integer),
        sa.Column("memory_mb", sa.Integer),
        sa.Column("pve_pool_id", sa.String(128)),
        sa.Column(
            "provider_config", postgresql.JSONB, nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "auto_logoff_min", sa.Integer, nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "delete_on_logoff", sa.Boolean, nullable=False,
            server_default=sa.text("FALSE"),
        ),
        sa.Column(
            "refresh_on_logoff", sa.Boolean, nullable=False,
            server_default=sa.text("TRUE"),
        ),
        sa.Column(
            "status", _pool_status, nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "vmid_range_start < vmid_range_end",
            name="vmid_range_valid",
        ),
        sa.CheckConstraint(
            "max_size <= (vmid_range_end - vmid_range_start + 1)",
            name="max_size_within_range",
        ),
    )

    # ── desktops ────────────────────────────────────────────────
    # pve_task_kind comes from 003 — included inline in baseline.
    op.create_table(
        "desktops",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "pool_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pools.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("pve_vmid", sa.Integer, nullable=False),
        sa.Column("pve_node", sa.String(128), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("assigned_user", sa.String(256)),
        sa.Column("assignment_type", sa.String(32)),
        sa.Column(
            "status", _desktop_status, nullable=False,
            server_default=sa.text("'provisioning'"),
        ),
        sa.Column(
            "power_state", sa.String(32), nullable=False,
            server_default=sa.text("'stopped'"),
        ),
        sa.Column(
            "spice_enabled", sa.Boolean, nullable=False,
            server_default=sa.text("FALSE"),
        ),
        sa.Column("last_connected", sa.DateTime(timezone=True)),
        sa.Column("last_disconnected", sa.DateTime(timezone=True)),
        sa.Column("provisioned_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.Text),
        sa.Column("pve_task_upid", sa.String(512)),
        # 003 patch — VARCHAR(32) per the patch file. Nullable;
        # populated only while a task is in flight.
        sa.Column("pve_task_kind", sa.String(32)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("pve_vmid", name="desktops_pve_vmid_uq"),
    )

    # ── sessions ────────────────────────────────────────────────
    # desktop_id: nullable + ON DELETE SET NULL (post-006).
    # connection_info / os_info: JSONB. The model declares them with
    # JSONB(none_as_null=True); none_as_null is a SQLAlchemy
    # compile-time option, NOT a DDL artifact, so the rendered DDL
    # is plain JSONB either way. Included for symmetry with the model.
    op.create_table(
        "sessions",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "desktop_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("desktops.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("username", sa.String(256), nullable=False),
        sa.Column("protocol", sa.String(32), nullable=False),
        sa.Column("client_ip", postgresql.INET),
        sa.Column(
            "status", _session_status, nullable=False,
            server_default=sa.text("'connecting'"),
        ),
        sa.Column("connected_at", sa.DateTime(timezone=True)),
        sa.Column("disconnected_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column(
            "connection_info",
            postgresql.JSONB(none_as_null=True),
        ),
        sa.Column("os_user", sa.String(256)),
        sa.Column(
            "os_info",
            postgresql.JSONB(none_as_null=True),
        ),
        sa.Column("vm_ip_address", postgresql.INET),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True)),
        sa.Column("idle_since", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ── entitlements ────────────────────────────────────────────
    # pool_id: ON DELETE CASCADE (post-007).
    op.create_table(
        "entitlements",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "pool_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pools.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("principal_type", sa.String(32), nullable=False),
        sa.Column("principal_name", sa.String(256), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "pool_id", "principal_type", "principal_name",
            name="entitlements_pool_principal_uq",
        ),
    )

    # ── audit_log ───────────────────────────────────────────────
    # BIGSERIAL pk; SQLAlchemy renders BigInteger + autoincrement as
    # BIGSERIAL on Postgres.
    op.create_table(
        "audit_log",
        sa.Column(
            "id", sa.BigInteger,
            primary_key=True, autoincrement=True,
        ),
        sa.Column(
            "timestamp", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("actor", sa.String(256)),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("resource_type", sa.String(64)),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True)),
        sa.Column("details", postgresql.JSONB),
        sa.Column("client_ip", postgresql.INET),
    )

    # ── session_metrics ─────────────────────────────────────────
    # Float (not REAL) matches the model. FK ondelete=RESTRICT also
    # canonicalizes to model intent.
    op.create_table(
        "session_metrics",
        sa.Column(
            "id", sa.BigInteger,
            primary_key=True, autoincrement=True,
        ),
        sa.Column(
            "session_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "timestamp", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("cpu_percent", sa.Float),
        sa.Column("memory_percent", sa.Float),
        sa.Column("disk_io_read", sa.BigInteger),
        sa.Column("disk_io_write", sa.BigInteger),
        sa.Column("network_rx", sa.BigInteger),
        sa.Column("network_tx", sa.BigInteger),
        sa.Column("active_apps", postgresql.JSONB),
    )

    # ── Indexes ─────────────────────────────────────────────────
    # 13 total — all from the bottom of 001_schema.sql.
    op.create_index(
        "idx_clusters_provider_type", "clusters", ["provider_type"],
    )
    op.create_index("idx_desktops_pool", "desktops", ["pool_id"])
    op.create_index(
        "idx_desktops_assigned", "desktops", ["assigned_user"],
        postgresql_where=sa.text("assigned_user IS NOT NULL"),
    )
    op.create_index("idx_desktops_status", "desktops", ["status"])
    op.create_index("idx_desktops_vmid", "desktops", ["pve_vmid"])
    op.create_index("idx_sessions_desktop", "sessions", ["desktop_id"])
    op.create_index("idx_sessions_user", "sessions", ["username"])
    op.create_index(
        "idx_sessions_active", "sessions", ["status"],
        postgresql_where=sa.text("status IN ('connecting', 'active')"),
    )
    op.create_index("idx_entitlements_pool", "entitlements", ["pool_id"])
    op.create_index(
        "idx_entitlements_principal", "entitlements", ["principal_name"],
    )
    op.create_index("idx_audit_timestamp", "audit_log", ["timestamp"])
    op.create_index(
        "idx_audit_resource", "audit_log",
        ["resource_type", "resource_id"],
    )
    op.create_index(
        "idx_session_metrics_session", "session_metrics",
        ["session_id", "timestamp"],
    )


def downgrade() -> None:
    # Reverse-dependency order. Indexes drop automatically with their
    # tables. Invoked only by `alembic downgrade base` — a developer
    # escape hatch; production never downgrades from baseline.
    op.drop_table("session_metrics")
    op.drop_table("audit_log")
    op.drop_table("entitlements")
    op.drop_table("sessions")
    op.drop_table("desktops")
    op.drop_table("pools")
    op.drop_table("templates")
    op.drop_table("clusters")
    op.execute("DROP TYPE IF EXISTS session_status")
    op.execute("DROP TYPE IF EXISTS desktop_status")
    op.execute("DROP TYPE IF EXISTS pool_status")
    op.execute("DROP TYPE IF EXISTS pool_type")
