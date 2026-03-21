"""Async database engine and session management."""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from src.config import settings
from src.utils.logger import get_logger

log = get_logger("database")


def _adapt_url(raw: str) -> tuple[str, dict]:
    """Convert a database URL to the correct async driver format.

    - sqlite:// → sqlite+aiosqlite://  (no extra connect_args)
    - postgresql:// / postgres:// → postgresql+asyncpg://
      strips libpq-only params (sslmode, channel_binding) and
      returns ssl=True connect_args when sslmode=require/verify-*.
    """
    if raw.startswith("sqlite"):
        if "+aiosqlite" not in raw:
            raw = raw.replace("sqlite://", "sqlite+aiosqlite://", 1)
        return raw, {}

    parsed = urlparse(raw)
    params = parse_qs(parsed.query, keep_blank_values=True)

    sslmode = params.pop("sslmode", ["disable"])[0]
    params.pop("channel_binding", None)  # asyncpg doesn't understand this

    new_query = urlencode({k: v[0] for k, v in params.items()})
    scheme = "postgresql+asyncpg"
    new_url = urlunparse((
        scheme, parsed.netloc, parsed.path,
        parsed.params, new_query, parsed.fragment,
    ))

    connect_args: dict = {}
    if sslmode in ("require", "verify-ca", "verify-full"):
        connect_args["ssl"] = "require"

    return new_url, connect_args


_url, _connect_args = _adapt_url(settings.database_url)
_engine = create_async_engine(
    _url,
    echo=False,
    future=True,
    pool_pre_ping=True,   # detect stale connections (important for Neon serverless)
    connect_args=_connect_args,
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
