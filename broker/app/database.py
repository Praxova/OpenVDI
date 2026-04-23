"""Async SQLAlchemy engine + session management.

Single engine per process, session-per-request via FastAPI dependency.
Transactions commit explicitly from service methods; the dependency only
rolls back on unhandled exceptions.
"""
from __future__ import annotations

from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


engine: AsyncEngine = create_async_engine(
    get_settings().database_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=10,
)

async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Shared declarative base for every ORM model."""


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yields one session per request.

    Does NOT commit — service methods commit their own writes explicitly.
    Rolls back on unhandled exceptions; always closes in finally.
    """
    session = async_session_factory()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
