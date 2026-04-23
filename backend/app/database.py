"""Async SQLAlchemy engine + session factory.

One engine per process. `async_session_factory` yields short-lived sessions;
callers (dependencies, scripts) are responsible for commit/rollback via
`async with`. Expiry on commit is disabled so ORM objects remain usable after
the session closes — game state is serialized to JSON on the way out.

Connection pooling: `pool_pre_ping=True` catches stale connections after
Supavisor or network hiccups. `pool_size=5` matches a small Railway Hobby
instance; raise if we see pool exhaustion under load.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    echo=(settings.LOG_LEVEL.lower() == "debug"),
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)
