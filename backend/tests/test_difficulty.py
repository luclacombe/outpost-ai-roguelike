"""Unit tests for app.game.difficulty — rails, floors, and wealth scaling.

All tests synchronous; no I/O, no LLM, no DB.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.game.difficulty import (
    apply_early_turn_floors,
    validate_event_pool_for_setting,
    wealth_scale_factor,
)
from app.game.schemas import (
    Agent,
    ColonyState,
    Quadrant,
    Resource,
    ToolCall,
)
from app.game.setting_loader import (
    AdvisorArchetype,
    EventCategory,
    ResourceDef,
    Setting,
    load_setting,
)

# ---------------------------------------------------------------------------
# Shared fabricators
# ---------------------------------------------------------------------------

GAME_ID = UUID("aaaaaaaa-0000-0000-0000-000000000010")
USER_ID = UUID("bbbbbbbb-0000-0000-0000-000000000020")


def _agent(health: int = 100, is_alive: bool = True) -> Agent:
    return Agent(
        id=uuid4(),
        game_id=GAME_ID,
        name="A",
        role="Advisor",
        archetype_quadrant=Quadrant.ACCOUNTANT,
        ethics=0.0,
        temperament=0.0,
        loyalty=0.0,
        portrait_seed="seed",
        health=health,
        morale=50,
        grievance=0,
        is_alive=is_alive,
    )


def _resource(rid: str = "fuel", value: int = 100, max_: int = 100) -> Resource:
    return Resource(
        id=rid,
        name=rid.capitalize(),
        icon="X",
        value=value,
        max=max_,
        critical_threshold=20,
    )


def _colony(
    turn_number: int = 1,
    resources: dict[str, Resource] | None = None,
    agents: list[Agent] | None = None,
    legitimacy: int = 60,
) -> ColonyState:
    return ColonyState(
        game_id=GAME_ID,
        user_id=USER_ID,
        setting_id="arctic_station_v1",
        seed=42,
        turn_number=turn_number,
        max_turns=10,
        resources=resources if resources is not None else {"fuel": _resource()},
        agents=agents if agents is not None else [_agent()],
        legitimacy=legitimacy,
        player_health=100,
        consequence_seeds=[],
        event_log=[],
        current_event=None,
        turn_proposals=[],
        turn_tool_calls=[],
    )


def _minimal_archetypes() -> list[AdvisorArchetype]:
    return [
        AdvisorArchetype(
            quadrant=q,
            role_name=f"Role {q.value}",
            name_pool=["Alpha", "Bravo", "Charlie"],
            description_template="A capable advisor.",
            verbal_tic="Speaks plainly.",
            portrait_options={"clothing_color": ["aabbcc"], "hair_color": ["112233"]},
        )
        for q in Quadrant
    ]


def _minimal_resources() -> list[ResourceDef]:
    return [
        ResourceDef(
            id=f"res_{i}",
            name=f"Res {i}",
            icon="i",
            starting=50,
            max=100,
            critical_threshold=20,
        )
        for i in range(5)
    ]


def _category(
    *,
    cid: str = "test_event",
    weight: float = 1.0,
    tags: list[str] | None = None,
    severity: int = 2,
    min_turn: int = 1,
    can_kill_agent: bool = False,
    prompt_template: str = "A thing. Details.",
) -> EventCategory:
    return EventCategory(
        id=cid,
        weight=weight,
        tags=tags or ["test"],
        severity=severity,
        min_turn=min_turn,
        prompt_template=prompt_template,
        choice_templates=["A.", "B.", "C."],
        can_kill_agent=can_kill_agent,
    )


def _fe_category() -> EventCategory:
    return _category(
        cid="forced_escalation_test",
        tags=["forced_escalation", "moral"],
        severity=5,
        min_turn=5,
        can_kill_agent=True,
        weight=10.0,
    )


def _setting_with(categories: list[EventCategory]) -> Setting:
    filler_needed = max(0, 12 - len(categories))
    filler = [
        _category(cid=f"filler_{i}", weight=0.1, severity=1, min_turn=99)
        for i in range(filler_needed)
    ]
    return Setting(
        id="test_setting_v1",
        name="Test",
        lore="L" * 120,
        palette_accent="#1a2b3c",
        narrative_voice="Terse.",
        player_title="Director",
        resources=_minimal_resources(),
        advisor_archetypes=_minimal_archetypes(),
        event_categories=[*categories, *filler],
    )


# ---------------------------------------------------------------------------
# validate_event_pool_for_setting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "setting_id",
    ["arctic_station_v1", "orbital_colony_v1", "desert_outpost_v1"],
)
def test_validate_pool_accepts_launch_settings(setting_id: str) -> None:
    setting = load_setting(setting_id)
    errors = validate_event_pool_for_setting(setting)
    assert errors == [], f"{setting_id} should pass, got: {errors}"


def test_validate_pool_rejects_can_kill_before_turn_4() -> None:
    # Bypass EventCategory schema AND Setting schema via model_construct so we
    # can build an illegal category for the rails to catch.
    bad = EventCategory.model_construct(
        id="bad_event",
        weight=1.0,
        tags=["test"],
        severity=3,
        min_turn=2,
        prompt_template="x",
        choice_templates=["A.", "B.", "C."],
        can_kill_agent=True,
    )
    base = _setting_with([_fe_category()])
    # model_construct skips validation entirely; copy fields from a valid base.
    setting = Setting.model_construct(
        id=base.id,
        name=base.name,
        lore=base.lore,
        palette_accent=base.palette_accent,
        narrative_voice=base.narrative_voice,
        resources=base.resources,
        advisor_archetypes=base.advisor_archetypes,
        event_categories=[bad, *base.event_categories],
    )
    errors = validate_event_pool_for_setting(setting)
    assert any("can_kill_agent" in e and "min_turn" in e for e in errors), errors


def test_validate_pool_rejects_zero_forced_escalation_events() -> None:
    setting = _setting_with([_category(cid="only_natural", severity=2)])
    errors = validate_event_pool_for_setting(setting)
    assert any("no forced_escalation" in e for e in errors), errors


def test_validate_pool_rejects_multiple_forced_escalation() -> None:
    fe_a = _category(
        cid="forced_escalation_a",
        tags=["forced_escalation"],
        severity=5,
        min_turn=5,
        can_kill_agent=True,
    )
    fe_b = _category(
        cid="forced_escalation_b",
        tags=["forced_escalation"],
        severity=5,
        min_turn=5,
        can_kill_agent=True,
    )
    setting = _setting_with([fe_a, fe_b])
    errors = validate_event_pool_for_setting(setting)
    assert any("exactly 1 forced_escalation" in e for e in errors), errors


def test_validate_pool_rejects_duplicate_category_ids() -> None:
    dup = _category(cid="same_id", severity=2)
    dup2 = _category(cid="same_id", severity=3)
    setting = _setting_with([dup, dup2, _fe_category()])
    errors = validate_event_pool_for_setting(setting)
    assert any("duplicate event category id" in e for e in errors), errors


# ---------------------------------------------------------------------------
# apply_early_turn_floors
# ---------------------------------------------------------------------------


def _llm_tool_call(name: str, args: dict) -> ToolCall:
    # caller_id non-None => LLM-authored.
    return ToolCall(name=name, caller_id=uuid4(), args=args)


def _engine_tool_call(name: str, args: dict) -> ToolCall:
    return ToolCall(name=name, caller_id=None, args=args)


def test_apply_floors_clamps_resource_drop_turn_1() -> None:
    state = _colony(turn_number=1, resources={"fuel": _resource(value=100)})
    tc = _llm_tool_call(
        "modify_resource",
        {"resource_id": "fuel", "delta": -50, "reason": "disaster"},
    )
    out = apply_early_turn_floors(state, tc)

    # Floor is 70% of max=100 => 70. Starting=100, clamped delta = 70 - 100 = -30.
    assert out.args["delta"] == -30
    assert out.args["resource_id"] == "fuel"
    assert out.args["reason"] == "disaster"
    # id / name / caller_id preserved.
    assert out.id == tc.id
    assert out.name == tc.name
    assert out.caller_id == tc.caller_id


def test_apply_floors_preserves_delta_after_turn_3() -> None:
    state = _colony(turn_number=4, resources={"fuel": _resource(value=100)})
    tc = _llm_tool_call(
        "modify_resource",
        {"resource_id": "fuel", "delta": -50, "reason": "late-game disaster"},
    )
    out = apply_early_turn_floors(state, tc)

    # No clamp past turn 3.
    assert out.args["delta"] == -50
    assert out.model_dump() == tc.model_dump()


def test_apply_floors_blocks_lethal_agent_damage() -> None:
    target = _agent(health=50)
    state = _colony(turn_number=2, agents=[target])
    tc = _llm_tool_call(
        "modify_health",
        {"target_id": str(target.id), "delta": -100, "reason": "injury"},
    )
    out = apply_early_turn_floors(state, tc)

    # Clamped so resulting health >= 1 => delta = 1 - 50 = -49.
    assert out.args["delta"] == -49
    assert out.args["target_id"] == str(target.id)


def test_apply_floors_skips_engine_emitted_calls() -> None:
    state = _colony(turn_number=2, resources={"fuel": _resource(value=100)})
    tc = _engine_tool_call(
        "modify_resource",
        {"resource_id": "fuel", "delta": -50, "reason": "engine heuristic"},
    )
    out = apply_early_turn_floors(state, tc)

    assert out is tc or out.model_dump() == tc.model_dump()
    assert out.args["delta"] == -50


def test_apply_floors_is_pure() -> None:
    target = _agent(health=50)
    state = _colony(turn_number=2, agents=[target])
    tc = _llm_tool_call(
        "modify_health",
        {"target_id": str(target.id), "delta": -100, "reason": "injury"},
    )
    state_before = state.model_dump()
    tc_before = tc.model_dump()

    apply_early_turn_floors(state, tc)

    assert state.model_dump() == state_before
    assert tc.model_dump() == tc_before


def test_apply_floors_leaves_back_leader_bonus_untouched() -> None:
    state = _colony(turn_number=2, legitimacy=25)
    tc = _llm_tool_call(
        "back_leader",
        {"decision_id": "d1", "legitimacy_bonus": 5},
    )
    out = apply_early_turn_floors(state, tc)
    assert out.args == tc.args


def test_apply_floors_resource_drop_harmless_if_within_floor() -> None:
    state = _colony(turn_number=1, resources={"fuel": _resource(value=100)})
    tc = _llm_tool_call(
        "modify_resource",
        {"resource_id": "fuel", "delta": -10, "reason": "minor"},
    )
    out = apply_early_turn_floors(state, tc)
    # Projected value 90 >= 70 floor; no clamp.
    assert out.args["delta"] == -10


def test_apply_floors_resource_drop_against_malicious_huge_delta() -> None:
    """A -9999 delta (impossible per tool schema but validators shouldn't
    assume) is still clamped to the 70% floor."""
    state = _colony(turn_number=1, resources={"fuel": _resource(value=100)})
    tc = _llm_tool_call(
        "modify_resource",
        {"resource_id": "fuel", "delta": -9999, "reason": "malice"},
    )
    out = apply_early_turn_floors(state, tc)
    assert out.args["delta"] == -30  # max=100, floor=70, current=100 => -30


# ---------------------------------------------------------------------------
# wealth_scale_factor
# ---------------------------------------------------------------------------


def test_wealth_scale_factor_pristine() -> None:
    resources = {f"r{i}": _resource(rid=f"r{i}", value=100, max_=100) for i in range(3)}
    agents = [_agent(health=100), _agent(health=100)]
    state = _colony(resources=resources, agents=agents)

    assert wealth_scale_factor(state) == 2.0


def test_wealth_scale_factor_catastrophic() -> None:
    resources = {f"r{i}": _resource(rid=f"r{i}", value=0, max_=100) for i in range(3)}
    agents = [_agent(health=0, is_alive=False)]
    state = _colony(resources=resources, agents=agents)

    assert wealth_scale_factor(state) == 0.5


def test_wealth_scale_factor_empty_resources_neutral() -> None:
    # resources={} is not possible through normal schemas but the function
    # should behave well. We model this by passing an empty dict.
    agents = [_agent(health=100)]
    state = _colony(resources={}, agents=agents)

    # resource_stability = 1.0, agent_stability = 1.0
    # raw = 0.5 + 0.75 + 0.75 = 2.0, clamped to 2.0.
    assert wealth_scale_factor(state) == 2.0


def test_wealth_scale_factor_midgame_balanced() -> None:
    resources = {f"r{i}": _resource(rid=f"r{i}", value=50, max_=100) for i in range(3)}
    agents = [_agent(health=50), _agent(health=50)]
    state = _colony(resources=resources, agents=agents)
    # res_stab=0.5, agent_stab=0.5 => raw = 0.5 + 0.375 + 0.375 = 1.25
    assert abs(wealth_scale_factor(state) - 1.25) < 1e-9
