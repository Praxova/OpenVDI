# OpenVDI Broker

The FastAPI broker that owns OpenVDI's data plane.

## Quick start

```bash
cd broker
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Database migrations (Alembic)

OpenVDI uses Alembic for schema migrations. The active path is:

```bash
# Fresh install (empty database):
alembic upgrade head

# Check current revision:
alembic current

# Generate a new revision after changing models:
alembic revision --autogenerate -m "describe the change"

# Roll forward one step:
alembic upgrade +1

# Nuclear development reset (destroys all data):
psql -f ../db/drop_all.sql
alembic upgrade head
```

The raw-SQL files in `db/` (001-007) are historical artifacts from
M2/M3. They are not used by the broker or by current tooling. The
baseline migration `alembic/versions/0001_baseline_m3.py` consolidates
their post-fix state into one revision.

### Onboarding an existing development database

If your Postgres already has the schema from `psql -f db/001_schema.sql`
plus the 003-007 patches (the M2/M3 path), tell Alembic the schema is
already at baseline:

```bash
alembic stamp 0001_baseline_m3
```

This adds an `alembic_version` row marking the database as having the
baseline migration applied without re-running it. From there, `alembic
upgrade head` advances through any subsequent revisions (M4-03's
`auth_tokens` migration, etc.) normally.

## Production migrations

See `docs/deploy.md` → *Database Migrations* for production deploy
procedures. Short version: `alembic upgrade head` is idempotent and
safe to run on every deploy.

## Running the broker

```bash
# from broker/
uvicorn app.main:app --host 127.0.0.1 --port 8080
```

See the project root `.env.example` for the env vars the broker reads.
