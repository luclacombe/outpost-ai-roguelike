"""Tests for app.eval.coherence (decision coherence Haiku-as-judge metric).

The Anthropic client is never hit. Tests inject a stub client per call;
never monkeypatch the real `app.agent.anthropic_client` module.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.eval.coherence import (
    COHERENCE_THRESHOLD_PER_TURN,
    INCOHERENT_TURNS_BUDGET,
    score_run_coherence,
    score_turn_coherence,
)
from app.eval.types import RunCoherenceInput, TurnJudgeInput
from app.game.schemas import Agent, Quadrant

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _agent(**over) -> Agent:
    base = {
        "id": uuid4(),
        "game_id": uuid4(),
        "name": "Vera",
        "role": "Quartermaster",
        "archetype_quadrant": Quadrant.ACCOUNTANT,
        "ethics": -0.6,
        "temperament": -0.3,
        "loyalty": 0.4,
        "portrait_seed": "vera-seed",
        "health": 100,
        "morale": 60,
        "grievance": 2,
        "is_alive": True,
        "verbal_tic": "calculates the margin",
    }
    base.update(over)
    return Agent(**base)


def _fake_response(text: str) -> SimpleNamespace:
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


def _fake_client(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        messages=SimpleNamespace(create=AsyncMock(return_value=_fake_response(text)))
    )


# ---------------------------------------------------------------------------
# score_turn_coherence
# ---------------------------------------------------------------------------


async def test_score_turn_returns_observation():
    agent = _agent()
    client = _fake_client('{"score": 1.0, "reasoning": "spot on"}')

    obs = await score_turn_coherence(
        agent=agent,
        reasoning="conserve fuel; the math doesn't work otherwise",
        action="propose_action(ration_fuel)",
        turn_number=3,
        anthropic_client=client,
    )

    assert obs.agent_id == agent.id
    assert obs.turn_number == 3
    assert obs.score == 1.0
    assert obs.rationale == "spot on"
    assert obs.reasoning == "conserve fuel; the math doesn't work otherwise"
    assert obs.action == "propose_action(ration_fuel)"
    client.messages.create.assert_awaited_once()


async def test_score_turn_snaps_score_to_rubric():
    """Snap rule: round to nearest of {0.0, 0.5, 1.0}.
    0.7 -> 0.5 (distance 0.2 vs 0.3 to 1.0).
    0.3 -> 0.5 (distance 0.2 vs 0.3 to 0.0).
    0.85 -> 1.0 (distance 0.15 vs 0.35 to 0.5).
    """
    agent = _agent()

    obs = await score_turn_coherence(
        agent=agent,
        reasoning="r",
        action="a",
        turn_number=1,
        anthropic_client=_fake_client('{"score": 0.7, "reasoning": "ok"}'),
    )
    assert obs.score == 0.5

    obs = await score_turn_coherence(
        agent=agent,
        reasoning="r",
        action="a",
        turn_number=1,
        anthropic_client=_fake_client('{"score": 0.3, "reasoning": "ok"}'),
    )
    assert obs.score == 0.5

    obs = await score_turn_coherence(
        agent=agent,
        reasoning="r",
        action="a",
        turn_number=1,
        anthropic_client=_fake_client('{"score": 0.85, "reasoning": "ok"}'),
    )
    assert obs.score == 1.0


async def test_score_turn_handles_malformed_json_returns_half():
    agent = _agent()
    client = _fake_client("not-json-at-all <thinking>...</thinking>")

    obs = await score_turn_coherence(
        agent=agent,
        reasoning="r",
        action="a",
        turn_number=2,
        anthropic_client=client,
    )

    assert obs.score == 0.5
    assert obs.rationale.startswith("judge response unparseable:")


async def test_score_turn_clamps_out_of_range():
    agent = _agent()

    obs_high = await score_turn_coherence(
        agent=agent,
        reasoning="r",
        action="a",
        turn_number=1,
        anthropic_client=_fake_client('{"score": 1.4, "reasoning": "wild"}'),
    )
    assert obs_high.score == 1.0

    obs_low = await score_turn_coherence(
        agent=agent,
        reasoning="r",
        action="a",
        turn_number=1,
        anthropic_client=_fake_client('{"score": -0.1, "reasoning": "wild"}'),
    )
    assert obs_low.score == 0.0


# ---------------------------------------------------------------------------
# score_run_coherence
# ---------------------------------------------------------------------------


def _build_run(
    scores_per_turn: list[tuple[UUID, int, float]],
) -> tuple[RunCoherenceInput, dict[tuple[UUID, int], float]]:
    """Build a RunCoherenceInput with synthetic turns and a score lookup."""
    agents_by_id: dict[UUID, Agent] = {}
    turns: list[TurnJudgeInput] = []
    score_map: dict[tuple[UUID, int], float] = {}
    for agent_id, turn_number, score in scores_per_turn:
        if agent_id not in agents_by_id:
            agents_by_id[agent_id] = _agent(id=agent_id)
        turns.append(
            TurnJudgeInput(
                agent_id=agent_id,
                turn_number=turn_number,
                reasoning=f"reason for {turn_number}",
                action=f"action {turn_number}",
            )
        )
        score_map[(agent_id, turn_number)] = score
    return RunCoherenceInput(agents_by_id=agents_by_id, turns=turns), score_map


def _scripted_client(score_map: dict[tuple[UUID, int], float]) -> SimpleNamespace:
    """Stub client that picks a score by parsing the user message for a turn label."""

    async def _create(*, model, max_tokens, system, messages):  # noqa: ARG001
        # The judge user message embeds turn number; extract it.
        user = messages[0]["content"]
        # We embedded "reason for {n}" — pull the int back out.
        # Simpler: walk all keys and find the one whose turn_number/agent_id
        # markers match. We just look for "action N" in the rendered prompt.
        for (agent_id, turn_number), score in score_map.items():
            if f"action {turn_number}" in user and str(agent_id) in user:
                return _fake_response(f'{{"score": {score}, "reasoning": "scripted"}}')
        return _fake_response('{"score": 0.5, "reasoning": "fallback"}')

    return SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(side_effect=_create)))


async def test_score_run_aggregates_mean():
    a1 = uuid4()
    a2 = uuid4()
    # 3 turns × 2 agents = 6 obs. Scores chosen to land on {0, 0.5, 1.0}
    # so snap is a no-op and the assertion is exact.
    scores = [
        (a1, 1, 1.0),
        (a1, 2, 0.5),
        (a1, 3, 1.0),
        (a2, 1, 0.0),
        (a2, 2, 1.0),
        (a2, 3, 0.5),
    ]
    run, score_map = _build_run(scores)
    client = _scripted_client(score_map)

    result = await score_run_coherence(run, anthropic_client=client)

    assert len(result.observations) == 6
    assert result.mean_score == pytest.approx(4.0 / 6.0)
    # Incoherent (per spec): turns where ANY agent scored < threshold (0.6).
    # Turn 1: a2=0.0 incoherent. Turn 2: a1=0.5 incoherent. Turn 3: a2=0.5 incoherent.
    # All 3 turns flagged.
    assert result.incoherent_turns == 3
    assert result.flagged is True


async def test_score_run_flags_when_over_budget():
    a1 = uuid4()
    scores = [(a1, t, 0.5) for t in range(1, 4)]  # 3 turns, all 0.5 (< 0.6)
    run, score_map = _build_run(scores)
    client = _scripted_client(score_map)

    result = await score_run_coherence(run, anthropic_client=client)

    # 3 incoherent turns > budget (2)
    assert result.incoherent_turns == 3
    assert result.flagged is True
    assert result.mean_score == pytest.approx(0.5)
    # Sanity-check budget constant remains what spec calls for.
    assert INCOHERENT_TURNS_BUDGET == 2
    assert COHERENCE_THRESHOLD_PER_TURN == 0.6


async def test_score_run_concurrency_cap():
    """Cap = 4. Track in-flight call count; assert max ≤ 4."""
    a1 = uuid4()
    scores = [(a1, t, 1.0) for t in range(1, 21)]  # 20 turns
    run, score_map = _build_run(scores)

    in_flight = 0
    max_in_flight = 0
    lock = asyncio.Lock()

    async def _create(*, model, max_tokens, system, messages):  # noqa: ARG001
        nonlocal in_flight, max_in_flight
        async with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        # Force scheduler to interleave coroutines.
        await asyncio.sleep(0.01)
        async with lock:
            in_flight -= 1
        return _fake_response('{"score": 1.0, "reasoning": "ok"}')

    client = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(side_effect=_create)))

    result = await score_run_coherence(run, anthropic_client=client)

    assert len(result.observations) == 20
    assert max_in_flight <= 4, f"saw {max_in_flight} concurrent calls; cap is 4"


async def test_score_run_does_not_swallow_real_errors():
    a1 = uuid4()
    scores = [(a1, 1, 1.0)]
    run, _ = _build_run(scores)

    class FakeRateLimitError(Exception):
        pass

    async def _create(**_kwargs):
        raise FakeRateLimitError("429")

    client = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(side_effect=_create)))

    with pytest.raises(FakeRateLimitError):
        await score_run_coherence(run, anthropic_client=client)
