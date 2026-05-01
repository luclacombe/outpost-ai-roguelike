"""/game router tests — POST /game/session + GET /game/{id}.

All DB-touching cases call `_require_db()` so a contributor without local
Postgres gets SKIP, not failure (matching the pattern in
`test_me_endpoint.py`).

The auth stub in `tests/conftest.py` uses a fixed UUID for the test user.
We provision the matching `auth.users` + `profiles` rows once per test
in a fixture and tear them down via ON DELETE CASCADE at the auth.users
level — that also drops the profile, any sessions, and any agent rows.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.database import async_session_factory
from app.dependencies import get_current_user
from app.main import app

# Matches the stub in tests/conftest.py.
TEST_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
OTHER_USER_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a2")


async def _require_db() -> None:
    try:
        async with async_session_factory() as s:
            await s.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover — env-dependent
        pytest.skip(f"Postgres unreachable: {exc}")


async def _ensure_auth_user(user_id: uuid.UUID, email: str) -> None:
    """Insert a row in auth.users and seed the matching profile.

    Real Supabase signs users up via the Auth API which fires a trigger
    that populates `public.profiles`. In tests we short-circuit that by
    writing both rows directly. Cleanup deletes from auth.users; the
    ON DELETE CASCADE from profiles handles the rest, and the cascade
    from game_sessions.user_id handles any created sessions.
    """
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
            {"id": user_id, "name": "Test User"},
        )
        await s.commit()


async def _delete_auth_user(user_id: uuid.UUID) -> None:
    async with async_session_factory() as s:
        await s.execute(text("DELETE FROM auth.users WHERE id = :id"), {"id": user_id})
        await s.commit()


@pytest_asyncio.fixture
async def seeded_user() -> AsyncIterator[uuid.UUID]:
    """Provision auth.users + profiles for the default test user, and
    clean up via CASCADE when the test finishes."""
    await _require_db()
    await _ensure_auth_user(TEST_USER_ID, "test@outpost.local")
    try:
        yield TEST_USER_ID
    finally:
        await _delete_auth_user(TEST_USER_ID)


# ---------------------------------------------------------------------------
# POST /game/session
# ---------------------------------------------------------------------------


async def test_create_session_creates_row(client, seeded_user) -> None:
    response = await client.post("/game/session")
    assert response.status_code == 201, response.text
    body = response.json()
    game_id = body["game_id"]

    async with async_session_factory() as s:
        sessions = (
            await s.execute(
                text("SELECT COUNT(*) FROM game_sessions WHERE user_id = :u"),
                {"u": seeded_user},
            )
        ).scalar_one()
        agents = (
            await s.execute(
                text("SELECT COUNT(*) FROM agents WHERE game_id = :g"),
                {"g": game_id},
            )
        ).scalar_one()
        profile_last = (
            await s.execute(
                text("SELECT last_run_started_at, total_runs FROM profiles WHERE id = :u"),
                {"u": seeded_user},
            )
        ).one()

    assert sessions == 1
    assert agents == 4
    assert profile_last[0] is not None
    assert profile_last[1] == 1


async def test_create_session_returns_public_view(client, seeded_user) -> None:
    response = await client.post("/game/session")
    assert response.status_code == 201
    body = response.json()

    assert body["current_turn"] == 0
    assert body["legitimacy"] == 100
    assert body["player_health"] == 100
    assert isinstance(body["max_turns"], int) and 8 <= body["max_turns"] <= 12
    assert body["setting_id"].endswith("_v1")
    assert body["setting_name"]
    assert len(body["agents"]) == 4
    assert len(body["resources"]) == 5

    for agent in body["agents"]:
        # Public fields present.
        for key in (
            "id",
            "name",
            "role",
            "archetype_quadrant",
            "portrait_seed",
            "verbal_tic",
            "health",
            "morale",
            "grievance",
            "is_alive",
            "status",
        ):
            assert key in agent, f"missing public field {key!r} on agent"
        # Fresh agents are ACTIVE; status surfaces detain/dismiss to the UI.
        assert agent["status"] == "ACTIVE"
        # Private fields must NOT leak.
        for private in (
            "ethics",
            "temperament",
            "loyalty",
            "memory",
            "relationships",
            "transformation",
        ):
            assert private not in agent, f"private field {private!r} leaked"

    # Proposals and event are empty right after creation.
    assert body["turn_proposals"] == []
    assert body["current_event"] is None


async def test_create_session_enforces_daily_gate(client, seeded_user) -> None:
    first = await client.post("/game/session")
    assert first.status_code == 201

    second = await client.post("/game/session")
    assert second.status_code == 429
    body = second.json()
    assert body["detail"]["error"]["code"] == "daily_gate_exceeded"
    assert "next_run_at" in body["detail"]["error"]["detail"]


async def test_create_session_daily_gate_race_condition(seeded_user) -> None:
    """Fire two near-simultaneous POSTs via asyncio.gather. The atomic
    conditional UPDATE on profiles guarantees exactly one winner."""
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as c1,
        AsyncClient(transport=transport, base_url="http://test") as c2,
    ):
        # Both clients use the same stubbed user (single-user race).
        app.dependency_overrides[get_current_user] = lambda: {
            "sub": str(seeded_user),
            "email": "test@outpost.local",
            "aud": "authenticated",
        }
        try:
            r1, r2 = await asyncio.gather(
                c1.post("/game/session"),
                c2.post("/game/session"),
            )
        finally:
            app.dependency_overrides.pop(get_current_user, None)

    statuses = sorted([r1.status_code, r2.status_code])
    assert statuses == [201, 429], (
        f"expected one 201 + one 429, got {statuses}: r1={r1.text!r} r2={r2.text!r}"
    )
    losing = r1 if r1.status_code == 429 else r2
    assert losing.json()["detail"]["error"]["code"] == "daily_gate_exceeded"

    # Exactly one row persisted.
    async with async_session_factory() as s:
        rows = (
            await s.execute(
                text("SELECT COUNT(*) FROM game_sessions WHERE user_id = :u"),
                {"u": seeded_user},
            )
        ).scalar_one()
    assert rows == 1


async def test_create_session_requires_auth(unauth_client) -> None:
    response = await unauth_client.post("/game/session")
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# GET /game/{game_id}
# ---------------------------------------------------------------------------


async def test_get_session_returns_own(client, seeded_user) -> None:
    created = await client.post("/game/session")
    assert created.status_code == 201
    game_id = created.json()["game_id"]

    fetched = await client.get(f"/game/{game_id}")
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["game_id"] == game_id
    assert body["current_turn"] == 0
    assert len(body["agents"]) == 4


async def test_get_session_other_user_404(client, seeded_user) -> None:
    """User A creates a run. User B queries it → 404, not 403, so the
    server never leaks that the run exists."""
    created = await client.post("/game/session")
    assert created.status_code == 201
    game_id = created.json()["game_id"]

    # Provision user B and swap the auth override to them.
    await _ensure_auth_user(OTHER_USER_ID, "other@outpost.local")
    try:
        app.dependency_overrides[get_current_user] = lambda: {
            "sub": str(OTHER_USER_ID),
            "email": "other@outpost.local",
            "aud": "authenticated",
        }
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                resp = await c.get(f"/game/{game_id}")
        finally:
            # Restore the default test-user override so the fixture teardown
            # and any follow-on tests still work.
            app.dependency_overrides[get_current_user] = lambda: {
                "sub": str(TEST_USER_ID),
                "email": "test@outpost.local",
                "aud": "authenticated",
            }
    finally:
        await _delete_auth_user(OTHER_USER_ID)

    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"]["error"]["code"] == "game_not_found"


async def test_get_session_nonexistent_404(client, seeded_user) -> None:
    random_id = uuid.uuid4()
    resp = await client.get(f"/game/{random_id}")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"]["code"] == "game_not_found"


async def test_get_session_requires_auth(unauth_client) -> None:
    resp = await unauth_client.get(f"/game/{uuid.uuid4()}")
    assert resp.status_code in (401, 403)
