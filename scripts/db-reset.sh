#!/usr/bin/env bash
# Drop, reapply schema, reseed. Idempotent. Used by the M2 acceptance
# test and (later) CI. No arguments; reads connection info from the
# same env vars the broker uses.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DB_DIR="$REPO_ROOT/db"

: "${OPENVDI_DB_HOST:?OPENVDI_DB_HOST is required}"
: "${OPENVDI_DB_PORT:?OPENVDI_DB_PORT is required}"
: "${OPENVDI_DB_NAME:?OPENVDI_DB_NAME is required}"
: "${OPENVDI_DB_USER:?OPENVDI_DB_USER is required}"
: "${PGPASSWORD:?PGPASSWORD is required (matches docker-compose Postgres)}"

PSQL=(psql -v ON_ERROR_STOP=1
      -h "$OPENVDI_DB_HOST"
      -p "$OPENVDI_DB_PORT"
      -U "$OPENVDI_DB_USER"
      -d "$OPENVDI_DB_NAME")

echo "==> dropping all objects"
"${PSQL[@]}" -f "$DB_DIR/drop_all.sql"

echo "==> applying schema"
"${PSQL[@]}" -f "$DB_DIR/001_schema.sql"

echo "==> applying migrations"
# 003 adds desktops.pve_task_kind. Idempotent (IF NOT EXISTS) so running
# it against a fresh 001_schema.sql install is a no-op.
"${PSQL[@]}" -f "$DB_DIR/003_add_task_kind.sql"

# 004 adds 'deleting' to pool_status. Idempotent (ADD VALUE IF NOT
# EXISTS); a fresh 001_schema.sql install already carries the value.
"${PSQL[@]}" -f "$DB_DIR/004_add_pool_status_deleting.sql"

# 005 cleans up sessions rows whose JSONB columns were written as JSON
# literal null instead of SQL NULL by pre-fix code (M2-09-fix). No-op
# on fresh installs; UPDATE has a tight WHERE so safe to re-run.
"${PSQL[@]}" -f "$DB_DIR/005_cleanup_connection_info_json_null.sql"

# 006 makes sessions.desktop_id nullable with ON DELETE SET NULL so
# destroying a desktop nulls the FK on its session rows rather than
# blocking the parent delete (M2-15-fix-2). Idempotent via DO-block
# guards on pg_constraint + information_schema; no-op on fresh installs.
"${PSQL[@]}" -f "$DB_DIR/006_sessions_desktop_id_set_null.sql"

# 007 aligns entitlements.pool_id FK with the Entitlement model's
# ondelete=CASCADE declaration so pool delete cascades through
# entitlements (M2-15-fix-3). Idempotent via pg_constraint.confdeltype
# guard; no-op on fresh installs.
"${PSQL[@]}" -f "$DB_DIR/007_entitlements_pool_id_cascade.sql"

echo "==> seeding"
"${PSQL[@]}" -f "$DB_DIR/002_seed_data.sql"

echo "==> done"
