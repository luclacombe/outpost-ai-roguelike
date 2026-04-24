"""Fixtures for integration tests.

Copied inline from tests/test_agent_router.py rather than imported from that
module.  Pytest fixture discovery from tests/integration/ finds fixtures in
tests/conftest.py automatically (it is a parent conftest), but fixtures
defined inside test_agent_router.py are not visible here.  This file promotes
the minimum needed set so tests/integration/ tests can stand alone.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import text

from app.agent.checkpointer import _sqlalchemy_to_psycopg_url
from app.agent.graph import build_graph
from app.config import settings
from app.database import async_session_factory
from app.main import app

# ---------------------------------------------------------------------------
# Shared test identity — matches the stub in tests/conftest.py
# ---------------------------------------------------------------------------

INTEGRATION_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000099")
INTEGRATION_USER_EMAIL = "integration@outpost.local"


# ---------------------------------------------------------------------------
# DB reachability guard (same pattern as test_agent_router.py / test_game_router.py)
# ---------------------------------------------------------------------------


async def _require_db() -> None:
    try:
        async with async_session_factory() as s:
            await s.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover — env-dependent
        pytest.skip(f"Postgres unreachable: {exc}")


# ---------------------------------------------------------------------------
# Auth/profile seeding helpers
# ---------------------------------------------------------------------------


async def _ensure_auth_user(user_id: uuid.UUID, email: str) -> None:
    async with async_session_factory() as s:
        await s.execute(
            text(
                """
                INSERT INTO auth.users (
                    id, instance_id, aud, role, email, encrypted_password,
                    email_confirmed_at, created_at, updated_at
                )
                VALUES (
                    :id,
                    '00000000-0000-0000-0000-000000000000'::uuid,
                    'authenticated', 'authenticated', :email,
                    '', now(), now(), now()
                )
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {"id": user_id, "email": email},
        )
        await s.execute(
            text(
                """
                INSERT INTO profiles (id, display_name, total_runs, created_at)
                VALUES (:id, :name, 0, now())
                ON CONFLICT (id) DO UPDATE SET last_run_started_at = NULL, total_runs = 0
                """
            ),
            {"id": user_id, "name": "Integration Test User"},
        )
        await s.commit()


async def _delete_auth_user(user_id: uuid.UUID) -> None:
    async with async_session_factory() as s:
        await s.execute(text("DELETE FROM auth.users WHERE id = :id"), {"id": user_id})
        await s.commit()


def _dsn() -> str:
    return _sqlalchemy_to_psycopg_url(settings.DATABASE_URL)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def full_run_user_id() -> AsyncIterator[uuid.UUID]:
    """Provision auth.users + profiles for the integration test user.

    Uses a distinct UUID from the unit-test user so parallel runs of the
    default suite + integration suite don't collide.
    """
    await _require_db()
    await _ensure_auth_user(INTEGRATION_USER_ID, INTEGRATION_USER_EMAIL)
    try:
        yield INTEGRATION_USER_ID
    finally:
        await _delete_auth_user(INTEGRATION_USER_ID)


@pytest_asyncio.fixture
async def graph_postgres() -> AsyncIterator[Any]:
    """Postgres-checkpointer-backed graph.  Mirrors test_agent_router.py."""
    await _require_db()
    async with AsyncPostgresSaver.from_conn_string(_dsn()) as saver:
        await saver.setup()
        compiled = build_graph(saver)
        prior = getattr(app.state, "graph", None)
        app.state.graph = compiled
        try:
            yield compiled
        finally:
            if prior is None:
                if hasattr(app.state, "graph"):
                    delattr(app.state, "graph")
            else:
                app.state.graph = prior
