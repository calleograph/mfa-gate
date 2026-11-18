from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .config import settings


@lru_cache
def engine() -> AsyncEngine:
    return create_async_engine(settings().database_url, pool_pre_ping=True, pool_size=10, max_overflow=10)


@lru_cache
def sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with sessionmaker()() as session:
        yield session


def raw_dsn() -> str:
    """DSN для asyncpg напрямую (LISTEN/NOTIFY)."""
    return settings().database_url.replace("postgresql+asyncpg://", "postgresql://")
