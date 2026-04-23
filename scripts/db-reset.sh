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

echo "==> seeding"
"${PSQL[@]}" -f "$DB_DIR/002_seed_data.sql"

echo "==> done"
