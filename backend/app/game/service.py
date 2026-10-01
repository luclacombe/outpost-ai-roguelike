"""Game session service layer.

Owns the two database-touching flows the /game router exposes:

  - `create_game_session` — atomic daily-gate enforcement + generator call
    + persistence of the game_sessions row, the four agents rows, and the
    profile timestamp update.
  - `get_game_session` — loads a run's state and rehydrates it into
    `ColonyState`, filtered by user_id so RLS is a backup, not primary.

The router is thin (parse → call → return); this module owns every
mutation and every query. Transactions are explicit — the router does
not manage commits.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.gate import next_run_at
from app.config import settings
from app.game import models as game_models
from app.game.exceptions import DailyGateExceeded, GameNotFound, NoSettingsAvailable
from app.game.generator import generate_run
from app.game.schemas import ColonyState
from app.game.setting_loader import list_available_settings, load_setting
from app.models import Profile


def _iso_utc(dt: datetime) -> str:
    """Normalize a UTC datetime to an ISO-8601 string ending in 'Z'."""
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _pick_setting_id(user_id: UUID, now_utc: datetime) -> str:
    """Pick the setting for this run, stable per (user, UTC day).

    Rationale: players sometimes retry after a network error or crash.
    If the first call succeeded server-side, the conditional UPDATE of
    profiles makes the retry a no-op 429. If it failed before commit,
    they should at least get the same setting on retry so the "felt
    random" experience isn't broken — keying off UTC day achieves that
    without needing any persistence of the choice.

    Also validates that the shipped settings all load; if the pool is
    empty or unreadable, raises `NoSettingsAvailable` which the router
    surfaces as 503.
    """
    candidates = list_available_settings()
    if not candidates:
        raise NoSettingsAvailable()

    # Filter to the settings that actually validate right now. We don't
    # cache this because it's a cheap YAML parse + Pydantic validate and
    # /game/session is not a hot path.
    valid: list[str] = []
    for sid in candidates:
        try:
            load_setting(sid)
            valid.append(sid)
        except Exception:  # noqa: BLE001 — defensive; any load failure
            continue
    if not valid:
        raise NoSettingsAvailable()

    # Sorted for determinism; random.Random seeded by user.int XOR UTC
    # ordinal so a user's retry within the same day picks the same setting.
    rng = random.Random(user_id.int ^ now_utc.toordinal())
    return rng.choice(sorted(valid))


async def create_game_session(
    *,
    user_id: UUID,
    db: AsyncSession,
    rng_seed_source: int | None = None,
) -> ColonyState:
    """Create a run, atomically enforcing the one-run-per-day gate.

    Race safety uses a conditional UPDATE on `profiles`::

        UPDATE profiles
           SET last_run_started_at = now(),
               total_runs = total_runs + 1
         WHERE id = :user_id
           AND (last_run_started_at IS NULL
                OR last_run_started_at < :today_midnight)

    If `rowcount == 0`, either the gate was claimed by a concurrent
    request or the profile row is missing. A follow-up SELECT
    distinguishes the two: a hit → 429 with a concrete `next_run_at`;
    a miss → 429 with tomorrow's midnight as a best-effort fallback.
    """
    now_utc = datetime.now(UTC)
    setting_id = _pick_setting_id(user_id, now_utc)

    # UTC midnight today = the cutoff below which a prior run no longer blocks.
    today_midnight = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)

    # Atomic gate + claim. `total_runs` is bumped as part of the same row
    # touch so no separate UPDATE is needed.
    claim = update(Profile).where(Profile.id == user_id)
    if not settings.DEV_BYPASS_DAILY_GATE:
        claim = claim.where(
            (Profile.last_run_started_at.is_(None)) | (Profile.last_run_started_at < today_midnight)
        )
    result = await db.execute(
        claim.values(
            last_run_started_at=now_utc,
            total_runs=Profile.total_runs + 1,
        )
    )

    if result.rowcount == 0:
        # Gate was claimed OR profile row missing. Read the current
        # last_run_started_at so we can surface a precise next_run_at.
        # Snapshot the value before rollback, otherwise accessing a
        # lazy-loaded attribute on the expired ORM instance triggers
        # an un-awaited SELECT under async SQLAlchemy.
        last = (
            await db.execute(select(Profile.last_run_started_at).where(Profile.id == user_id))
        ).scalar_one_or_none()
        await db.rollback()
        if last is not None:
            raise DailyGateExceeded(_iso_utc(next_run_at(last)))
        # Defensive path: the profile row is missing or has a NULL
        # last_run_started_at but the conditional UPDATE still matched
        # zero rows. The first case means the Supabase bootstrap trigger
        # didn't fire for this user — operator bug, propagates as 500.
        # The second case can't really happen (the WHERE clause passes
        # when last_run_started_at IS NULL), but we handle it defensively
        # by falling through to the same error so callers see a
        # consistent 500 rather than silently proceeding.
        raise RuntimeError(f"profile for {user_id} is missing or in an inconsistent state")

    # Seed derivation: callers (tests) can pin this; default is an epoch-
    # derived integer for a practically-unique stream per run.
    seed = (
        rng_seed_source
        if rng_seed_source is not None
        else int(now_utc.timestamp() * 1000) & 0x7FFFFFFF
    )

    state = generate_run(user_id=user_id, setting_id=setting_id, seed=seed)

    # Persist the run + its four agents in the same transaction as the
    # conditional UPDATE. Any failure here rolls back the profile
    # timestamp bump — the user can retry without burning their gate.
    session_row = game_models.GameSession(
        id=state.game_id,
        user_id=user_id,
        setting_id=state.setting_id,
        seed=state.seed,
        max_turns=state.max_turns,
        current_turn=state.turn_number,
        state=state.model_dump(mode="json"),
        legitimacy=state.legitimacy,
        player_health=state.player_health,
        started_at=now_utc,
    )
    db.add(session_row)
    # Flush so the agents INSERT below can satisfy its FK to game_sessions.id
    # without SQLAlchemy re-ordering via executemany batching.
    await db.flush()

    for agent in state.agents:
        agent_state = agent.model_dump(mode="json")
        # Identity/axes are mirrored as scalar columns; the rest stays in JSONB.
        for k in (
            "id",
            "game_id",
            "name",
            "role",
            "archetype_quadrant",
            "ethics",
            "temperament",
            "loyalty",
            "portrait_seed",
        ):
            agent_state.pop(k, None)
        db.add(
            game_models.Agent(
                id=agent.id,
                game_id=state.game_id,
                name=agent.name,
                role=agent.role,
                archetype_quadrant=agent.archetype_quadrant.value,
                ethics=agent.ethics,
                temperament=agent.temperament,
                loyalty=agent.loyalty,
                portrait_seed=agent.portrait_seed,
                state=agent_state,
            )
        )

    await db.commit()
    return state


async def get_game_session(
    *,
    user_id: UUID,
    game_id: UUID,
    db: AsyncSession,
) -> ColonyState:
    """Load a run's full state, filtered by user_id.

    RLS is a backup per invariant #9; app-level filtering is primary.
    Raises `GameNotFound` when no row matches — we never distinguish
    "doesn't exist" from "belongs to another user" to avoid leaking
    existence information.
    """
    stmt = (
        select(game_models.GameSession)
        .where(game_models.GameSession.id == game_id)
        .where(game_models.GameSession.user_id == user_id)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise GameNotFound()
    return ColonyState.model_validate(row.state)
