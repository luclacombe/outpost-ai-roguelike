"""
Unit tests for app.game.schemas — roundtrip fidelity + validation rejection.

All tests are synchronous; no asyncio or DB needed.
"""

import json
import uuid

import pytest
from pydantic import ValidationError

from app.game.schemas import (
    Agent,
    ColonyState,
    ConsequenceSeed,
    Decision,
    DecisionRef,
    EndState,
    Event,
    EventOrigin,
    Proposal,
    ProposalFlavor,
    Quadrant,
    ResolvedEvent,
    Resource,
    SeedPayload,
    ToolCall,
    Transformation,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

GAME_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
USER_ID = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")
AGENT_ID = uuid.UUID("cccccccc-0000-0000-0000-000000000003")


def _minimal_resource(value: int = 50, max_val: int = 100) -> Resource:
    return Resource(
        id="fuel", name="Fuel", icon="🛢", max=max_val, critical_threshold=20, value=value
    )


def _minimal_agent(game_id: uuid.UUID = GAME_ID) -> Agent:
    return Agent(
        id=AGENT_ID,
        game_id=game_id,
        name="Nadia",
        role="Quartermaster",
        archetype_quadrant=Quadrant.ACCOUNTANT,
        ethics=0.8,
        temperament=-0.5,
        loyalty=0.6,
        portrait_seed="nadia-seed",
        health=90,
        morale=75,
        grievance=2,
        is_alive=True,
    )


def _minimal_event() -> Event:
    return Event(
        id="evt-001",
        category="weather",
        title="Arctic Storm",
        body="A storm is coming.",
        severity=3,
        origin=EventOrigin.NATURAL,
    )


def _minimal_colony() -> ColonyState:
    return ColonyState(
        game_id=GAME_ID,
        user_id=USER_ID,
        setting_id="arctic_station_v1",
        seed=42,
        turn_number=1,
        max_turns=10,
        resources={"fuel": _minimal_resource()},
        agents=[_minimal_agent()],
        legitimacy=80,
        player_health=100,
        consequence_seeds=[],
        event_log=[],
        current_event=None,
        turn_proposals=[],
        turn_tool_calls=[],
    )


# ---------------------------------------------------------------------------
# Test 1: ColonyState roundtrip
# ---------------------------------------------------------------------------


def test_colony_state_roundtrip():
    """Serialising to JSON and reloading must produce an identical model."""
    state = _minimal_colony()
    dumped = state.model_dump_json()
    reloaded = ColonyState.model_validate_json(dumped)
    assert reloaded.game_id == state.game_id
    assert reloaded.agents[0].name == "Nadia"
    assert reloaded.resources["fuel"].value == 50
    assert reloaded.end_state is None


def test_colony_state_roundtrip_with_nested_objects():
    """Roundtrip with consequence seeds, proposals, and tool calls."""
    tc = ToolCall(
        name="modify_resource",
        caller_id=AGENT_ID,
        args={"resource_id": "fuel", "delta": -5, "reason": "storm"},
    )
    seed_payload = SeedPayload(
        event_id="evt-consequence", severity=4, category="weather", description="Storm aftermath"
    )
    dec_ref = DecisionRef(turn_number=1, event_id="evt-001", chosen_proposal_id="prop-a")
    seed = ConsequenceSeed(
        game_id=GAME_ID,
        written_at_turn=1,
        fires_at_turn=3,
        foreshadowed=True,
        foreshadow_turn=2,
        payload=seed_payload,
        origin_decision=dec_ref,
    )
    proposal = Proposal(
        id="prop-a",
        proposed_by_agent_id=AGENT_ID,
        flavor=ProposalFlavor.DOMINANT,
        name="Full Power",
        description="Run the generator at full.",
        effects_preview=["–5 fuel"],
        tool_calls_on_pick=[tc],
    )
    state = _minimal_colony()
    state = state.model_copy(
        update={
            "consequence_seeds": [seed],
            "turn_proposals": [proposal],
            "turn_tool_calls": [tc],
            "end_state": EndState.ENDURED,
        }
    )
    dumped = json.loads(state.model_dump_json())
    reloaded = ColonyState.model_validate(dumped)
    assert reloaded.end_state == EndState.ENDURED
    assert reloaded.consequence_seeds[0].fires_at_turn == 3
    assert reloaded.turn_proposals[0].flavor == ProposalFlavor.DOMINANT


# ---------------------------------------------------------------------------
# Test 2: Agent axis bounds
# ---------------------------------------------------------------------------


def test_agent_axis_bounds_reject_out_of_range():
    """An axis value > 1.0 must raise ValidationError."""
    with pytest.raises(ValidationError):
        Agent(
            id=AGENT_ID,
            game_id=GAME_ID,
            name="Marko",
            role="Commissar",
            archetype_quadrant=Quadrant.OPERATOR,
            ethics=1.5,  # invalid
            temperament=0.0,
            loyalty=0.0,
            portrait_seed="marko-seed",
            health=100,
            morale=80,
            grievance=0,
            is_alive=True,
        )


def test_agent_axis_bounds_reject_below_minus_one():
    """An axis value < -1.0 must raise ValidationError."""
    with pytest.raises(ValidationError):
        Agent(
            id=AGENT_ID,
            game_id=GAME_ID,
            name="Irena",
            role="Medic",
            archetype_quadrant=Quadrant.CONSCIENCE,
            ethics=-1.1,  # invalid
            temperament=0.0,
            loyalty=0.0,
            portrait_seed="irena-seed",
            health=100,
            morale=80,
            grievance=0,
            is_alive=True,
        )


# ---------------------------------------------------------------------------
# Test 3: Grievance schema-level clamp
# ---------------------------------------------------------------------------


def test_grievance_clamp_is_schema_level():
    """grievance=11 exceeds the 0-10 bound and must raise ValidationError."""
    with pytest.raises(ValidationError):
        Agent(
            id=AGENT_ID,
            game_id=GAME_ID,
            name="Yuri",
            role="Radio Operator",
            archetype_quadrant=Quadrant.CRUSADER,
            ethics=0.3,
            temperament=0.7,
            loyalty=-0.4,
            portrait_seed="yuri-seed",
            health=100,
            morale=70,
            grievance=11,  # invalid
            is_alive=True,
        )


# ---------------------------------------------------------------------------
# Test 4: Resource value cannot exceed max
# ---------------------------------------------------------------------------


def test_resource_value_cannot_exceed_max():
    """value=101 with max=100 must raise ValidationError."""
    with pytest.raises(ValidationError):
        Resource(id="food", name="Food", icon="🥫", max=100, critical_threshold=20, value=101)


def test_resource_value_cannot_be_negative():
    """value=-1 must raise ValidationError."""
    with pytest.raises(ValidationError):
        Resource(id="food", name="Food", icon="🥫", max=100, critical_threshold=20, value=-1)


def test_resource_value_at_zero_is_valid():
    """value=0 is valid (depleted resource)."""
    r = Resource(id="food", name="Food", icon="🥫", max=100, critical_threshold=20, value=0)
    assert r.value == 0


def test_resource_value_at_max_is_valid():
    """value == max is valid (full resource)."""
    r = Resource(id="fuel", name="Fuel", icon="🛢", max=100, critical_threshold=20, value=100)
    assert r.value == r.max


# ---------------------------------------------------------------------------
# Test 5: ToolCall default IDs are unique
# ---------------------------------------------------------------------------


def test_tool_call_default_id_is_unique():
    """Two ToolCalls with no explicit id must have different UUIDs."""
    tc1 = ToolCall(name="remember", caller_id=None, args={"fact": "A"})
    tc2 = ToolCall(name="remember", caller_id=None, args={"fact": "B"})
    assert tc1.id != tc2.id


# ---------------------------------------------------------------------------
# Test 6: ConsequenceSeed fires_at_turn ordering
# ---------------------------------------------------------------------------


def test_consequence_seed_validates_turn_ordering():
    """fires_at_turn <= written_at_turn must raise ValidationError."""
    with pytest.raises(ValidationError):
        ConsequenceSeed(
            game_id=GAME_ID,
            written_at_turn=5,
            fires_at_turn=3,  # invalid: must be > written_at_turn
            foreshadowed=False,
            foreshadow_turn=None,
            payload=SeedPayload(event_id="e", severity=2, category="c", description="d"),
            origin_decision=DecisionRef(turn_number=5, event_id="e", chosen_proposal_id="p"),
        )


def test_consequence_seed_fires_at_turn_equal_written_invalid():
    """fires_at_turn == written_at_turn must also raise ValidationError."""
    with pytest.raises(ValidationError):
        ConsequenceSeed(
            game_id=GAME_ID,
            written_at_turn=3,
            fires_at_turn=3,  # equal is not greater-than
            foreshadowed=False,
            foreshadow_turn=None,
            payload=SeedPayload(event_id="e", severity=1, category="c", description="d"),
            origin_decision=DecisionRef(turn_number=3, event_id="e", chosen_proposal_id="p"),
        )


# ---------------------------------------------------------------------------
# Test 7: ConsequenceSeed foreshadow_turn required when foreshadowed=True
# ---------------------------------------------------------------------------


def test_consequence_seed_foreshadow_must_have_turn():
    """foreshadowed=True with foreshadow_turn=None must raise ValidationError."""
    with pytest.raises(ValidationError):
        ConsequenceSeed(
            game_id=GAME_ID,
            written_at_turn=1,
            fires_at_turn=4,
            foreshadowed=True,
            foreshadow_turn=None,  # invalid: must be set when foreshadowed
            payload=SeedPayload(event_id="e", severity=3, category="c", description="d"),
            origin_decision=DecisionRef(turn_number=1, event_id="e", chosen_proposal_id="p"),
        )


def test_consequence_seed_foreshadow_turn_must_be_in_range():
    """foreshadow_turn must satisfy written_at_turn <= foreshadow_turn < fires_at_turn."""
    with pytest.raises(ValidationError):
        ConsequenceSeed(
            game_id=GAME_ID,
            written_at_turn=1,
            fires_at_turn=4,
            foreshadowed=True,
            foreshadow_turn=4,  # invalid: must be strictly < fires_at_turn
            payload=SeedPayload(event_id="e", severity=3, category="c", description="d"),
            origin_decision=DecisionRef(turn_number=1, event_id="e", chosen_proposal_id="p"),
        )


def test_consequence_seed_valid_foreshadow():
    """A valid seed with foreshadow_turn in range must construct without error."""
    seed = ConsequenceSeed(
        game_id=GAME_ID,
        written_at_turn=1,
        fires_at_turn=5,
        foreshadowed=True,
        foreshadow_turn=3,  # valid: 1 <= 3 < 5
        payload=SeedPayload(event_id="e", severity=2, category="weather", description="hint"),
        origin_decision=DecisionRef(turn_number=1, event_id="e-orig", chosen_proposal_id="p1"),
    )
    assert seed.foreshadow_turn == 3


# ---------------------------------------------------------------------------
# Test 8: end_state optional, defaults to None
# ---------------------------------------------------------------------------


def test_end_state_optional_defaults_none():
    """A fresh ColonyState has end_state=None (run still active)."""
    state = _minimal_colony()
    assert state.end_state is None


def test_end_state_can_be_set_to_each_value():
    """All four EndState values can be set on ColonyState."""
    for es in EndState:
        state = _minimal_colony().model_copy(update={"end_state": es})
        assert state.end_state == es


# ---------------------------------------------------------------------------
# Test 9: Additional edge cases
# ---------------------------------------------------------------------------


def test_event_severity_bounds():
    """Event severity 0 is below the 1..5 range."""
    with pytest.raises(ValidationError):
        Event(id="e", category="c", title="t", body="b", severity=0, origin=EventOrigin.NATURAL)


def test_event_severity_above_5():
    """Event severity 6 exceeds the 1..5 range."""
    with pytest.raises(ValidationError):
        Event(id="e", category="c", title="t", body="b", severity=6, origin=EventOrigin.NATURAL)


def test_agent_verbal_tic_defaults_empty():
    """verbal_tic defaults to empty string (generator fills it in later)."""
    agent = _minimal_agent()
    assert agent.verbal_tic == ""


def test_agent_memory_defaults_empty_list():
    """memory defaults to an empty list."""
    agent = _minimal_agent()
    assert agent.memory == []


def test_agent_relationships_defaults_empty_dict():
    """relationships defaults to an empty dict."""
    agent = _minimal_agent()
    assert agent.relationships == {}


def test_agent_transformation_defaults_none():
    """transformation defaults to None (set at most once per run)."""
    agent = _minimal_agent()
    assert agent.transformation is None


def test_transformation_roundtrip():
    """Transformation survives JSON roundtrip on an Agent."""
    t = Transformation(
        trigger_turn=5,
        description="Watched the collective sacrifice fail.",
        axis_shift={"loyalty": -0.4},
    )
    agent = _minimal_agent().model_copy(update={"transformation": t})
    reloaded = Agent.model_validate_json(agent.model_dump_json())
    assert reloaded.transformation is not None
    assert reloaded.transformation.trigger_turn == 5
    assert reloaded.transformation.axis_shift["loyalty"] == pytest.approx(-0.4)


def test_event_log_roundtrip():
    """ResolvedEvent can be stored on ColonyState and survives roundtrip."""
    resolved = ResolvedEvent(
        turn_number=1,
        event=_minimal_event(),
        chosen_proposal_id="prop-a",
        summary="Yuri rigged a bypass. Nadia did not speak at breakfast.",
    )
    state = _minimal_colony().model_copy(update={"event_log": [resolved]})
    reloaded = ColonyState.model_validate_json(state.model_dump_json())
    assert reloaded.event_log[0].summary == resolved.summary


def test_colony_legitimacy_bounds():
    """legitimacy outside 0..100 must raise ValidationError."""
    base = _minimal_colony().model_dump()
    base["legitimacy"] = 101
    with pytest.raises(ValidationError):
        ColonyState.model_validate(base)
    base["legitimacy"] = -1
    with pytest.raises(ValidationError):
        ColonyState.model_validate(base)


def test_colony_player_health_bounds():
    """player_health outside 0..100 must raise ValidationError."""
    base = _minimal_colony().model_dump()
    base["player_health"] = 101
    with pytest.raises(ValidationError):
        ColonyState.model_validate(base)
    base["player_health"] = -1
    with pytest.raises(ValidationError):
        ColonyState.model_validate(base)


def test_agent_relationships_value_out_of_range_rejected():
    """relationship scores outside -10..10 must raise ValidationError."""
    from uuid import uuid4

    other_id = uuid4()
    with pytest.raises(ValidationError):
        Agent(
            id=AGENT_ID,
            game_id=GAME_ID,
            name="Nadia",
            role="Quartermaster",
            archetype_quadrant=Quadrant.ACCOUNTANT,
            ethics=0.8,
            temperament=-0.5,
            loyalty=0.6,
            portrait_seed="nadia-seed",
            health=90,
            morale=75,
            grievance=2,
            is_alive=True,
            relationships={other_id: 11},
        )
    with pytest.raises(ValidationError):
        Agent(
            id=AGENT_ID,
            game_id=GAME_ID,
            name="Nadia",
            role="Quartermaster",
            archetype_quadrant=Quadrant.ACCOUNTANT,
            ethics=0.8,
            temperament=-0.5,
            loyalty=0.6,
            portrait_seed="nadia-seed",
            health=90,
            morale=75,
            grievance=2,
            is_alive=True,
            relationships={other_id: -11},
        )


# ---------------------------------------------------------------------------
# Test 10: Decision.choices_offered cardinality
# ---------------------------------------------------------------------------


def _minimal_proposal(pid: str) -> Proposal:
    return Proposal(
        id=pid,
        proposed_by_agent_id=None,
        flavor=ProposalFlavor.WILDCARD,
        name="Option",
        description="Do something.",
        effects_preview=[],
        tool_calls_on_pick=[],
    )


def test_decision_choices_offered_requires_exactly_3():
    """Decision with 2 proposals must raise ValidationError (spec §7.3)."""
    dec_ref = DecisionRef(turn_number=1, event_id="evt-001", chosen_proposal_id="p1")
    with pytest.raises(ValidationError):
        Decision(
            turn_number=1,
            event_id="evt-001",
            choices_offered=[_minimal_proposal("p1"), _minimal_proposal("p2")],
            chosen=dec_ref,
            effects_applied=[],
            seeds_written=[],
            grievance_shifts={},
            legitimacy_cost=0,
        )
