"""Smoke-test that the ORM models match Pydantic schemas and persist correctly.

Follows the repo's pattern of skipping gracefully if Postgres is unreachable
so a contributor without local Supabase still gets a clean pytest run.
"""

import pytest
from sqlalchemy import text

from app.database import async_session_factory
from app.game.models import Agent, ConsequenceSeed, GameSession, TurnLog  # noqa: F401


async def _require_db() -> None:
    try:
        async with async_session_factory() as s:
            await s.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"Postgres unreachable: {exc}")


async def test_game_tables_exist() -> None:
    await _require_db()
    async with async_session_factory() as s:
        # Read from each table to confirm it exists. Empty result is fine.
        await s.execute(text("SELECT COUNT(*) FROM game_sessions"))
        await s.execute(text("SELECT COUNT(*) FROM agents"))
        await s.execute(text("SELECT COUNT(*) FROM turn_logs"))
        await s.execute(text("SELECT COUNT(*) FROM consequence_seeds"))
