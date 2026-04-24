"""Unit tests for app.game.transformations — eligibility + apply.

All tests are synchronous; no asyncio, no DB, no LLM.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.game.schemas import (
    Agent,
    ColonyState,
    Event,
    EventOrigin,
    Quadrant,
    Resource,
    Transformation,
)
from app.game.transformations import (
    _TRANSFORMATION_DESCRIPTIONS,
    TransformationCandidate,
    apply_transformation,
    check_transformation_eligibility,
)

# ---------------------------------------------------------------------------
# Fabricators (minimal copies of test_engine.py helpers)
# ---------------------------------------------------------------------------

GAME_ID = UUID("aaaaaaaa-0000-0000-0000-000000000001")
USER_ID = UUID("bbbbbbbb-0000-0000-0000-000000000002")


def _agent(
    ethics: float = 0.0,
    temperament: float = 0.0,
    loyalty: float = 0.0,
    grievance: int = 0,
    alive: bool = True,
    memory: list[str] | None = None,
    transformation: Transformation | None = None,
    **over,
) -> Agent:
    return Agent(
        id=over.pop("id", uuid4()),
        game_id=GAME_ID,
        name="A",
        role="Advisor",
        archetype_quadrant=Quadrant.ACCOUNTANT,
        ethics=ethics,
        temperament=temperament,
        loyalty=loyalty,
        portrait_seed="seed",
        health=100,
        morale=50,
        grievance=grievance,
        is_alive=alive,
        memory=memory or [],
        relationships={},
        transformation=transformation,
        **over,
    )


def _resource() -> Resource:
    return Resource(id="fuel", name="Fuel", icon="X", value=50, max=100, critical_threshold=20)


def _colony(agents: list[Agent], turn_number: int = 3) -> ColonyState:
    return ColonyState(
        game_id=GAME_ID,
        user_id=USER_ID,
        setting_id="arctic_station_v1",
        seed=42,
        turn_number=turn_number,
        max_turns=10,
        resources={"fuel": _resource()},
        agents=agents,
        legitimacy=60,
        player_health=100,
        consequence_seeds=[],
        event_log=[],
        current_event=None,
        turn_proposals=[],
        turn_tool_calls=[],
    )


def _event(severity: int = 4) -> Event:
    return Event(
        id="e-test",
        category="crisis",
        title="Crisis",
        body="Something terrible happened.",
        severity=severity,
        origin=EventOrigin.NATURAL,
    )


# ---------------------------------------------------------------------------
# Eligibility — gate checks
# ---------------------------------------------------------------------------


def test_eligibility_requires_high_severity() -> None:
    agent = _agent(ethics=0.8, grievance=5)
    state = _colony([agent])

    # Severity 3 → no candidate.
    assert check_transformation_eligibility(state, agent, _event(severity=3)) is None

    # Severity 4 → candidate.
    cand = check_transformation_eligibility(state, agent, _event(severity=4))
    assert cand is not None
    assert cand.agent_id == agent.id


def test_eligibility_once_per_run() -> None:
    """Agent with an existing transformation never becomes eligible again."""
    existing = Transformation(
        trigger_turn=2,
        description="The principle bent against the outcome. Now the outcome decides.",
        axis_shift={"ethics": -0.4},
    )
    agent = _agent(ethics=0.8, grievance=5, transformation=existing)
    state = _colony([agent])
    assert check_transformation_eligibility(state, agent, _event(severity=5)) is None


def test_eligibility_requires_live_agent() -> None:
    agent = _agent(ethics=0.8, grievance=5, alive=False)
    state = _colony([agent])
    assert check_transformation_eligibility(state, agent, _event(severity=5)) is None


def test_eligibility_requires_grievance_or_recent_death() -> None:
    # Low grievance + no death memory → None.
    bland = _agent(ethics=0.8, grievance=2)
    state = _colony([bland])
    assert check_transformation_eligibility(state, bland, _event(severity=5)) is None

    # High grievance alone is enough.
    angry = _agent(ethics=0.8, grievance=5)
    state2 = _colony([angry])
    assert check_transformation_eligibility(state2, angry, _event(severity=5)) is not None

    # Death pin in recent memory is enough even with zero grievance.
    haunted = _agent(ethics=0.8, grievance=0, memory=["died in turn 4"])
    state3 = _colony([haunted])
    assert check_transformation_eligibility(state3, haunted, _event(severity=5)) is not None


def test_eligibility_death_outside_window_does_not_trigger() -> None:
    """Death tag deep in memory (>3 from end) should not count as recent."""
    # 4 newer non-death memories push the death tag out of the 3-entry window.
    memory = ["died in turn 1", "a", "b", "c", "d"]
    agent = _agent(ethics=0.8, grievance=0, memory=memory)
    state = _colony([agent])
    assert check_transformation_eligibility(state, agent, _event(severity=5)) is None


def test_eligibility_no_candidate_when_axes_moderate() -> None:
    """All axes within [-0.5, 0.5] → no dominant pattern → None."""
    agent = _agent(ethics=0.3, temperament=-0.2, loyalty=0.4, grievance=8)
    state = _colony([agent])
    assert check_transformation_eligibility(state, agent, _event(severity=5)) is None


# ---------------------------------------------------------------------------
# Eligibility — per-axis patterns
# ---------------------------------------------------------------------------


def test_eligibility_idealist_shifts_pragmatist() -> None:
    agent = _agent(ethics=0.8, grievance=5)
    state = _colony([agent])
    cand = check_transformation_eligibility(state, agent, _event(severity=4))
    assert cand is not None
    assert cand.axis_shift == {"ethics": -0.4}
    # Description must come from the bank.
    assert cand.description == _TRANSFORMATION_DESCRIPTIONS[("ethics", "negative")]


def test_eligibility_pragmatist_shifts_idealist() -> None:
    agent = _agent(ethics=-0.8, grievance=5)
    state = _colony([agent])
    cand = check_transformation_eligibility(state, agent, _event(severity=4))
    assert cand is not None
    assert cand.axis_shift == {"ethics": 0.4}
    assert cand.description == _TRANSFORMATION_DESCRIPTIONS[("ethics", "positive")]


def test_eligibility_cautious_shifts_bold() -> None:
    agent = _agent(temperament=-0.8, grievance=5)
    state = _colony([agent])
    cand = check_transformation_eligibility(state, agent, _event(severity=4))
    assert cand is not None
    assert cand.axis_shift == {"temperament": 0.4}
    assert cand.description == _TRANSFORMATION_DESCRIPTIONS[("temperament", "positive")]


def test_eligibility_bold_shifts_cautious() -> None:
    agent = _agent(temperament=0.8, grievance=5)
    state = _colony([agent])
    cand = check_transformation_eligibility(state, agent, _event(severity=4))
    assert cand is not None
    assert cand.axis_shift == {"temperament": -0.4}
    assert cand.description == _TRANSFORMATION_DESCRIPTIONS[("temperament", "negative")]


def test_eligibility_collective_shifts_individual_requires_sev_5() -> None:
    # At severity 4, loyalty shift does not fire — agent has no other dominant
    # axis, so the result must be None.
    agent = _agent(loyalty=0.8, grievance=5)
    state = _colony([agent])
    assert check_transformation_eligibility(state, agent, _event(severity=4)) is None

    # At severity 5, it fires.
    cand = check_transformation_eligibility(state, agent, _event(severity=5))
    assert cand is not None
    assert cand.axis_shift == {"loyalty": -0.4}
    assert cand.description == _TRANSFORMATION_DESCRIPTIONS[("loyalty", "negative")]


def test_eligibility_individual_shifts_collective_requires_sev_5() -> None:
    agent = _agent(loyalty=-0.8, grievance=5)
    state = _colony([agent])
    assert check_transformation_eligibility(state, agent, _event(severity=4)) is None

    cand = check_transformation_eligibility(state, agent, _event(severity=5))
    assert cand is not None
    assert cand.axis_shift == {"loyalty": 0.4}
    assert cand.description == _TRANSFORMATION_DESCRIPTIONS[("loyalty", "positive")]


def test_eligibility_ethics_priority_over_temperament() -> None:
    """When two axes are both dominant, ethics wins (priority order)."""
    agent = _agent(ethics=0.8, temperament=0.8, grievance=5)
    state = _colony([agent])
    cand = check_transformation_eligibility(state, agent, _event(severity=5))
    assert cand is not None
    assert "ethics" in cand.axis_shift
    assert "temperament" not in cand.axis_shift


# ---------------------------------------------------------------------------
# apply_transformation — writes + clamps
# ---------------------------------------------------------------------------


def test_apply_transformation_writes_axis_and_description() -> None:
    agent = _agent(ethics=0.8, grievance=5)
    state = _colony([agent], turn_number=5)
    cand = TransformationCandidate(
        agent_id=agent.id,
        description=_TRANSFORMATION_DESCRIPTIONS[("ethics", "negative")],
        axis_shift={"ethics": -0.4},
        trigger_reason="test",
    )
    new_state = apply_transformation(state, agent.id, cand)

    updated = next(a for a in new_state.agents if a.id == agent.id)
    assert updated.transformation is not None
    assert updated.transformation.trigger_turn == 5
    assert updated.transformation.description == cand.description
    assert updated.transformation.axis_shift == {"ethics": -0.4}
    # Axis actually shifted: 0.8 - 0.4 = 0.4.
    assert updated.ethics == pytest.approx(0.4)


def test_apply_transformation_clamps_axis_to_minus_1() -> None:
    agent = _agent(ethics=-0.9, grievance=5)
    state = _colony([agent])
    cand = TransformationCandidate(
        agent_id=agent.id,
        description="x",
        axis_shift={"ethics": -0.4},
        trigger_reason="t",
    )
    new_state = apply_transformation(state, agent.id, cand)
    updated = next(a for a in new_state.agents if a.id == agent.id)
    assert updated.ethics == pytest.approx(-1.0)


def test_apply_transformation_clamps_axis_to_plus_1() -> None:
    agent = _agent(ethics=0.9, grievance=5)
    state = _colony([agent])
    cand = TransformationCandidate(
        agent_id=agent.id,
        description="x",
        axis_shift={"ethics": 0.4},
        trigger_reason="t",
    )
    new_state = apply_transformation(state, agent.id, cand)
    updated = next(a for a in new_state.agents if a.id == agent.id)
    assert updated.ethics == pytest.approx(1.0)


def test_apply_transformation_twice_raises() -> None:
    agent = _agent(ethics=0.8, grievance=5)
    state = _colony([agent])
    cand = TransformationCandidate(
        agent_id=agent.id,
        description="x",
        axis_shift={"ethics": -0.4},
        trigger_reason="t",
    )
    once = apply_transformation(state, agent.id, cand)
    with pytest.raises(ValueError, match="once-per-run"):
        apply_transformation(once, agent.id, cand)


def test_apply_transformation_unknown_agent_raises_key_error() -> None:
    agent = _agent()
    state = _colony([agent])
    missing_id = uuid4()
    cand = TransformationCandidate(
        agent_id=missing_id,
        description="x",
        axis_shift={"ethics": -0.4},
        trigger_reason="t",
    )
    with pytest.raises(KeyError):
        apply_transformation(state, missing_id, cand)


def test_apply_transformation_appends_memory() -> None:
    agent = _agent(ethics=0.8, grievance=5, memory=["existing note"])
    state = _colony([agent])
    cand = TransformationCandidate(
        agent_id=agent.id,
        description="hardened by loss",
        axis_shift={"ethics": -0.4},
        trigger_reason="t",
    )
    new_state = apply_transformation(state, agent.id, cand)
    updated = next(a for a in new_state.agents if a.id == agent.id)
    assert len(updated.memory) == 2
    assert "existing note" in updated.memory
    assert any(m.startswith("transformed:") for m in updated.memory)


def test_apply_transformation_is_pure() -> None:
    """Input state must be byte-equal to its pre-call snapshot."""
    agent = _agent(ethics=0.8, grievance=5)
    state = _colony([agent])
    snapshot = state.model_dump()
    cand = TransformationCandidate(
        agent_id=agent.id,
        description="x",
        axis_shift={"ethics": -0.4},
        trigger_reason="t",
    )
    apply_transformation(state, agent.id, cand)
    assert state.model_dump() == snapshot


def test_apply_transformation_leaves_other_agents_untouched() -> None:
    target = _agent(ethics=0.8, grievance=5)
    bystander = _agent(ethics=0.0)
    state = _colony([target, bystander])
    bystander_snapshot = bystander.model_dump()
    cand = TransformationCandidate(
        agent_id=target.id,
        description="x",
        axis_shift={"ethics": -0.4},
        trigger_reason="t",
    )
    new_state = apply_transformation(state, target.id, cand)
    updated_bystander = next(a for a in new_state.agents if a.id == bystander.id)
    assert updated_bystander.model_dump() == bystander_snapshot


# ---------------------------------------------------------------------------
# Description bank sanity
# ---------------------------------------------------------------------------


def test_description_bank_covers_six_slots() -> None:
    """Every axis × direction combination has a distinct description."""
    expected_keys = {
        ("ethics", "positive"),
        ("ethics", "negative"),
        ("temperament", "positive"),
        ("temperament", "negative"),
        ("loyalty", "positive"),
        ("loyalty", "negative"),
    }
    assert set(_TRANSFORMATION_DESCRIPTIONS.keys()) == expected_keys
    # All values non-empty, short-ish, and unique.
    values = list(_TRANSFORMATION_DESCRIPTIONS.values())
    assert len(set(values)) == len(values)
    for v in values:
        assert 0 < len(v) < 120
