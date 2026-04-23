"""GET /me router tests.

Two hit the real DB (skipped if Postgres is unreachable, following the
repo convention in `test_database.py`). One uses a dependency override to
inject a fake profile so we can exercise the "already ran today" branch
without mutating real rows.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from app.database import async_session_factory
from app.dependencies import get_db
from app.main import app
from app.models import Profile


async def _require_db() -> None:
    try:
        async with async_session_factory() as s:
            await s.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover — env-dependent
        pytest.skip(f"Postgres unreachable: {exc}")


async def test_me_returns_user_info(client):
    await _require_db()
    response = await client.get("/me")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "00000000-0000-0000-0000-000000000001"
    assert data["email"] == "test@outpost.local"
    # Fresh user (no profile row) is eligible now, so next_run_at is null.
    assert "next_run_at" in data
    assert data["next_run_at"] is None


async def test_me_requires_auth(unauth_client):
    response = await unauth_client.get("/me")
    assert response.status_code in (401, 403)


async def test_me_reports_next_run_at_when_blocked(client):
    """When the profile has a recent last_run_started_at, /me returns the
    next 00:00 UTC rollover instead of None."""
    now = datetime.now(UTC)
    fake_profile = Profile(
        id="00000000-0000-0000-0000-000000000001",
        display_name="Blocked User",
        last_run_started_at=now,
        total_runs=1,
        created_at=now - timedelta(days=3),
    )

    fake_session = AsyncMock()
    fake_session.get = AsyncMock(return_value=fake_profile)

    async def _fake_db():
        yield fake_session

    app.dependency_overrides[get_db] = _fake_db
    try:
        response = await client.get("/me")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    data = response.json()
    assert data["display_name"] == "Blocked User"
    assert data["next_run_at"] is not None
    # Next run must land at 00:00 UTC of tomorrow or later.
    parsed = datetime.fromisoformat(data["next_run_at"])
    assert parsed.tzinfo is not None
    assert parsed.hour == 0 and parsed.minute == 0
