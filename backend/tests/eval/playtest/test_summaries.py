"""runs.json summaries + aggregates for the baseline questions."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from app.eval.choice_spread import compute_choice_spread
from app.eval.playtest.output import PerRunArchive
from app.eval.playtest.schemas import PersonaTurnFlags, PersonaTurnOutput
from app.eval.playtest.summaries import build_turn_context, corpus_aggregates, per_run_summary
from app.game.generator import generate_run
from app.game.schemas import Proposal, ProposalFlavor, ToolCall
from tests.eval.playtest._verdicts import make_verdict


def _spread_turn(turn: int, picked: str) -> dict[str, Any]:
    colony = generate_run(
        user_id=UUID("bbbbbbbb-0000-0000-0000-000000000002"),
        setting_id="arctic_station_v1",
        seed=5,
    )
    props = [
        Proposal(
            id=pid,
            proposed_by_agent_id=None,
            flavor=ProposalFlavor.DOMINANT,
            name=pid,
            description="d",
            effects_preview=[],
            tool_calls_on_pick=[
                ToolCall(
                    name="modify_resource",
                    caller_id=None,
                    args={"resource_id": "fuel", "delta": -delta, "reason": "r"},
                )
            ],
        )
        for pid, delta in (("a", 5), ("b", 0), ("c", 60))  # c: delta out of range → rejected
    ]
    return {"turn": turn, "picked_proposal_id": picked, **compute_choice_spread(colony, props)}


def _turn(turn: int, *, outcome: bool | None, bug: bool = False) -> PersonaTurnOutput:
    return PersonaTurnOutput(
        turn=turn,
        displayed_order=["a", "b", "c"],
        chosen_proposal_id="c",
        shown_position_of_chosen=2,
        reaction="r",
        outcome_matched_expectation=outcome,
        flags=PersonaTurnFlags(
            surprise=False,
            tone_break=False,
            advisor_oc=False,
            stakes_low=False,
            suspected_bug=bug,
        ),
        quotes={"suspected_bug": "Fuel +2"} if bug else {},
    )


def _archive(persona: str, end_state: str, legit: int, difficulty: int) -> PerRunArchive:
    spread = [_spread_turn(1, "c"), _spread_turn(2, "a")]
    return PerRunArchive(
        run_id=f"run-{persona}",
        persona_id=persona,
        setting_id="arctic_station_v1",
        seed=11,
        end_state=end_state,
        turns_played=2,
        per_turn_outputs=[_turn(1, outcome=None), _turn(2, outcome=False, bug=True)],
        verdict=make_verdict(score=4, difficulty=difficulty, bugs=["fuel bar stuck"]),
        run_record_summary={
            "final_player_health": 90,
            "tool_calls": [],
            "narration_per_turn": [],
            "choice_spread": spread,
        },
        per_run_oracles={"tool_validity_rate": 1.0},
        final_legitimacy=legit,
        final_resource_frac=0.5,
    )


def test_per_run_summary_has_baseline_fields() -> None:
    s = per_run_summary(_archive("optimizer", "ENDURED", 60, 2))
    assert s["outcome_expectation"] == {"matched": 0, "mismatched": 1, "n_a": 1}
    assert s["flag_counts"]["suspected_bug"] == 1
    assert s["flag_counts"]["confused"] == 0
    assert s["axis_scores"]["meaningful_choices"] == 4
    assert s["difficulty"] == 2
    assert s["bugs_noticed"] == ["fuel bar stuck"]
    assert s["final_legitimacy"] == 60
    assert s["choice_spread"]["n_turns"] == 2
    assert s["choice_spread"]["picked_rejected_calls"] == 1


def test_corpus_aggregates_answer_the_baseline_questions() -> None:
    archives = [
        _archive("optimizer", "ENDURED", 60, 2),
        _archive("pessimist", "COLLAPSED", 20, 4),
    ]
    agg = corpus_aggregates(archives)

    assert agg["difficulty"]["mean"] == pytest.approx(3.0)
    assert agg["difficulty"]["distribution"] == {"1": 0, "2": 1, "3": 0, "4": 1, "5": 0}
    assert agg["difficulty"]["by_persona"] == {"optimizer": 2.0, "pessimist": 4.0}
    assert agg["verdict_axis_means"]["strategic_depth"]["mean"] == pytest.approx(4.0)
    assert agg["outcome_expectation"]["mismatched"] == 2
    assert agg["outcome_expectation"]["mismatch_rate"] == pytest.approx(1.0)
    assert agg["flag_aggregates"]["suspected_bug"] == 2
    assert agg["flagged_quotes"]["suspected_bug"][0] == {
        "persona": "optimizer",
        "setting": "arctic_station_v1",
        "seed": 11,
        "turn": 2,
        "quote": "Fuel +2",
    }
    assert len(agg["bugs_noticed"]) == 2
    assert agg["end_states"]["overall"] == {"ENDURED": 1, "COLLAPSED": 1}
    assert agg["choice_spread"]["n_turns"] == 4
    cs = agg["choice_spread"]
    assert cs["picked_rejected_calls"] == 2
    assert cs["picked_rejected_rate"] == pytest.approx(2 / cs["picked_calls"])
    paired = agg["paired"]["summary"]
    assert paired["n_paired_cells"] == 1
    assert paired["share_end_state_differs"] == pytest.approx(1.0)
    assert paired["mean_legitimacy_range"] == pytest.approx(40)


def test_corpus_aggregates_empty() -> None:
    agg = corpus_aggregates([])
    assert agg["difficulty"]["mean"] is None
    assert agg["paired"]["summary"]["n_paired_cells"] == 0


def test_build_turn_context_joins_view_pick_and_oracle() -> None:
    turn_inputs = [
        {
            "turn": 1,
            "day": 0,
            "event": {"title": "Coil", "origin": "NATURAL"},
            "proposals": [
                {"id": "a", "name": "A", "flavor": "DOMINANT", "effects_preview": ["-5 fuel"]},
                {"id": "b", "name": "B", "flavor": "MORAL", "effects_preview": []},
                {"id": "c", "name": "C", "flavor": "WILDCARD", "effects_preview": []},
            ],
        }
    ]
    ctx = build_turn_context(
        turn_inputs=turn_inputs,
        outputs=[_turn(1, outcome=None)],
        choice_spread=[_spread_turn(1, "c")],
    )
    assert len(ctx) == 1
    t = ctx[0]
    assert t["event_title"] == "Coil"
    assert t["chosen"]["name"] == "C"
    by_id = {o["proposal_id"]: o for o in t["options"]}
    assert by_id["a"]["resource_deltas"] == {"fuel": -5}
    assert by_id["b"]["resource_deltas"] == {}
    assert by_id["c"]["rejected"] == ["modify_resource:delta_out_of_range"]
    assert t["spread_l1_max"] > 0
