#!/usr/bin/env bash
# Drop, reapply schema, reseed. Idempotent. Used by the M2 acceptance
# test and (later) CI. No arguments; reads connection info from the
# same env vars the broker uses.
#
# Two env-var sets are consumed:
#   - OPENVDI_DB_* + PGPASSWORD: drive the psql calls below (the drop
#     and the seed).
#   - POSTGRES_* (read by app.config via the repo-root .env file):
#     drive `alembic upgrade head` for schema apply.
# Both sets must point at the same database. M4-02 unifies the broker's
# config layer; this script will follow suit at that point.

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

echo "==> applying schema (alembic upgrade head)"
(cd "$REPO_ROOT/broker" && alembic upgrade head)

echo "==> seeding"
"${PSQL[@]}" -f "$DB_DIR/002_seed_data.sql"

echo "==> done"
