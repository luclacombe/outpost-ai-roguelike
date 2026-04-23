"""Verify the async SQLAlchemy engine can talk to Postgres.

Requires a running Postgres at DATABASE_URL (typically local Supabase via
`supabase start`). Skipped automatically if the DB is unreachable so CI and
contributors without Docker can still run the rest of the suite.
"""

import pytest
from sqlalchemy import text

from app.database import async_session_factory


async def test_database_can_connect() -> None:
    try:
        async with async_session_factory() as session:
            result = await session.execute(text("SELECT 1"))
            assert result.scalar() == 1
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"Postgres unreachable at configured DATABASE_URL: {exc}")
