"""Post-mortem aggregation tests.

Two layers:

* **Unit:** the helpers (`_to_one_line`, `_last_sentence`,
  `_peak_near_miss`) — pure, run anywhere.
* **DB-backed:** end-to-end — insert a completed game, four agents, a
  handful of turn_logs and seeds, call `build_post_mortem`, assert. Skips
  when local Postgres is unreachable, matching the convention in
  `test_agent_router.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.game import models as game_models
from app.game.exceptions import GameNotEnded, GameNotFound
from app.game.post_mortem import (
    _last_sentence,
    _peak_near_miss,
    _to_one_line,
    build_post_mortem,
    build_post_mortem_by_token,
    ensure_share_token,
)

# ---------------------------------------------------------------------------
# Pure-function tests
# ---------------------------------------------------------------------------


def test_to_one_line_keeps_first_sentence() -> None:
    s = "MET 04:22:17 — Unit 3 efficiency at 71%. Need timeline before rationing."
    assert _to_one_line(s) == "MET 04:22:17 — Unit 3 efficiency at 71%."


def test_to_one_line_collapses_whitespace() -> None:
    assert _to_one_line("  hello   world  ") == "hello world"


def test_to_one_line_no_terminator_returns_full_text() -> None:
    assert _to_one_line("a brief uncertain murmur") == "a brief uncertain murmur"


def test_last_sentence_picks_final_clause() -> None:
    s = "First. Second. Third one is the last."
    assert _last_sentence(s) == "Third one is the last."


def test_last_sentence_handles_trailing_quotes() -> None:
    s = 'First sentence here. "Second sentence here."'
    out = _last_sentence(s)
    assert out is not None
    assert "Second sentence" in out


def test_peak_near_miss_returns_empty_when_below_threshold() -> None:
    a, b = uuid4(), uuid4()
    history = [{a: 5, b: 5}, {a: 6, b: 6}]
    assert _peak_near_miss(history) == []


def test_peak_near_miss_returns_highest_combined() -> None:
    a, b, c = uuid4(), uuid4(), uuid4()
    history = [
        {a: 7, b: 7, c: 0},  # combined=14
        {a: 8, b: 7, c: 0},  # combined=15
        {a: 8, b: 6, c: 7},  # b dropped below threshold; a+c = 15 (tie)
    ]
    out = _peak_near_miss(history)
    assert len(out) == 1
    # Two pairs hit 15 — _peak_near_miss should surface ONE (the first seen).
    pair = {out[0].agent_a_id, out[0].agent_b_id}
    assert pair in ({a, b}, {a, c})


# ---------------------------------------------------------------------------
# DB-backed end-to-end
# ---------------------------------------------------------------------------


async def _require_db() -> None:
    try:
        async with async_session_factory() as s:
            await s.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover — env-dependent
        pytest.skip(f"Postgres unreachable: {exc}")


async def _ensure_user(s: AsyncSession, user_id: UUID, email: str) -> None:
    await s.execute(
        text(
            """
            INSERT INTO auth.users (
                id, instance_id, aud, role, email, encrypted_password,
                email_confirmed_at, created_at, updated_at
            )
            VALUES (
                :id, '00000000-0000-0000-0000-000000000000'::uuid,
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
            VALUES (:id, 'Post-Mortem Test', 0, now())
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"id": user_id},
    )


def _empty_colony_state(game_id: UUID, user_id: UUID) -> dict:
    """Minimal `state` JSONB for a game_sessions row.

    The post-mortem aggregator does not read game_sessions.state — every
    field it needs is on scalar columns or in turn_logs. We still need
    *something* parseable here to satisfy the not-null constraint.
    """
    return {
        "game_id": str(game_id),
        "user_id": str(user_id),
        "setting_id": "arctic_station_v1",
        "seed": 1,
        "turn_number": 2,
        "max_turns": 2,
        "resources": {},
        "agents": [],
        "legitimacy": 80,
        "player_health": 100,
        "consequence_seeds": [],
        "event_log": [],
        "current_event": None,
        "turn_proposals": [],
        "turn_tool_calls": [],
    }


def _state_snapshot(legitimacy: int, agents_grievance: dict[UUID, int]) -> dict:
    """Snapshot shape that turn_logs.state_snapshot stores."""
    return {
        "legitimacy": legitimacy,
        "player_health": 100,
        "resources": {
            "fuel": {"id": "fuel", "value": 50, "max": 100, "icon": "▲"},
        },
        "agents": [
            {
                "id": str(aid),
                "is_alive": True,
                "grievance": g,
                "transformation": None,
            }
            for aid, g in agents_grievance.items()
        ],
    }


async def test_build_post_mortem_aggregates_completed_run() -> None:
    """A two-turn ENDURED run with one fired seed and one dormant seed."""
    await _require_db()

    user_id = UUID("00000000-0000-0000-0000-0000000000aa")
    game_id = uuid4()
    agent_a = uuid4()
    agent_b = uuid4()
    seed_fired_id = uuid4()
    seed_dormant_id = uuid4()
    started = datetime.now(UTC) - timedelta(minutes=20)
    ended = datetime.now(UTC)

    async with async_session_factory() as s:
        await _ensure_user(s, user_id, "post-mortem-test@outpost.local")

        s.add(
            game_models.GameSession(
                id=game_id,
                user_id=user_id,
                setting_id="arctic_station_v1",
                seed=42,
                max_turns=2,
                current_turn=2,
                state=_empty_colony_state(game_id, user_id),
                legitimacy=78,
                player_health=100,
                end_state="ENDURED",
                cost_cents=11,
                share_token=None,
                started_at=started,
                ended_at=ended,
            )
        )
        await s.flush()

        for aid, name, role, quad in (
            (agent_a, "Nadia", "Quartermaster", "ACCOUNTANT"),
            (agent_b, "Marko", "Commissar", "OPERATOR"),
        ):
            s.add(
                game_models.Agent(
                    id=aid,
                    game_id=game_id,
                    name=name,
                    role=role,
                    archetype_quadrant=quad,
                    ethics=0.0,
                    temperament=0.0,
                    loyalty=0.0,
                    portrait_seed=f"seed:{name}",
                    state={
                        "health": 100,
                        "morale": 60,
                        "grievance": 0,
                        "is_alive": True,
                        "memory": [],
                        "relationships": {},
                        "transformation": None,
                    },
                )
            )

        # Turn 0: natural event, one short narration.
        s.add(
            game_models.TurnLog(
                game_id=game_id,
                turn_number=0,
                event={
                    "id": "evt-natural",
                    "title": "Heating core leak",
                    "body": "core leaks fast.",
                    "category": "infrastructure",
                    "severity": 4,
                    "origin": "NATURAL",
                    "origin_seed_id": None,
                },
                proposals=[
                    {
                        "id": "p1",
                        "name": "Full power",
                        "description": "Burn fuel hot.",
                        "flavor": "DOMINANT",
                        "proposed_by_agent_id": str(agent_b),
                        "effects_preview": ["fuel −12"],
                    }
                ],
                chosen={"chosen_proposal_id": "p1"},
                agent_responses=[
                    {
                        "agent_id": str(agent_a),
                        "text": "We can't afford this. The reserves are too thin.",
                    },
                    {
                        "agent_id": str(agent_b),
                        "text": "Burn it. Patch in three hours.",
                    },
                ],
                autonomous_actions=None,
                narration="The crew worked. By dawn the patch held.",
                state_snapshot=_state_snapshot(85, {agent_a: 7, agent_b: 7}),
                turn_cost_cents=5,
            )
        )
        # Turn 1: seed-fired event.
        s.add(
            game_models.TurnLog(
                game_id=game_id,
                turn_number=1,
                event={
                    "id": "evt-seed",
                    "title": "Aftermath of leak",
                    "body": "consequences come due.",
                    "category": "infrastructure",
                    "severity": 3,
                    "origin": "SEED_FIRED",
                    "origin_seed_id": str(seed_fired_id),
                },
                proposals=[
                    {
                        "id": "p2",
                        "name": "Vent the lab",
                        "description": "Save the core, lose the samples.",
                        "flavor": "MORAL",
                        "proposed_by_agent_id": str(agent_a),
                        "effects_preview": ["morale −4"],
                    }
                ],
                chosen={"chosen_proposal_id": "p2"},
                agent_responses=[
                    {"agent_id": str(agent_a), "text": "Vent it."},
                    {"agent_id": str(agent_b), "text": "Agreed."},
                ],
                autonomous_actions=None,
                narration="They closed the bulkheads. The samples froze. Nobody spoke at breakfast.",
                state_snapshot=_state_snapshot(78, {agent_a: 5, agent_b: 5}),
                turn_cost_cents=6,
            )
        )

        # Two seeds: one fired (matches turn 1), one dormant (never fired).
        s.add(
            game_models.ConsequenceSeed(
                id=seed_fired_id,
                game_id=game_id,
                written_at_turn=0,
                fires_at_turn=1,
                foreshadowed=True,
                foreshadow_turn=0,
                payload={"title": "Aftermath of leak", "category": "infrastructure"},
                origin_decision={"event_title": "Heating core leak", "proposal_id": "p1"},
                fired=True,
                dormant=False,
            )
        )
        s.add(
            game_models.ConsequenceSeed(
                id=seed_dormant_id,
                game_id=game_id,
                written_at_turn=0,
                fires_at_turn=5,
                foreshadowed=False,
                foreshadow_turn=None,
                payload={"title": "Marko remembers", "category": "social"},
                origin_decision={"event_title": "Heating core leak", "proposal_id": "p1"},
                fired=False,
                dormant=True,
            )
        )
        await s.commit()

    # Run aggregator
    async with async_session_factory() as s:
        post_mortem = await build_post_mortem(game_id=game_id, db=s)

    assert post_mortem.game_id == game_id
    assert post_mortem.end_state.value == "ENDURED"
    assert post_mortem.total_turns == 2
    assert post_mortem.cost_cents == 11
    # Final grievance reads from the LAST turn's snapshot.
    final_g = {a.id: a.final_grievance for a in post_mortem.agents}
    assert final_g[agent_a] == 5
    assert final_g[agent_b] == 5
    # Final status surfaces detain/dismiss to the post-mortem timeline.
    # No status mutations happened in this fixture, so both stay ACTIVE.
    final_status = {a.id: a.final_status for a in post_mortem.agents}
    assert final_status[agent_a] == "ACTIVE"
    assert final_status[agent_b] == "ACTIVE"

    # Turn 0: advisor lines collapsed to single sentence.
    line_a_t0 = next(
        line for line in post_mortem.turns[0].advisor_lines if line.agent_id == agent_a
    )
    assert line_a_t0.text == "We can't afford this."

    # Turn 1: SEED_FIRED event surfaces in seeds_fired list.
    seeds_fired_t1 = post_mortem.turns[1].seeds_fired
    assert any(s.id == seed_fired_id for s in seeds_fired_t1), seeds_fired_t1

    # Turn 0: seed_written_at_turn=0 so both seeds appear in seeds_written for turn 0.
    written_t0 = {s.id for s in post_mortem.turns[0].seeds_written}
    assert seed_fired_id in written_t0
    assert seed_dormant_id in written_t0

    # Counterfactuals: dormant seed surfaces; near-miss coup detected on turn 0.
    cf = post_mortem.counterfactuals
    dormant_ids = {s.id for s in cf.dormant_seeds}
    assert seed_dormant_id in dormant_ids
    # Both agents hit grievance 7 simultaneously on turn 0 → one near-miss.
    assert len(cf.near_miss_coups) == 1
    near = cf.near_miss_coups[0]
    assert {near.agent_a_id, near.agent_b_id} == {agent_a, agent_b}
    assert near.grievance_a == 7
    assert near.grievance_b == 7

    # Opening / closing lines.
    assert post_mortem.opening_line == "The crew worked."
    assert post_mortem.closing_line is not None
    assert "breakfast" in post_mortem.closing_line


async def test_build_post_mortem_raises_on_missing_run() -> None:
    await _require_db()
    async with async_session_factory() as s:
        with pytest.raises(GameNotFound):
            await build_post_mortem(game_id=uuid4(), db=s)


async def test_ensure_share_token_idempotent_and_resolves_back() -> None:
    """First call mints, second call returns the same token; build_by_token works."""
    await _require_db()

    user_id = UUID("00000000-0000-0000-0000-0000000000ad")
    game_id = uuid4()
    async with async_session_factory() as s:
        await _ensure_user(s, user_id, "share-token-test@outpost.local")
        s.add(
            game_models.GameSession(
                id=game_id,
                user_id=user_id,
                setting_id="arctic_station_v1",
                seed=42,
                max_turns=1,
                current_turn=1,
                state=_empty_colony_state(game_id, user_id),
                legitimacy=50,
                player_health=100,
                end_state="ENDURED",
                cost_cents=1,
                share_token=None,
            )
        )
        s.add(
            game_models.TurnLog(
                game_id=game_id,
                turn_number=0,
                event={
                    "id": "evt",
                    "title": "Quiet",
                    "body": "",
                    "category": "respite",
                    "severity": 1,
                    "origin": "NATURAL",
                    "origin_seed_id": None,
                },
                proposals=[],
                chosen={"chosen_proposal_id": ""},
                agent_responses=[],
                autonomous_actions=None,
                narration="A quiet day.",
                state_snapshot=_state_snapshot(50, {}),
                turn_cost_cents=1,
            )
        )
        await s.commit()

    async with async_session_factory() as s:
        token1 = await ensure_share_token(game_id=game_id, db=s)
        token2 = await ensure_share_token(game_id=game_id, db=s)
        await s.commit()

    assert token1 == token2
    assert len(token1) >= 8  # token_urlsafe(8) → 11 chars

    async with async_session_factory() as s:
        pm = await build_post_mortem_by_token(token=token1, db=s)
    assert pm.game_id == game_id


async def test_build_post_mortem_by_token_raises_for_unknown_token() -> None:
    await _require_db()
    async with async_session_factory() as s:
        with pytest.raises(GameNotFound):
            await build_post_mortem_by_token(token="not-a-real-token", db=s)


async def test_build_post_mortem_raises_when_run_still_active() -> None:
    await _require_db()

    user_id = UUID("00000000-0000-0000-0000-0000000000ab")
    game_id = uuid4()
    async with async_session_factory() as s:
        await _ensure_user(s, user_id, "active-run-test@outpost.local")
        s.add(
            game_models.GameSession(
                id=game_id,
                user_id=user_id,
                setting_id="arctic_station_v1",
                seed=42,
                max_turns=10,
                current_turn=3,
                state=_empty_colony_state(game_id, user_id),
                legitimacy=70,
                player_health=100,
                end_state=None,  # still running
                cost_cents=2,
            )
        )
        await s.commit()

    async with async_session_factory() as s:
        with pytest.raises(GameNotEnded):
            await build_post_mortem(game_id=game_id, db=s)
