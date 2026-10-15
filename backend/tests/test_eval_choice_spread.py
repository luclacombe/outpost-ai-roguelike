"""Choice-spread oracle: per-option engine outcomes at a paused turn."""

from __future__ import annotations

from uuid import UUID

import pytest

from app.eval.choice_spread import compute_choice_spread, summarize_choice_spread
from app.game.generator import generate_run
from app.game.schemas import (
    AdvisorStatus,
    ColonyState,
    EndState,
    Proposal,
    ProposalFlavor,
    ToolCall,
)

USER = UUID("bbbbbbbb-0000-0000-0000-000000000002")


@pytest.fixture
def colony() -> ColonyState:
    state = generate_run(user_id=USER, setting_id="arctic_station_v1", seed=7)
    fuel = state.resources["fuel"]
    # Fuel one below max so a +5 clamps to +1.
    resources = {**state.resources, "fuel": fuel.model_copy(update={"value": fuel.max - 1})}
    return state.model_copy(update={"turn_number": 4, "legitimacy": 60, "resources": resources})


def _tc(name: str, **args: object) -> ToolCall:
    return ToolCall(name=name, caller_id=None, args={"reason": "r", **args})


def _proposal(pid: str, flavor: ProposalFlavor, calls: list[ToolCall], by: UUID | None) -> Proposal:
    return Proposal(
        id=pid,
        proposed_by_agent_id=by,
        flavor=flavor,
        name=pid,
        description="d",
        effects_preview=[],
        tool_calls_on_pick=calls,
    )


def _triple(colony: ColonyState) -> list[Proposal]:
    a0, a1, a2 = (a.id for a in colony.agents[:3])
    return [
        _proposal(
            "p_dom",
            ProposalFlavor.DOMINANT,
            [_tc("modify_resource", resource_id="fuel", delta=-10)],
            a0,
        ),
        _proposal(
            "p_mor", ProposalFlavor.MORAL, [_tc("modify_resource", resource_id="food", delta=5)], a1
        ),
        _proposal(
            "p_wld",
            ProposalFlavor.WILDCARD,
            # Overflow clamps at max.
            [_tc("modify_resource", resource_id="fuel", delta=5)],
            a2,
        ),
    ]


def test_options_report_engine_deltas(colony: ColonyState) -> None:
    out = compute_choice_spread(colony, _triple(colony))
    by_id = {o["proposal_id"]: o for o in out["options"]}

    assert by_id["p_dom"]["resource_deltas"]["fuel"] == -10
    assert by_id["p_dom"]["legitimacy_delta"] == 0
    assert by_id["p_dom"]["rejected_calls"] == []

    # Picking MORAL over a living DOMINANT proposer: grievance + legitimacy cost.
    mor = by_id["p_mor"]
    assert mor["resource_deltas"]["food"] == 5
    assert mor["legitimacy_delta"] < 0
    dom_proposer = colony.agents[0].name
    assert mor["grievance_deltas"][dom_proposer] > 0

    wld = by_id["p_wld"]
    assert wld["resource_deltas"]["fuel"] == 1
    assert wld["rejected_calls"] == []
    assert wld["n_calls"] >= 1


def test_spread_is_max_pairwise_normalized_l1(colony: ColonyState) -> None:
    out = compute_choice_spread(colony, _triple(colony))
    vecs = {o["proposal_id"]: o["vector"] for o in out["options"]}
    dists = []
    ids = list(vecs)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            dists.append(sum(abs(a - b) for a, b in zip(vecs[ids[i]], vecs[ids[j]], strict=True)))
    assert out["spread_l1_max"] == pytest.approx(max(dists))
    assert out["spread_l1_mean"] == pytest.approx(sum(dists) / len(dists))
    assert out["identical_options"] is False


def test_identical_options_have_zero_spread(colony: ColonyState) -> None:
    calls = [_tc("modify_resource", resource_id="fuel", delta=-3)]
    props = [_proposal(f"p{i}", ProposalFlavor.DOMINANT, calls, None) for i in range(3)]
    out = compute_choice_spread(colony, props)
    assert out["spread_l1_max"] == 0.0
    assert out["identical_options"] is True
    assert out["any_option_ends_run"] is False


def test_option_that_kills_player_ends_run(colony: ColonyState) -> None:
    props = _triple(colony)
    props[2] = _proposal(
        "p_wld",
        ProposalFlavor.WILDCARD,
        [_tc("modify_health", target_id="player", delta=-100)],
        None,
    )
    out = compute_choice_spread(colony, props)
    by_id = {o["proposal_id"]: o for o in out["options"]}
    assert by_id["p_wld"]["ends_run"] == EndState.KILLED.value
    assert by_id["p_wld"]["player_health_delta"] == -100
    assert by_id["p_dom"]["ends_run"] is None
    assert out["any_option_ends_run"] is True


def test_status_change_and_input_untouched(colony: ColonyState) -> None:
    target = colony.agents[3]
    props = _triple(colony)
    props[0] = _proposal(
        "p_dom",
        ProposalFlavor.DOMINANT,
        [
            _tc(
                "set_advisor_status",
                target_id=str(target.id),
                new_status=AdvisorStatus.DETAINED.value,
            )
        ],
        colony.agents[0].id,
    )
    before = colony.model_dump()
    out = compute_choice_spread(colony, props)
    assert colony.model_dump() == before
    changes = {o["proposal_id"]: o["status_changes"] for o in out["options"]}
    assert changes["p_dom"] == [{"agent": target.name, "from": "ACTIVE", "to": "DETAINED"}]


def test_summary_aggregates_turns(colony: ColonyState) -> None:
    # Overflow clamps now, so make the picked option reject on delta range.
    triple = _triple(colony)
    triple[2] = _proposal(
        "p_wld",
        ProposalFlavor.WILDCARD,
        [_tc("modify_resource", resource_id="fuel", delta=60)],
        colony.agents[2].id,
    )
    t1 = compute_choice_spread(colony, triple)
    t1["picked_proposal_id"] = "p_wld"
    calls = [_tc("modify_resource", resource_id="fuel", delta=-3)]
    t2 = compute_choice_spread(
        colony, [_proposal(f"p{i}", ProposalFlavor.DOMINANT, calls, None) for i in range(3)]
    )
    t2["picked_proposal_id"] = "p0"
    summary = summarize_choice_spread([t1, t2])
    assert summary["n_turns"] == 2
    assert summary["zero_spread_turns"] == 1
    assert summary["spread_l1_max_mean"] == pytest.approx(t1["spread_l1_max"] / 2)
    assert summary["picked_rejected_calls"] == 1
    assert summary["picked_calls"] >= 2
    assert summary["turns_with_run_ending_option"] == 0


def test_summary_of_nothing() -> None:
    assert summarize_choice_spread([])["n_turns"] == 0


def test_summary_skips_oracle_errors(colony: ColonyState) -> None:
    ok = compute_choice_spread(colony, _triple(colony))
    summary = summarize_choice_spread([{"turn": 1, "error": "boom"}, ok])
    assert summary["n_turns"] == 1
    assert summary["oracle_errors"] == 1
    only_errors = summarize_choice_spread([{"turn": 1, "error": "boom"}])
    assert only_errors == {"n_turns": 0, "oracle_errors": 1}
