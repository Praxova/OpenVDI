"""Alembic environment for OpenVDI broker.

Async-engine-aware: the broker uses asyncpg via SQLAlchemy's async
extension, so the migration runner has to bridge between Alembic's
sync API and our async engine. Pattern:
  online → asyncio.run(run_async_migrations()) → async with engine
           → connection.run_sync(do_run_migrations) → Alembic ops
  offline → emit SQL using a synthetic URL (no engine needed)

The DSN is sourced from the broker's settings — one source of truth
for prod, dev, and test.
"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Importing the Base + every model module so target_metadata is fully
# populated for autogenerate. Importing the package alone runs the
# package __init__.py — which currently imports every submodule — but
# we list each module explicitly here so env.py is self-evident and a
# future drift in app/models/__init__.py doesn't silently leave tables
# out of metadata.
from app.config import get_settings
from app.database import Base
from app.models import (  # noqa: F401  -- import-for-side-effect
    audit,
    auth_token,
    cluster,
    desktop,
    entitlement,
    pool as pool_model,
    session,
    session_metrics,
    template,
)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    """Emit SQL to stdout/file without connecting.

    Used by `alembic upgrade head --sql > out.sql` for code review
    or staged production deploys. The URL is still consulted to pick
    the dialect, but no connection is made.
    """
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Online migrations against the broker's asyncpg DSN."""
    config.set_main_option("sqlalchemy.url", get_url())
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
