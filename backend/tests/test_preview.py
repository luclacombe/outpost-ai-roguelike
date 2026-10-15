"""Engine-rendered previews (spec §4.1)."""

from __future__ import annotations

from uuid import UUID

import pytest

from app.game import engine
from app.game.generator import generate_run
from app.game.preview import (
    compact_state,
    outcome_from_calls,
    render_close,
    render_effects,
    simulate_option,
    state_from_compact,
)
from app.game.schemas import ColonyState, Proposal, ProposalFlavor, ToolCall
from app.game.setting_loader import load_setting

USER = UUID("bbbbbbbb-0000-0000-0000-000000000002")
ARCTIC = load_setting("arctic_station_v1")


@pytest.fixture
def colony() -> ColonyState:
    return generate_run(user_id=USER, setting_id="arctic_station_v1", seed=7).model_copy(
        update={"turn_number": 4, "legitimacy": 60}
    )


def _tc(name: str, **args: object) -> ToolCall:
    return ToolCall(name=name, caller_id=None, args={"reason": "r", **args})


def _p(pid: str, flavor: ProposalFlavor, calls: list[ToolCall], by: UUID | None = None) -> Proposal:
    return Proposal(
        id=pid,
        proposed_by_agent_id=by,
        flavor=flavor,
        name=pid,
        description="d",
        effects_preview=[],
        tool_calls_on_pick=calls,
    )


def _set(colony: ColonyState, rid: str, value: int) -> ColonyState:
    res = colony.resources[rid]
    return colony.model_copy(
        update={"resources": {**colony.resources, rid: res.model_copy(update={"value": value})}}
    )


def test_resource_lines_in_yaml_order_with_true_minus(colony: ColonyState) -> None:
    picked = _p(
        "d",
        ProposalFlavor.DOMINANT,
        [
            _tc("modify_resource", resource_id="food", delta=5),
            _tc("modify_resource", resource_id="fuel", delta=-10),
        ],
    )
    lines = render_effects(ARCTIC, simulate_option(colony, picked, [picked]))
    assert lines == ["fuel −10", "food +5"]


def test_clamped_gain_renders_at_cap(colony: ColonyState) -> None:
    colony = _set(colony, "fuel", colony.resources["fuel"].max - 2)
    picked = _p(
        "d", ProposalFlavor.DOMINANT, [_tc("modify_resource", resource_id="fuel", delta=10)]
    )
    assert render_effects(ARCTIC, simulate_option(colony, picked, [picked])) == ["fuel +2 (at cap)"]


def test_overrule_legitimacy_single_part(colony: ColonyState) -> None:
    dom_by = colony.agents[0].id
    dom = _p(
        "d",
        ProposalFlavor.DOMINANT,
        [_tc("modify_resource", resource_id="fuel", delta=-1)],
        by=dom_by,
    )
    mor = _p("m", ProposalFlavor.MORAL, [_tc("modify_resource", resource_id="food", delta=3)])
    lines = render_effects(ARCTIC, simulate_option(colony, mor, [dom, mor]))
    name = colony.agents[0].name
    assert lines == ["food +3", f"legitimacy −2 (overrules {name})", f"{name} grievance +2"]


def test_legitimacy_multiple_parts(colony: ColonyState) -> None:
    target = colony.agents[1]
    picked = _p(
        "w",
        ProposalFlavor.WILDCARD,
        [
            _tc("modify_health", target_id=str(target.id), delta=-60),
            _tc("modify_resource", resource_id="fuel", delta=-40),
        ],
    )
    lines = render_effects(ARCTIC, simulate_option(colony, picked, [picked]))
    assert lines[0] == "fuel −40"
    assert lines[1] == f"legitimacy −17 (severe harm to {target.name} −12, heavy fuel drain −5)"
    assert lines[2] == f"{target.name} −60 health"


def test_render_legitimacy_partial_clamp_at_floor(colony: ColonyState) -> None:
    colony = colony.model_copy(update={"legitimacy": 1})
    dom = _p("d", ProposalFlavor.DOMINANT, [], by=colony.agents[0].id)
    mor = _p("m", ProposalFlavor.MORAL, [_tc("modify_resource", resource_id="food", delta=1)])
    lines = render_effects(ARCTIC, simulate_option(colony, mor, [dom, mor]))
    assert f"legitimacy −1 (overrules {colony.agents[0].name})" in lines


def test_legitimacy_all_parts_clamped_reads_unchanged(colony: ColonyState) -> None:
    colony = colony.model_copy(update={"legitimacy": 0})
    dom = _p("d", ProposalFlavor.DOMINANT, [], by=colony.agents[0].id)
    mor = _p("m", ProposalFlavor.MORAL, [_tc("modify_resource", resource_id="food", delta=1)])
    lines = render_effects(ARCTIC, simulate_option(colony, mor, [dom, mor]))
    assert "legitimacy unchanged (at floor)" in lines


def test_lethal_advisor_hit_renders_dies(colony: ColonyState) -> None:
    target = colony.agents[2]
    colony = colony.model_copy(
        update={
            "agents": [
                a.model_copy(update={"health": 30}) if a.id == target.id else a
                for a in colony.agents
            ]
        }
    )
    picked = _p(
        "w", ProposalFlavor.WILDCARD, [_tc("modify_health", target_id=str(target.id), delta=-40)]
    )
    lines = render_effects(ARCTIC, simulate_option(colony, picked, [picked]))
    assert f"{target.name} −30 health, dies" in lines


def test_lethal_player_hit_renders_run_ends(colony: ColonyState) -> None:
    colony = colony.model_copy(update={"player_health": 20})
    picked = _p("w", ProposalFlavor.WILDCARD, [_tc("modify_health", target_id="player", delta=-30)])
    lines = render_effects(ARCTIC, simulate_option(colony, picked, [picked]))
    assert lines == ["you −20 health", "run ends"]


def test_no_own_effect_reads_no_immediate_effect(colony: ColonyState) -> None:
    dom = _p("d", ProposalFlavor.DOMINANT, [], by=colony.agents[0].id)
    mor = _p("m", ProposalFlavor.MORAL, [])
    lines = render_effects(ARCTIC, simulate_option(colony, mor, [dom, mor]))
    assert lines[0] == "no immediate effect"
    assert lines[1].startswith("legitimacy −2")


def test_status_change_renders(colony: ColonyState) -> None:
    target = colony.agents[3]
    picked = _p(
        "d",
        ProposalFlavor.DOMINANT,
        [
            _tc("set_advisor_status", target_id=str(target.id), new_status="DETAINED"),
        ],
    )
    lines = render_effects(ARCTIC, simulate_option(colony, picked, [picked]))
    assert lines == [f"{target.name} detained"]


def test_rejected_nested_call_is_reported(colony: ColonyState) -> None:
    picked = _p(
        "d", ProposalFlavor.DOMINANT, [_tc("modify_resource", resource_id="water", delta=-5)]
    )
    out = simulate_option(colony, picked, [picked])
    assert [tc.rejection_reason for tc in out.rejected] == ["unknown_resource"]
    assert render_effects(ARCTIC, out) == ["no immediate effect"]


def test_simulate_does_not_mutate_input(colony: ColonyState) -> None:
    before = colony.model_dump()
    picked = _p(
        "d", ProposalFlavor.DOMINANT, [_tc("modify_resource", resource_id="fuel", delta=-10)]
    )
    simulate_option(colony, picked, [picked])
    assert colony.model_dump() == before


def test_outcome_from_recorded_calls_renders_identically(colony: ColonyState) -> None:
    dom = _p(
        "d",
        ProposalFlavor.DOMINANT,
        [_tc("modify_resource", resource_id="fuel", delta=-10)],
        by=colony.agents[0].id,
    )
    mor = _p("m", ProposalFlavor.MORAL, [_tc("modify_resource", resource_id="food", delta=4)])
    preview = render_effects(ARCTIC, simulate_option(colony, mor, [dom, mor]))
    derived = engine.derive_resolution_consequences(colony, mor, [dom, mor])
    after = engine.apply_tool_calls(
        colony, engine.stamp_phase([*mor.tool_calls_on_pick, *derived], "resolve")
    )
    recorded = after.turn_tool_calls[len(colony.turn_tool_calls) :]
    assert render_effects(ARCTIC, outcome_from_calls(recorded, colony)) == preview


def test_render_close() -> None:
    assert render_close() == ["−1 legitimacy at day's end"]


def test_compact_state_round_trips_stats(colony: ColonyState) -> None:
    restored = state_from_compact(compact_state(colony))
    assert restored.resources == colony.resources
    assert restored.legitimacy == colony.legitimacy
    assert [a.name for a in restored.agents] == [a.name for a in colony.agents]
    assert restored.event_log == []
