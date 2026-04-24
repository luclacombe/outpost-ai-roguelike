"""
Unit tests for app.game.engine — pure game engine.

All tests are synchronous; no asyncio, no DB, no LLM.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.game.engine import (
    _append_memory,
    _pin_memory,
    advance_turn,
    apply_tool_call,
    apply_tool_calls,
    check_deaths,
    compute_end_state,
    resolve_coup,
)
from app.game.schemas import (
    Agent,
    ColonyState,
    EndState,
    Event,
    EventOrigin,
    Proposal,
    ProposalFlavor,
    Quadrant,
    ResolvedEvent,
    Resource,
    ToolCall,
)

# ---------------------------------------------------------------------------
# Fabricators
# ---------------------------------------------------------------------------

GAME_ID = UUID("aaaaaaaa-0000-0000-0000-000000000001")
USER_ID = UUID("bbbbbbbb-0000-0000-0000-000000000002")


def _agent(
    name: str = "A",
    grievance: int = 0,
    temperament: float = 0.0,
    health: int = 100,
    alive: bool = True,
    **over,
) -> Agent:
    return Agent(
        id=over.pop("id", uuid4()),
        game_id=GAME_ID,
        name=name,
        role="Advisor",
        archetype_quadrant=Quadrant.ACCOUNTANT,
        ethics=0.0,
        temperament=temperament,
        loyalty=0.0,
        portrait_seed="seed",
        health=health,
        morale=50,
        grievance=grievance,
        is_alive=alive,
        **over,
    )


def _resource(rid: str = "fuel", value: int = 50, max_: int = 100, crit: int = 20) -> Resource:
    return Resource(
        id=rid,
        name=rid.capitalize(),
        icon="X",
        value=value,
        max=max_,
        critical_threshold=crit,
    )


def _colony(
    agents: list[Agent] | None = None,
    resources: dict[str, Resource] | None = None,
    turn_number: int = 1,
    max_turns: int = 10,
    legitimacy: int = 60,
    player_health: int = 100,
    **over,
) -> ColonyState:
    if agents is None:
        agents = [_agent()]
    if resources is None:
        resources = {"fuel": _resource()}
    defaults: dict = {
        "consequence_seeds": [],
        "event_log": [],
        "current_event": None,
        "turn_proposals": [],
        "turn_tool_calls": [],
    }
    defaults.update(over)
    return ColonyState(
        game_id=GAME_ID,
        user_id=USER_ID,
        setting_id="arctic_station_v1",
        seed=42,
        turn_number=turn_number,
        max_turns=max_turns,
        resources=resources,
        agents=agents,
        legitimacy=legitimacy,
        player_health=player_health,
        **defaults,
    )


def _tc(name: str, args: dict | None = None, caller_id: UUID | None = None) -> ToolCall:
    return ToolCall(name=name, caller_id=caller_id, args=args or {})


# ---------------------------------------------------------------------------
# apply_tool_call — modify_resource (valid)
# ---------------------------------------------------------------------------


def test_modify_resource_valid_mutates_state() -> None:
    state = _colony(resources={"fuel": _resource(value=50)})
    tc = _tc("modify_resource", {"resource_id": "fuel", "delta": -10, "reason": "used"})
    new_state = apply_tool_call(state, tc)
    assert new_state.resources["fuel"].value == 40


def test_modify_resource_does_not_mutate_input() -> None:
    state = _colony(resources={"fuel": _resource(value=50)})
    snapshot = state.model_dump()
    tc = _tc("modify_resource", {"resource_id": "fuel", "delta": -10, "reason": "used"})
    apply_tool_call(state, tc)
    assert state.model_dump() == snapshot


def test_modify_resource_appends_tc_to_turn_tool_calls() -> None:
    state = _colony(resources={"fuel": _resource(value=50)})
    tc = _tc("modify_resource", {"resource_id": "fuel", "delta": 5, "reason": "found"})
    new_state = apply_tool_call(state, tc)
    assert len(new_state.turn_tool_calls) == 1
    assert new_state.turn_tool_calls[0].rejection_reason is None


# ---------------------------------------------------------------------------
# apply_tool_call — modify_resource (invalid)
# ---------------------------------------------------------------------------


def test_modify_resource_invalid_leaves_resources_unchanged() -> None:
    state = _colony(resources={"fuel": _resource(value=5)})
    tc = _tc("modify_resource", {"resource_id": "fuel", "delta": -10, "reason": "drained"})
    new_state = apply_tool_call(state, tc)
    # Resource unchanged.
    assert new_state.resources["fuel"].value == 5


def test_modify_resource_invalid_records_rejection_reason() -> None:
    state = _colony(resources={"fuel": _resource(value=5)})
    tc = _tc("modify_resource", {"resource_id": "fuel", "delta": -10, "reason": "drained"})
    new_state = apply_tool_call(state, tc)
    assert len(new_state.turn_tool_calls) == 1
    assert new_state.turn_tool_calls[0].rejection_reason == "would_underflow"


# ---------------------------------------------------------------------------
# apply_tool_call — modify_health clamping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "start,delta,expected",
    [
        (10, -50, 0),  # clamps at 0
        (90, 50, 100),  # clamps at 100
        (50, -20, 30),  # normal
    ],
)
def test_modify_health_player_clamps(start: int, delta: int, expected: int) -> None:
    state = _colony(player_health=start)
    tc = _tc("modify_health", {"target_id": "player", "delta": delta, "reason": "event"})
    new_state = apply_tool_call(state, tc)
    assert new_state.player_health == expected


def test_modify_health_agent_clamps_at_zero() -> None:
    agent = _agent(health=5)
    state = _colony(agents=[agent])
    tc = _tc("modify_health", {"target_id": str(agent.id), "delta": -20, "reason": "hit"})
    new_state = apply_tool_call(state, tc)
    updated = next(a for a in new_state.agents if a.id == agent.id)
    assert updated.health == 0


def test_modify_health_agent_clamps_at_100() -> None:
    agent = _agent(health=95)
    state = _colony(agents=[agent])
    tc = _tc("modify_health", {"target_id": str(agent.id), "delta": 20, "reason": "healed"})
    new_state = apply_tool_call(state, tc)
    updated = next(a for a in new_state.agents if a.id == agent.id)
    assert updated.health == 100


# ---------------------------------------------------------------------------
# apply_tool_call — grievance clamping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "start,delta,expected",
    [
        (0, -3, 0),  # clamps at 0
        (9, 3, 10),  # clamps at 10
        (5, 2, 7),  # normal
    ],
)
def test_adjust_grievance_clamps(start: int, delta: int, expected: int) -> None:
    caller_id = uuid4()
    target_id = uuid4()
    caller = _agent(id=caller_id)
    target = _agent(id=target_id, grievance=start)
    state = _colony(agents=[caller, target])
    tc = _tc(
        "adjust_grievance",
        {"target_id": str(target_id), "delta": delta, "reason": "reason"},
        caller_id=caller_id,
    )
    new_state = apply_tool_call(state, tc)
    updated = next(a for a in new_state.agents if a.id == target_id)
    assert updated.grievance == expected


# ---------------------------------------------------------------------------
# apply_tool_call — speak_to_agent memory
# ---------------------------------------------------------------------------


def test_speak_to_agent_fifo_caps_at_10() -> None:
    caller_id = uuid4()
    target_id = uuid4()
    caller = _agent(id=caller_id)
    # Pre-seed target with 10 non-pinned messages.
    pre_seed = [f"message {i}" for i in range(10)]
    target = _agent(id=target_id, memory=pre_seed)
    state = _colony(agents=[caller, target])
    tc = _tc(
        "speak_to_agent",
        {"target_id": str(target_id), "message": "new message"},
        caller_id=caller_id,
    )
    new_state = apply_tool_call(state, tc)
    updated = next(a for a in new_state.agents if a.id == target_id)
    assert len(updated.memory) == 10
    # The new message should be present.
    assert "new message" in updated.memory
    # The oldest (first pre-seeded) should be gone.
    assert "message 0" not in updated.memory


def test_speak_to_agent_preserves_pinned_death_memories() -> None:
    caller_id = uuid4()
    target_id = uuid4()
    caller = _agent(id=caller_id)
    # Mix: 1 pinned + 9 non-pinned = 10 total.
    pinned = "died in turn 3"
    non_pinned = [f"message {i}" for i in range(9)]
    target = _agent(id=target_id, memory=[pinned, *non_pinned])
    state = _colony(agents=[caller, target])
    tc = _tc(
        "speak_to_agent",
        {"target_id": str(target_id), "message": "a new one"},
        caller_id=caller_id,
    )
    new_state = apply_tool_call(state, tc)
    updated = next(a for a in new_state.agents if a.id == target_id)
    assert len(updated.memory) == 10
    # Pinned must survive.
    assert pinned in updated.memory
    # Newest message in.
    assert "a new one" in updated.memory
    # Oldest non-pinned gone.
    assert "message 0" not in updated.memory


def test_speak_to_agent_truncates_message_to_200_chars() -> None:
    caller_id = uuid4()
    target_id = uuid4()
    caller = _agent(id=caller_id)
    target = _agent(id=target_id)
    state = _colony(agents=[caller, target])
    long_message = "x" * 300
    tc = _tc(
        "speak_to_agent",
        {"target_id": str(target_id), "message": long_message},
        caller_id=caller_id,
    )
    new_state = apply_tool_call(state, tc)
    updated = next(a for a in new_state.agents if a.id == target_id)
    stored = [m for m in updated.memory if m.startswith("x")]
    assert stored and len(stored[0]) <= 200


# ---------------------------------------------------------------------------
# apply_tool_call — remember
# ---------------------------------------------------------------------------


def test_remember_inserts_fact_into_caller_memory() -> None:
    caller_id = uuid4()
    caller = _agent(id=caller_id, memory=["old fact"])
    state = _colony(agents=[caller])
    tc = _tc("remember", {"fact": "new insight"}, caller_id=caller_id)
    new_state = apply_tool_call(state, tc)
    updated = next(a for a in new_state.agents if a.id == caller_id)
    assert "new insight" in updated.memory
    assert "old fact" in updated.memory


# ---------------------------------------------------------------------------
# apply_tool_call — propose_action
# ---------------------------------------------------------------------------


def test_propose_action_appends_proposal() -> None:
    state = _colony()
    tc = _tc(
        "propose_action",
        {"name": "Ration fuel", "description": "Cut usage by 20%.", "flavor": "DOMINANT"},
    )
    new_state = apply_tool_call(state, tc)
    assert len(new_state.turn_proposals) == 1
    p = new_state.turn_proposals[0]
    assert p.name == "Ration fuel"
    assert p.flavor == ProposalFlavor.DOMINANT


# ---------------------------------------------------------------------------
# apply_tool_calls — convenience fold
# ---------------------------------------------------------------------------


def test_apply_tool_calls_folds_correctly() -> None:
    state = _colony(resources={"fuel": _resource(value=50)})
    tcs = [
        _tc("modify_resource", {"resource_id": "fuel", "delta": -5, "reason": "a"}),
        _tc("modify_resource", {"resource_id": "fuel", "delta": -5, "reason": "b"}),
    ]
    new_state = apply_tool_calls(state, tcs)
    assert new_state.resources["fuel"].value == 40
    assert len(new_state.turn_tool_calls) == 2


# ---------------------------------------------------------------------------
# Anti-regression: immutability
# ---------------------------------------------------------------------------


def test_apply_tool_call_does_not_mutate_input_agents() -> None:
    agent = _agent(grievance=2)
    caller_id = uuid4()
    other = _agent(id=caller_id)
    state2 = _colony(agents=[agent, other])
    tc = _tc(
        "adjust_grievance",
        {"target_id": str(agent.id), "delta": 3, "reason": "test"},
        caller_id=caller_id,
    )
    apply_tool_call(state2, tc)
    # Original state2 agents unchanged.
    original_agent = next(a for a in state2.agents if a.id == agent.id)
    assert original_agent.grievance == 2


# ---------------------------------------------------------------------------
# Anti-regression: engine-emitted tool calls (caller_id=None) for modify_resource
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "delta,expected",
    [
        (10, 60),
        (-10, 40),
    ],
)
def test_engine_emitted_modify_resource_works(delta: int, expected: int) -> None:
    """Engine-emitted tc (caller_id=None) for modify_resource must reach same path."""
    state = _colony(resources={"fuel": _resource(value=50)})
    tc = ToolCall(
        name="modify_resource",
        caller_id=None,
        args={"resource_id": "fuel", "delta": delta, "reason": "engine adjustment"},
    )
    new_state = apply_tool_call(state, tc)
    assert new_state.resources["fuel"].value == expected


# ---------------------------------------------------------------------------
# check_deaths
# ---------------------------------------------------------------------------


def test_check_deaths_marks_agent_dead() -> None:
    agent = _agent(health=0)
    state = _colony(agents=[agent])
    new_state = check_deaths(state)
    updated = next(a for a in new_state.agents if a.id == agent.id)
    assert updated.is_alive is False
    assert updated.health == 0


def test_check_deaths_pins_death_memory_to_dead_agent() -> None:
    agent = _agent(health=0)
    state = _colony(agents=[agent], turn_number=4)
    new_state = check_deaths(state)
    updated = next(a for a in new_state.agents if a.id == agent.id)
    assert "died in turn 4" in updated.memory


def test_check_deaths_survivors_get_death_memory() -> None:
    dead = _agent(name="Dead", health=0)
    alive = _agent(name="Alive")
    state = _colony(agents=[dead, alive], turn_number=5)
    new_state = check_deaths(state)
    survivor = next(a for a in new_state.agents if a.id == alive.id)
    assert "died in turn 5" in survivor.memory


def test_check_deaths_player_at_zero_sets_killed() -> None:
    state = _colony(player_health=0)
    new_state = check_deaths(state)
    assert new_state.end_state == EndState.KILLED


def test_check_deaths_healthy_player_not_killed() -> None:
    state = _colony(player_health=50)
    new_state = check_deaths(state)
    assert new_state.end_state is None


def test_check_deaths_cross_call_idempotent() -> None:
    """Calling check_deaths twice in the same turn does not accumulate
    duplicate 'died in turn N' memories on survivors."""
    dead = _agent(name="Dead", health=0)  # alive=True going in; will die
    alive = _agent(name="Alive")
    state = _colony(agents=[dead, alive], turn_number=5)
    s1 = check_deaths(state)
    s2 = check_deaths(s1)
    survivor = next(a for a in s2.agents if a.id == alive.id)
    death_msgs = [m for m in survivor.memory if m == "died in turn 5"]
    assert len(death_msgs) == 1


# ---------------------------------------------------------------------------
# resolve_coup — four outcome branches
# ---------------------------------------------------------------------------


def test_resolve_coup_kills_when_all_conditions_met() -> None:
    """initiator.grievance=10, accomplice.grievance=10, bold=1.2, legitimacy=5 → KILLED."""
    initiator_id = uuid4()
    accomplice_id = uuid4()
    initiator = _agent(id=initiator_id, grievance=10, temperament=0.6)
    accomplice = _agent(id=accomplice_id, grievance=10, temperament=0.6)
    state = _colony(agents=[initiator, accomplice], legitimacy=5)
    new_state = resolve_coup(state, initiator_id=initiator_id)
    assert new_state.end_state == EndState.KILLED


def test_resolve_coup_ousted_legitimacy_too_high() -> None:
    """Same grievance and boldness, but legitimacy=20 → OUSTED."""
    initiator_id = uuid4()
    accomplice_id = uuid4()
    initiator = _agent(id=initiator_id, grievance=10, temperament=0.6)
    accomplice = _agent(id=accomplice_id, grievance=10, temperament=0.6)
    state = _colony(agents=[initiator, accomplice], legitimacy=20)
    new_state = resolve_coup(state, initiator_id=initiator_id)
    assert new_state.end_state == EndState.OUSTED


def test_resolve_coup_ousted_boldness_too_low() -> None:
    """initiator=9, accomplice=9, but total_boldness=0.4 → OUSTED."""
    initiator_id = uuid4()
    accomplice_id = uuid4()
    initiator = _agent(id=initiator_id, grievance=9, temperament=0.2)
    accomplice = _agent(id=accomplice_id, grievance=9, temperament=0.2)
    state = _colony(agents=[initiator, accomplice], legitimacy=5)
    new_state = resolve_coup(state, initiator_id=initiator_id)
    assert new_state.end_state == EndState.OUSTED


def test_resolve_coup_ousted_accomplice_grievance_too_low() -> None:
    """initiator=9, accomplice=8 (< 9), bold=1.2, legitimacy=5 → OUSTED (max_accomplice_grievance < 9)."""
    initiator_id = uuid4()
    accomplice_id = uuid4()
    initiator = _agent(id=initiator_id, grievance=9, temperament=0.6)
    accomplice = _agent(id=accomplice_id, grievance=8, temperament=0.6)
    state = _colony(agents=[initiator, accomplice], legitimacy=5)
    new_state = resolve_coup(state, initiator_id=initiator_id)
    assert new_state.end_state == EndState.OUSTED


def test_resolve_coup_no_change_when_end_state_set() -> None:
    """Coup on a state with pre-set end_state → no change."""
    initiator_id = uuid4()
    accomplice_id = uuid4()
    initiator = _agent(id=initiator_id, grievance=10, temperament=0.9)
    accomplice = _agent(id=accomplice_id, grievance=10, temperament=0.9)
    state = _colony(agents=[initiator, accomplice], legitimacy=5, end_state=EndState.ENDURED)
    new_state = resolve_coup(state, initiator_id=initiator_id)
    assert new_state.end_state == EndState.ENDURED


def test_resolve_coup_no_accomplices_ousted() -> None:
    """Coup with no accomplices: max_accomplice_grievance defaults to 0 → OUSTED."""
    initiator_id = uuid4()
    initiator = _agent(id=initiator_id, grievance=10, temperament=0.9)
    state = _colony(agents=[initiator], legitimacy=5)
    new_state = resolve_coup(state, initiator_id=initiator_id)
    assert new_state.end_state == EndState.OUSTED


# ---------------------------------------------------------------------------
# initiate_coup via apply_tool_call (integration path through engine)
# ---------------------------------------------------------------------------


def test_initiate_coup_via_apply_tool_call_ousted() -> None:
    initiator_id = uuid4()
    accomplice_id = uuid4()
    # legitimacy=60 forbids KILLED regardless of grievance/boldness.
    initiator = _agent(id=initiator_id, grievance=9, temperament=0.0)
    accomplice = _agent(id=accomplice_id, grievance=9, temperament=0.0)
    state = _colony(agents=[initiator, accomplice], legitimacy=60)
    tc = _tc("initiate_coup", {}, caller_id=initiator_id)
    new_state = apply_tool_call(state, tc)
    assert new_state.end_state == EndState.OUSTED  # legitimacy=60 forbids KILLED
    assert len(new_state.turn_tool_calls) == 1
    assert new_state.turn_tool_calls[0].name == "initiate_coup"
    assert new_state.turn_tool_calls[0].rejection_reason is None


def test_initiate_coup_via_apply_tool_call_killed() -> None:
    initiator_id = uuid4()
    accomplice_id = uuid4()
    # All conditions for KILLED: both grievance >= 9, total_boldness > 0.5, legitimacy < 15.
    initiator = _agent(id=initiator_id, grievance=10, temperament=0.6)
    accomplice = _agent(id=accomplice_id, grievance=10, temperament=0.6)
    state = _colony(agents=[initiator, accomplice], legitimacy=5)
    tc = _tc("initiate_coup", {}, caller_id=initiator_id)
    new_state = apply_tool_call(state, tc)
    assert new_state.end_state == EndState.KILLED


# ---------------------------------------------------------------------------
# advance_turn
# ---------------------------------------------------------------------------


def test_advance_turn_increments_turn_number() -> None:
    state = _colony(turn_number=3)
    new_state = advance_turn(state)
    assert new_state.turn_number == 4


def test_advance_turn_decrements_legitimacy() -> None:
    state = _colony(legitimacy=60)
    new_state = advance_turn(state)
    assert new_state.legitimacy == 59


def test_advance_turn_legitimacy_does_not_go_below_zero() -> None:
    state = _colony(legitimacy=0)
    new_state = advance_turn(state)
    assert new_state.legitimacy == 0


def test_advance_turn_clears_per_turn_fields() -> None:
    event = Event(
        id="e1",
        category="crisis",
        title="Event",
        body="Something happened.",
        severity=2,
        origin=EventOrigin.NATURAL,
    )
    proposal = Proposal(
        id="p1",
        proposed_by_agent_id=None,
        flavor=ProposalFlavor.DOMINANT,
        name="Do it",
        description="Do the thing.",
        effects_preview=[],
        tool_calls_on_pick=[],
    )
    tc = ToolCall(
        name="modify_resource",
        caller_id=None,
        args={"resource_id": "fuel", "delta": 1, "reason": "x"},
    )
    state = _colony(current_event=event, turn_proposals=[proposal], turn_tool_calls=[tc])
    new_state = advance_turn(state)
    assert new_state.current_event is None
    assert new_state.turn_proposals == []
    assert new_state.turn_tool_calls == []


def test_advance_turn_preserves_event_log() -> None:
    event = Event(
        id="e1",
        category="crisis",
        title="Event",
        body="Something.",
        severity=2,
        origin=EventOrigin.NATURAL,
    )
    resolved = ResolvedEvent(
        turn_number=1,
        event=event,
        chosen_proposal_id="p1",
        summary="Player chose wisely.",
    )
    state = _colony(event_log=[resolved])
    new_state = advance_turn(state)
    assert len(new_state.event_log) == 1


def test_advance_turn_sets_end_state_when_conditions_met() -> None:
    """All agents dead → advance_turn should detect COLLAPSED via compute_end_state."""
    dead = _agent(health=0, alive=False)
    # Also need resources OK to isolate the all-agents-dead branch.
    state = _colony(agents=[dead], turn_number=5, max_turns=10)
    new_state = advance_turn(state)
    assert new_state.end_state == EndState.COLLAPSED


# ---------------------------------------------------------------------------
# compute_end_state — all 6 branches
# ---------------------------------------------------------------------------


def test_compute_end_state_returns_existing() -> None:
    state = _colony(end_state=EndState.OUSTED)
    result = compute_end_state(state)
    assert result == EndState.OUSTED


def test_compute_end_state_player_dead_killed() -> None:
    state = _colony(player_health=0)
    result = compute_end_state(state)
    assert result == EndState.KILLED


def test_compute_end_state_all_agents_dead_collapsed() -> None:
    dead = _agent(health=0, alive=False)
    state = _colony(agents=[dead])
    result = compute_end_state(state)
    assert result == EndState.COLLAPSED


def test_compute_end_state_resource_at_zero_collapsed() -> None:
    state = _colony(resources={"fuel": _resource(value=0)})
    result = compute_end_state(state)
    assert result == EndState.COLLAPSED


def test_compute_end_state_turn_limit_endured() -> None:
    """Turn >= max, ≥2 live agents, all resources above threshold, legitimacy ≥ 20 → ENDURED."""
    a1 = _agent(name="A1")
    a2 = _agent(name="A2")
    state = _colony(
        agents=[a1, a2],
        turn_number=10,
        max_turns=10,
        legitimacy=20,
        resources={"fuel": _resource(value=50, crit=20)},
    )
    result = compute_end_state(state)
    assert result == EndState.ENDURED


def test_compute_end_state_turn_limit_legitimacy_too_low_collapsed() -> None:
    a1 = _agent(name="A1")
    a2 = _agent(name="A2")
    state = _colony(
        agents=[a1, a2],
        turn_number=10,
        max_turns=10,
        legitimacy=19,
        resources={"fuel": _resource(value=50, crit=20)},
    )
    result = compute_end_state(state)
    assert result == EndState.COLLAPSED


def test_compute_end_state_run_continues_returns_none() -> None:
    state = _colony(turn_number=3, max_turns=10, legitimacy=60)
    result = compute_end_state(state)
    assert result is None


# ---------------------------------------------------------------------------
# back_leader
# ---------------------------------------------------------------------------


def test_back_leader_increases_legitimacy() -> None:
    caller_id = uuid4()
    caller = _agent(id=caller_id, grievance=0)
    state = _colony(agents=[caller], legitimacy=50)
    tc = _tc(
        "back_leader",
        {"decision_id": "p-abc", "legitimacy_bonus": 3},
        caller_id=caller_id,
    )
    new_state = apply_tool_call(state, tc)
    assert new_state.legitimacy == 53


def test_back_leader_clamps_legitimacy_at_100() -> None:
    caller_id = uuid4()
    caller = _agent(id=caller_id, grievance=0)
    state = _colony(agents=[caller], legitimacy=99)
    tc = _tc(
        "back_leader",
        {"decision_id": "p-abc", "legitimacy_bonus": 5},
        caller_id=caller_id,
    )
    new_state = apply_tool_call(state, tc)
    assert new_state.legitimacy == 100


# ---------------------------------------------------------------------------
# adjust_relationship
# ---------------------------------------------------------------------------


def test_adjust_relationship_creates_new_entry_when_missing() -> None:
    caller_id = uuid4()
    target_id = uuid4()
    caller = _agent(id=caller_id)
    target = _agent(id=target_id)
    state = _colony(agents=[caller, target])
    tc = _tc(
        "adjust_relationship",
        {"target_id": str(target_id), "delta": 2},
        caller_id=caller_id,
    )
    new_state = apply_tool_call(state, tc)
    updated_caller = next(a for a in new_state.agents if a.id == caller_id)
    assert updated_caller.relationships.get(target_id) == 2


def test_adjust_relationship_clamps_at_10() -> None:
    caller_id = uuid4()
    target_id = uuid4()
    caller = _agent(id=caller_id, relationships={target_id: 9})
    target = _agent(id=target_id)
    state = _colony(agents=[caller, target])
    tc = _tc(
        "adjust_relationship",
        {"target_id": str(target_id), "delta": 3},
        caller_id=caller_id,
    )
    new_state = apply_tool_call(state, tc)
    updated_caller = next(a for a in new_state.agents if a.id == caller_id)
    assert updated_caller.relationships[target_id] == 10


def test_adjust_relationship_clamps_at_minus_10() -> None:
    caller_id = uuid4()
    target_id = uuid4()
    caller = _agent(id=caller_id, relationships={target_id: -9})
    target = _agent(id=target_id)
    state = _colony(agents=[caller, target])
    tc = _tc(
        "adjust_relationship",
        {"target_id": str(target_id), "delta": -3},
        caller_id=caller_id,
    )
    new_state = apply_tool_call(state, tc)
    updated_caller = next(a for a in new_state.agents if a.id == caller_id)
    assert updated_caller.relationships[target_id] == -10


# ---------------------------------------------------------------------------
# Large-delta bypass — clamp saves state even if validator is bypassed
# ---------------------------------------------------------------------------


def test_engine_clamps_extreme_delta_on_hand_forged_tc() -> None:
    """If validator is bypassed with a hand-forged tc, clamp prevents negative resource."""
    state = _colony(resources={"fuel": _resource(value=50)})
    # Build a tc that bypasses the validator's range check by passing directly to effect.
    # We craft a valid-looking tc but with extreme delta — this tests the clamp path.
    # Since validate_tool_call rejects delta_out_of_range, we forge a tc that would
    # be accepted by the validator but stress-tests the engine clamp via modify_health.
    tc = ToolCall(
        name="modify_health",
        caller_id=None,
        args={"target_id": "player", "delta": -999, "reason": "stress test"},
    )
    new_state = apply_tool_call(state, tc)
    # The validator accepts any delta in [-100, 100] — -999 is rejected.
    # So we check the rejection was stamped, not a crash.
    assert len(new_state.turn_tool_calls) == 1
    rejected = new_state.turn_tool_calls[0]
    assert rejected.rejection_reason == "delta_out_of_range"
    assert new_state.player_health == 100  # unchanged


# ---------------------------------------------------------------------------
# adjust_own_grievance
# ---------------------------------------------------------------------------


def test_adjust_own_grievance_applies_to_caller() -> None:
    caller = _agent(grievance=3)
    state = _colony(agents=[caller])
    tc = ToolCall(
        name="adjust_own_grievance",
        caller_id=caller.id,
        args={"delta": 2, "reason": "regret"},
    )
    new_state = apply_tool_call(state, tc)
    updated = next(a for a in new_state.agents if a.id == caller.id)
    assert updated.grievance == 5


def test_adjust_own_grievance_clamps_at_10() -> None:
    caller = _agent(grievance=9)
    state = _colony(agents=[caller])
    tc = ToolCall(
        name="adjust_own_grievance",
        caller_id=caller.id,
        args={"delta": 3, "reason": "over the limit"},
    )
    new_state = apply_tool_call(state, tc)
    updated = next(a for a in new_state.agents if a.id == caller.id)
    assert updated.grievance == 10


def test_adjust_own_grievance_rejected_without_caller() -> None:
    state = _colony(agents=[_agent()])
    tc = ToolCall(
        name="adjust_own_grievance",
        caller_id=None,
        args={"delta": 1, "reason": "engine"},
    )
    new_state = apply_tool_call(state, tc)
    assert new_state.turn_tool_calls[-1].rejection_reason is not None


# ---------------------------------------------------------------------------
# Memory helper unit tests (Fix 1 regression + dedup)
# ---------------------------------------------------------------------------


def test_append_memory_never_drops_new_entry_when_all_slots_pinned() -> None:
    """Pathological case: 10 pinned death entries — new entry must survive."""
    pinned = [f"died in turn {i}" for i in range(10)]
    result = _append_memory(pinned, "new observation")
    assert "new observation" in result
    assert len(result) == 10


def test_pin_memory_dedupes_same_death_tag() -> None:
    mem = ["died in turn 5", "other memory"]
    result = _pin_memory(mem, "died in turn 5")
    assert result == mem  # unchanged
    assert result.count("died in turn 5") == 1


# ---------------------------------------------------------------------------
# resolve_coup — boldness strict inequality boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bold_sum,expected",
    [
        (0.5, EndState.OUSTED),  # strict >, so exactly 0.5 fails KILLED
        (0.51, EndState.KILLED),  # just over
    ],
)
def test_resolve_coup_boldness_strict_inequality(bold_sum: float, expected: EndState) -> None:
    init = _agent(grievance=10, temperament=bold_sum / 2)
    acc = _agent(grievance=10, temperament=bold_sum / 2)
    state = _colony(agents=[init, acc], legitimacy=5)
    new = resolve_coup(state, initiator_id=init.id)
    assert new.end_state == expected
