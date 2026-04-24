"""
Unit tests for app.game.validator — pure tool-call validation.

All tests are synchronous; no asyncio, no DB, no LLM.

Coverage target: every tool, every documented failure reason, plus happy path.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.game.schemas import (
    Agent,
    ColonyState,
    EndState,
    Proposal,
    ProposalFlavor,
    Quadrant,
    Resource,
    ToolCall,
)
from app.game.validator import ValidationResult, validate_tool_call

# ---------------------------------------------------------------------------
# Test helpers / fabricators
# ---------------------------------------------------------------------------

GAME_ID = UUID("aaaaaaaa-0000-0000-0000-000000000001")
USER_ID = UUID("bbbbbbbb-0000-0000-0000-000000000002")


def _agent(
    name: str = "Alpha",
    grievance: int = 0,
    health: int = 100,
    alive: bool = True,
    **over: object,
) -> Agent:
    return Agent(
        id=over.pop("id", uuid4()),
        game_id=GAME_ID,
        name=name,
        role="Advisor",
        archetype_quadrant=Quadrant.ACCOUNTANT,
        ethics=0.0,
        temperament=0.0,
        loyalty=0.0,
        portrait_seed="seed",
        health=health,
        morale=50,
        grievance=grievance,
        is_alive=alive,
        **over,
    )


def _resource(
    resource_id: str = "fuel",
    value: int = 50,
    max_val: int = 100,
    critical_threshold: int = 10,
) -> Resource:
    return Resource(
        id=resource_id,
        name=resource_id.capitalize(),
        icon="X",
        value=value,
        max=max_val,
        critical_threshold=critical_threshold,
    )


def _colony(
    agents: list[Agent] | None = None,
    resources: dict[str, Resource] | None = None,
    legitimacy: int = 60,
    player_health: int = 100,
    end_state: EndState | None = None,
    proposals: list[Proposal] | None = None,
) -> ColonyState:
    if agents is None:
        agents = []
    if resources is None:
        resources = {"fuel": _resource()}
    return ColonyState(
        game_id=GAME_ID,
        user_id=USER_ID,
        setting_id="arctic_station_v1",
        seed=42,
        turn_number=1,
        max_turns=10,
        resources=resources,
        agents=agents,
        legitimacy=legitimacy,
        player_health=player_health,
        consequence_seeds=[],
        event_log=[],
        current_event=None,
        turn_proposals=proposals or [],
        turn_tool_calls=[],
        end_state=end_state,
    )


def _tc(
    name: str,
    args: dict | None = None,
    caller_id: UUID | None = None,
) -> ToolCall:
    return ToolCall(name=name, caller_id=caller_id, args=args or {})


def _proposal(proposal_name: str) -> Proposal:
    return Proposal(
        id=str(uuid4()),
        proposed_by_agent_id=None,
        flavor=ProposalFlavor.DOMINANT,
        name=proposal_name,
        description="A proposal",
        effects_preview=[],
        tool_calls_on_pick=[],
    )


# ---------------------------------------------------------------------------
# Unknown tool
# ---------------------------------------------------------------------------


def test_unknown_tool_rejected() -> None:
    state = _colony()
    result = validate_tool_call(_tc("fly_away"), state)
    assert result.valid is False
    assert "unknown_tool" in result.reason


# ---------------------------------------------------------------------------
# modify_resource
# ---------------------------------------------------------------------------


def test_modify_resource_valid() -> None:
    state = _colony(resources={"fuel": _resource(value=50)})
    tc = _tc("modify_resource", {"resource_id": "fuel", "delta": -10, "reason": "used up"})
    result = validate_tool_call(tc, state)
    assert result.valid is True


def test_modify_resource_unknown_resource() -> None:
    state = _colony()
    tc = _tc("modify_resource", {"resource_id": "unobtanium", "delta": 1, "reason": "test"})
    result = validate_tool_call(tc, state)
    assert result.valid is False
    assert "unknown_resource" in result.reason


def test_modify_resource_would_underflow() -> None:
    state = _colony(resources={"fuel": _resource(value=5)})
    tc = _tc("modify_resource", {"resource_id": "fuel", "delta": -10, "reason": "drained"})
    result = validate_tool_call(tc, state)
    assert result.valid is False
    assert "would_underflow" in result.reason


def test_modify_resource_would_overflow() -> None:
    state = _colony(resources={"fuel": _resource(value=95)})
    tc = _tc("modify_resource", {"resource_id": "fuel", "delta": 10, "reason": "refuelled"})
    result = validate_tool_call(tc, state)
    assert result.valid is False
    assert "would_overflow" in result.reason


def test_modify_resource_delta_out_of_range() -> None:
    state = _colony(resources={"fuel": _resource(value=50)})
    tc = _tc("modify_resource", {"resource_id": "fuel", "delta": 51, "reason": "big"})
    result = validate_tool_call(tc, state)
    assert result.valid is False
    assert "delta_out_of_range" in result.reason


def test_modify_resource_missing_reason() -> None:
    state = _colony(resources={"fuel": _resource(value=50)})
    tc = _tc("modify_resource", {"resource_id": "fuel", "delta": 1})
    result = validate_tool_call(tc, state)
    assert result.valid is False
    assert "missing_reason" in result.reason


# ---------------------------------------------------------------------------
# modify_health
# ---------------------------------------------------------------------------


def test_modify_health_valid_player() -> None:
    state = _colony(player_health=80)
    tc = _tc("modify_health", {"target_id": "player", "delta": -10, "reason": "injured"})
    result = validate_tool_call(tc, state)
    assert result.valid is True


def test_modify_health_valid_alive_agent() -> None:
    agent = _agent()
    state = _colony(agents=[agent])
    tc = _tc("modify_health", {"target_id": str(agent.id), "delta": -20, "reason": "fight"})
    result = validate_tool_call(tc, state)
    assert result.valid is True


def test_modify_health_unknown_target() -> None:
    state = _colony()
    tc = _tc("modify_health", {"target_id": str(uuid4()), "delta": -5, "reason": "hit"})
    result = validate_tool_call(tc, state)
    assert result.valid is False
    assert "unknown_target" in result.reason


def test_modify_health_target_already_dead_agent() -> None:
    agent = _agent(alive=False, health=0)
    state = _colony(agents=[agent])
    tc = _tc("modify_health", {"target_id": str(agent.id), "delta": -1, "reason": "overkill"})
    result = validate_tool_call(tc, state)
    assert result.valid is False
    assert "target_already_dead" in result.reason


def test_modify_health_target_already_dead_player() -> None:
    state = _colony(player_health=0)
    tc = _tc("modify_health", {"target_id": "player", "delta": -1, "reason": "overkill"})
    result = validate_tool_call(tc, state)
    assert result.valid is False
    assert "target_already_dead" in result.reason


def test_modify_health_positive_delta_on_dead_agent_is_allowed() -> None:
    """The validator stays permissive on positive deltas to dead targets.
    Reviving is absurd but not the validator's concern — the engine clamps at 0."""
    agent = _agent(alive=False, health=0)
    state = _colony(agents=[agent])
    tc = _tc("modify_health", {"target_id": str(agent.id), "delta": 10, "reason": "healing"})
    result = validate_tool_call(tc, state)
    assert result.valid is True


# ---------------------------------------------------------------------------
# adjust_grievance
# ---------------------------------------------------------------------------

CALLER_A_ID = uuid4()
TARGET_B_ID = uuid4()


def test_adjust_grievance_valid() -> None:
    caller = _agent(id=CALLER_A_ID, grievance=2)
    target = _agent(id=TARGET_B_ID, grievance=3)
    state = _colony(agents=[caller, target])
    tc = _tc(
        "adjust_grievance",
        {"target_id": str(TARGET_B_ID), "delta": 2, "reason": "betrayed"},
        caller_id=CALLER_A_ID,
    )
    result = validate_tool_call(tc, state)
    assert result.valid is True


def test_adjust_grievance_unknown_target() -> None:
    agent = _agent(id=CALLER_A_ID)
    state = _colony(agents=[agent])
    tc = _tc(
        "adjust_grievance",
        {"target_id": str(uuid4()), "delta": 1, "reason": "annoyed"},
        caller_id=CALLER_A_ID,
    )
    result = validate_tool_call(tc, state)
    assert result.valid is False
    assert "unknown_target" in result.reason


def test_adjust_grievance_cannot_self_target() -> None:
    self_id = uuid4()
    agent = _agent(id=self_id)
    state = _colony(agents=[agent])
    tc = _tc(
        "adjust_grievance",
        {"target_id": str(self_id), "delta": 1, "reason": "self-pity"},
        caller_id=self_id,
    )
    result = validate_tool_call(tc, state)
    assert result.valid is False
    assert "cannot_self_target" in result.reason


def test_adjust_grievance_target_dead() -> None:
    caller = _agent(id=CALLER_A_ID)
    dead = _agent(id=TARGET_B_ID, alive=False, health=0)
    state = _colony(agents=[caller, dead])
    tc = _tc(
        "adjust_grievance",
        {"target_id": str(TARGET_B_ID), "delta": 1, "reason": "vengeance"},
        caller_id=CALLER_A_ID,
    )
    result = validate_tool_call(tc, state)
    assert result.valid is False
    assert "target_dead" in result.reason


@pytest.mark.parametrize("delta", [4, -4])
def test_adjust_grievance_delta_out_of_range(delta: int) -> None:
    caller = _agent(id=CALLER_A_ID)
    target = _agent(id=TARGET_B_ID)
    state = _colony(agents=[caller, target])
    tc = _tc(
        "adjust_grievance",
        {"target_id": str(TARGET_B_ID), "delta": delta, "reason": "big shift"},
        caller_id=CALLER_A_ID,
    )
    result = validate_tool_call(tc, state)
    assert result.valid is False
    assert "delta_out_of_range" in result.reason


# ---------------------------------------------------------------------------
# speak_to_agent
# ---------------------------------------------------------------------------


def test_speak_to_agent_valid() -> None:
    caller_id = uuid4()
    caller = _agent(id=caller_id)
    target_id = uuid4()
    target = _agent(id=target_id)
    state = _colony(agents=[caller, target])
    tc = _tc(
        "speak_to_agent",
        {"target_id": str(target_id), "message": "Watch your back."},
        caller_id=caller_id,
    )
    result = validate_tool_call(tc, state)
    assert result.valid is True


def test_speak_to_agent_missing_caller() -> None:
    target_id = uuid4()
    target = _agent(id=target_id)
    state = _colony(agents=[target])
    tc = _tc(
        "speak_to_agent",
        {"target_id": str(target_id), "message": "Hello"},
        caller_id=None,
    )
    result = validate_tool_call(tc, state)
    assert result.valid is False
    assert "missing_caller" in result.reason


def test_speak_to_agent_target_dead() -> None:
    caller_id = uuid4()
    caller = _agent(id=caller_id)
    dead_id = uuid4()
    dead = _agent(id=dead_id, alive=False, health=0)
    state = _colony(agents=[caller, dead])
    tc = _tc(
        "speak_to_agent",
        {"target_id": str(dead_id), "message": "Can you hear me?"},
        caller_id=caller_id,
    )
    result = validate_tool_call(tc, state)
    assert result.valid is False
    assert "target_unknown_or_dead" in result.reason


# ---------------------------------------------------------------------------
# adjust_own_grievance
# ---------------------------------------------------------------------------


def test_adjust_own_grievance_valid() -> None:
    caller_id = uuid4()
    caller = _agent(id=caller_id, grievance=3)
    state = _colony(agents=[caller])
    tc = _tc(
        "adjust_own_grievance",
        {"delta": 2, "reason": "angered"},
        caller_id=caller_id,
    )
    result = validate_tool_call(tc, state)
    assert result.valid is True


def test_adjust_own_grievance_missing_caller() -> None:
    state = _colony()
    tc = _tc("adjust_own_grievance", {"delta": 1, "reason": "upset"}, caller_id=None)
    result = validate_tool_call(tc, state)
    assert result.valid is False
    assert "missing_caller" in result.reason


def test_adjust_own_grievance_caller_dead() -> None:
    dead_id = uuid4()
    dead = _agent(id=dead_id, alive=False, health=0)
    state = _colony(agents=[dead])
    tc = _tc(
        "adjust_own_grievance",
        {"delta": 1, "reason": "posthumous"},
        caller_id=dead_id,
    )
    result = validate_tool_call(tc, state)
    assert result.valid is False
    assert "caller_dead" in result.reason


@pytest.mark.parametrize("delta", [4, -4])
def test_adjust_own_grievance_delta_out_of_range(delta: int) -> None:
    caller_id = uuid4()
    caller = _agent(id=caller_id)
    state = _colony(agents=[caller])
    tc = _tc(
        "adjust_own_grievance",
        {"delta": delta, "reason": "extreme"},
        caller_id=caller_id,
    )
    result = validate_tool_call(tc, state)
    assert result.valid is False
    assert "delta_out_of_range" in result.reason


# ---------------------------------------------------------------------------
# adjust_relationship
# ---------------------------------------------------------------------------


def test_adjust_relationship_valid() -> None:
    caller_id = uuid4()
    target_id = uuid4()
    caller = _agent(id=caller_id)
    target = _agent(id=target_id)
    state = _colony(agents=[caller, target])
    tc = _tc(
        "adjust_relationship",
        {"target_id": str(target_id), "delta": 1},
        caller_id=caller_id,
    )
    result = validate_tool_call(tc, state)
    assert result.valid is True


def test_adjust_relationship_cannot_self_target() -> None:
    self_id = uuid4()
    agent = _agent(id=self_id)
    state = _colony(agents=[agent])
    tc = _tc(
        "adjust_relationship",
        {"target_id": str(self_id), "delta": 1},
        caller_id=self_id,
    )
    result = validate_tool_call(tc, state)
    assert result.valid is False
    assert "cannot_self_target" in result.reason


@pytest.mark.parametrize("delta", [4, -4])
def test_adjust_relationship_delta_out_of_range(delta: int) -> None:
    caller_id = uuid4()
    target_id = uuid4()
    caller = _agent(id=caller_id)
    target = _agent(id=target_id)
    state = _colony(agents=[caller, target])
    tc = _tc(
        "adjust_relationship",
        {"target_id": str(target_id), "delta": delta},
        caller_id=caller_id,
    )
    result = validate_tool_call(tc, state)
    assert result.valid is False
    assert "delta_out_of_range" in result.reason


# ---------------------------------------------------------------------------
# remember
# ---------------------------------------------------------------------------


def test_remember_valid() -> None:
    caller_id = uuid4()
    caller = _agent(id=caller_id)
    state = _colony(agents=[caller])
    tc = _tc("remember", {"fact": "Player betrayed us on turn 3."}, caller_id=caller_id)
    result = validate_tool_call(tc, state)
    assert result.valid is True


def test_remember_missing_caller() -> None:
    state = _colony()
    tc = _tc("remember", {"fact": "Something happened."}, caller_id=None)
    result = validate_tool_call(tc, state)
    assert result.valid is False
    assert "missing_caller" in result.reason


def test_remember_fact_too_long() -> None:
    caller_id = uuid4()
    caller = _agent(id=caller_id)
    state = _colony(agents=[caller])
    tc = _tc("remember", {"fact": "x" * 201}, caller_id=caller_id)
    result = validate_tool_call(tc, state)
    assert result.valid is False
    assert "fact_too_long" in result.reason


# ---------------------------------------------------------------------------
# initiate_coup
# ---------------------------------------------------------------------------


def test_initiate_coup_valid() -> None:
    caller_id = uuid4()
    supporter_id = uuid4()
    caller = _agent(id=caller_id, grievance=9)
    supporter = _agent(id=supporter_id, grievance=7)
    state = _colony(agents=[caller, supporter])
    tc = _tc("initiate_coup", {}, caller_id=caller_id)
    result = validate_tool_call(tc, state)
    assert result.valid is True


def test_initiate_coup_grievance_too_low() -> None:
    caller_id = uuid4()
    supporter_id = uuid4()
    caller = _agent(id=caller_id, grievance=8)
    supporter = _agent(id=supporter_id, grievance=7)
    state = _colony(agents=[caller, supporter])
    tc = _tc("initiate_coup", {}, caller_id=caller_id)
    result = validate_tool_call(tc, state)
    assert result.valid is False
    assert "grievance_too_low" in result.reason


def test_initiate_coup_no_faction_support() -> None:
    caller_id = uuid4()
    bystander_id = uuid4()
    caller = _agent(id=caller_id, grievance=9)
    bystander = _agent(id=bystander_id, grievance=6)  # just below threshold
    state = _colony(agents=[caller, bystander])
    tc = _tc("initiate_coup", {}, caller_id=caller_id)
    result = validate_tool_call(tc, state)
    assert result.valid is False
    assert "no_faction_support" in result.reason


def test_initiate_coup_run_already_ended() -> None:
    caller_id = uuid4()
    supporter_id = uuid4()
    caller = _agent(id=caller_id, grievance=9)
    supporter = _agent(id=supporter_id, grievance=7)
    state = _colony(agents=[caller, supporter], end_state=EndState.ENDURED)
    tc = _tc("initiate_coup", {}, caller_id=caller_id)
    result = validate_tool_call(tc, state)
    assert result.valid is False
    assert "run_already_ended" in result.reason


# ---------------------------------------------------------------------------
# back_leader
# ---------------------------------------------------------------------------


def test_back_leader_valid() -> None:
    caller_id = uuid4()
    caller = _agent(id=caller_id, grievance=0)
    state = _colony(agents=[caller])
    tc = _tc(
        "back_leader",
        {"decision_id": "proposal-abc", "legitimacy_bonus": 3},
        caller_id=caller_id,
    )
    result = validate_tool_call(tc, state)
    assert result.valid is True


def test_back_leader_grievance_too_high() -> None:
    caller_id = uuid4()
    caller = _agent(id=caller_id, grievance=3)
    state = _colony(agents=[caller])
    tc = _tc(
        "back_leader",
        {"decision_id": "proposal-abc", "legitimacy_bonus": 2},
        caller_id=caller_id,
    )
    result = validate_tool_call(tc, state)
    assert result.valid is False
    assert "grievance_too_high" in result.reason


def test_back_leader_invalid_legitimacy_bonus_zero() -> None:
    caller_id = uuid4()
    caller = _agent(id=caller_id, grievance=1)
    state = _colony(agents=[caller])
    tc = _tc(
        "back_leader",
        {"decision_id": "proposal-abc", "legitimacy_bonus": 0},
        caller_id=caller_id,
    )
    result = validate_tool_call(tc, state)
    assert result.valid is False
    assert "invalid_legitimacy_bonus" in result.reason


def test_back_leader_invalid_legitimacy_bonus_too_high() -> None:
    caller_id = uuid4()
    caller = _agent(id=caller_id, grievance=1)
    state = _colony(agents=[caller])
    tc = _tc(
        "back_leader",
        {"decision_id": "proposal-abc", "legitimacy_bonus": 6},
        caller_id=caller_id,
    )
    result = validate_tool_call(tc, state)
    assert result.valid is False
    assert "invalid_legitimacy_bonus" in result.reason


# ---------------------------------------------------------------------------
# propose_action
# ---------------------------------------------------------------------------


def test_propose_action_valid() -> None:
    state = _colony()
    tc = _tc(
        "propose_action",
        {
            "name": "Full power",
            "description": "Run the core at capacity.",
            "flavor": "DOMINANT",
        },
    )
    result = validate_tool_call(tc, state)
    assert result.valid is True


def test_propose_action_duplicate_proposal() -> None:
    existing = _proposal("Full power")
    state = _colony(proposals=[existing])
    tc = _tc(
        "propose_action",
        {
            "name": "  full power  ",  # strips + case-insensitive
            "description": "Different description.",
            "flavor": "MORAL",
        },
    )
    result = validate_tool_call(tc, state)
    assert result.valid is False
    assert "duplicate_proposal" in result.reason


def test_propose_action_invalid_flavor() -> None:
    state = _colony()
    tc = _tc(
        "propose_action",
        {"name": "Something", "description": "A description.", "flavor": "RANDOM"},
    )
    result = validate_tool_call(tc, state)
    assert result.valid is False
    assert "invalid_flavor" in result.reason


# ---------------------------------------------------------------------------
# ValidationResult dataclass sanity
# ---------------------------------------------------------------------------


def test_validation_result_valid_has_no_reason() -> None:
    r = ValidationResult(valid=True)
    assert r.reason is None


def test_validation_result_invalid_has_reason() -> None:
    r = ValidationResult(valid=False, reason="something_wrong")
    assert r.valid is False
    assert r.reason == "something_wrong"
