"""Database fixtures for service- and worker-level integration tests.

Each test gets a real AsyncSession bound to a real connection; all
work is enclosed in a top-level transaction that's rolled back at
test end. Tests can therefore call session.commit() inside the
function under test (the production code path) and the savepoint
nesting handles isolation correctly.

Requires a running Postgres at the broker's configured DSN with the
schema at head (alembic upgrade head). The fixture does NOT manage
schema setup — it expects the caller's environment to be correct.
"""
from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import get_settings


@pytest_asyncio.fixture
async def db_session():
    """Yield a transactional AsyncSession. Rolled back at teardown.

    Per SQLAlchemy's "Joining a Session into an External Transaction"
    pattern: open a connection, BEGIN, attach a Session that uses
    SAVEPOINTs for inner commit() calls. At fixture teardown,
    ROLLBACK the outer transaction — every change made by the test
    is gone.
    """
    engine = create_async_engine(get_settings().database_url)
    async with engine.connect() as connection:
        outer = await connection.begin()
        # join_transaction_mode="create_savepoint" tells SQLAlchemy
        # to start a SAVEPOINT every time the application calls
        # session.commit(); the outer transaction stays open and
        # gets rolled back at teardown, undoing all savepoints.
        async_session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield async_session
        finally:
            await async_session.close()
            if outer.is_active:
                await outer.rollback()
    await engine.dispose()
