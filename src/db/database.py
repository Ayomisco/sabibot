"""Async database engine and session management."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from src.config import settings
from src.utils.logger import get_logger

log = get_logger("database")

_engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
)

async_session = sessionmaker(
    _engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    """Create all tables. Safe to call multiple times."""
    # Import models so SQLModel registers them
    import src.db.models  # noqa: F401

    async with _engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    log.info("database_initialized", url=settings.database_url.split("@")[-1])


async def get_session() -> AsyncSession:
    """Yield a database session."""
    async with async_session() as session:
        return session
