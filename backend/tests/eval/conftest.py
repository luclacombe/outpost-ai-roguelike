"""Fixtures for eval-pipeline tests.

Mirrors `tests/integration/conftest.py` — pytest fixture discovery walks
parent conftests but doesn't reach into sibling test files, so the
graph_postgres + user-seeding helpers are duplicated here. The unit-test
user UUID is distinct from the integration user so parallel runs don't
collide on the daily-run gate.
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

EVAL_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000077")
EVAL_USER_EMAIL = "eval@outpost.local"


async def _require_db() -> None:
    try:
        async with async_session_factory() as s:
            await s.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover — env-dependent
        pytest.skip(f"Postgres unreachable: {exc}")


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
            {"id": user_id, "name": "Eval Test User"},
        )
        await s.commit()


async def _delete_auth_user(user_id: uuid.UUID) -> None:
    async with async_session_factory() as s:
        await s.execute(text("DELETE FROM auth.users WHERE id = :id"), {"id": user_id})
        await s.commit()


def _dsn() -> str:
    return _sqlalchemy_to_psycopg_url(settings.DATABASE_URL)


@pytest_asyncio.fixture
async def eval_user_id() -> AsyncIterator[uuid.UUID]:
    await _require_db()
    await _ensure_auth_user(EVAL_USER_ID, EVAL_USER_EMAIL)
    try:
        yield EVAL_USER_ID
    finally:
        await _delete_auth_user(EVAL_USER_ID)


@pytest_asyncio.fixture
async def graph_postgres() -> AsyncIterator[Any]:
    """Postgres-checkpointer-backed graph for the eval driver.

    The driver must use a Postgres checkpointer because /turn pauses and
    /decide resumes; only the Postgres saver persists across the two HTTP
    calls.
    """
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
