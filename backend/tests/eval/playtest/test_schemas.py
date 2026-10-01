"""Validation tests for play-test harness Pydantic models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.eval.playtest.schemas import (
    AxisScore,
    ContinueTomorrow,
    Persona,
    PersonaRunVerdict,
    PersonaTurnFlags,
    PersonaTurnOutput,
    VerdictAxes,
)


def test_persona_turn_output_minimal() -> None:
    out = PersonaTurnOutput(
        turn=1,
        displayed_order=["a", "b", "c"],
        chosen_proposal_id="b",
        shown_position_of_chosen=1,
        reaction="Picked the middle option.",
        flags=PersonaTurnFlags(
            surprise=False, tone_break=False, advisor_oc=False, stakes_low=False
        ),
        quotes={},
    )
    assert out.chosen_proposal_id == "b"


def test_persona_turn_output_chosen_must_be_in_order() -> None:
    with pytest.raises(ValidationError, match="chosen_proposal_id must be in displayed_order"):
        PersonaTurnOutput(
            turn=1,
            displayed_order=["a", "b", "c"],
            chosen_proposal_id="z",
            shown_position_of_chosen=0,
            reaction="...",
            flags=PersonaTurnFlags(
                surprise=False, tone_break=False, advisor_oc=False, stakes_low=False
            ),
            quotes={},
        )


def test_persona_turn_output_position_must_match() -> None:
    with pytest.raises(ValidationError, match="shown_position_of_chosen must match"):
        PersonaTurnOutput(
            turn=1,
            displayed_order=["a", "b", "c"],
            chosen_proposal_id="b",
            shown_position_of_chosen=0,  # b is at index 1, not 0
            reaction="...",
            flags=PersonaTurnFlags(
                surprise=False, tone_break=False, advisor_oc=False, stakes_low=False
            ),
            quotes={},
        )


def test_persona_turn_output_displayed_order_must_be_three() -> None:
    with pytest.raises(ValidationError, match="exactly 3"):
        PersonaTurnOutput(
            turn=1,
            displayed_order=["a", "b"],
            chosen_proposal_id="a",
            shown_position_of_chosen=0,
            reaction="...",
            flags=PersonaTurnFlags(
                surprise=False, tone_break=False, advisor_oc=False, stakes_low=False
            ),
            quotes={},
        )


def test_axis_score_clamps_1_to_5() -> None:
    AxisScore(score=1, rationale="x", turn_citations=[])
    AxisScore(score=5, rationale="x", turn_citations=[])
    with pytest.raises(ValidationError):
        AxisScore(score=0, rationale="x", turn_citations=[])
    with pytest.raises(ValidationError):
        AxisScore(score=6, rationale="x", turn_citations=[])


def test_continue_tomorrow_caps_reason_at_20_words() -> None:
    ContinueTomorrow(decision=True, reason="five words is well within")
    too_long = " ".join(["word"] * 25)
    with pytest.raises(ValidationError, match="20 words"):
        ContinueTomorrow(decision=True, reason=too_long)


def _axis(score: int = 4) -> AxisScore:
    return AxisScore(score=score, rationale="...", turn_citations=[])


def _axes() -> VerdictAxes:
    return VerdictAxes(
        stakes_felt_real=AxisScore(score=4, rationale="...", turn_citations=[1, 7]),
        tonal_coherence=_axis(5),
        advisor_distinctness=AxisScore(score=4, rationale="...", turn_citations=[3]),
        dramatic_arc=AxisScore(score=4, rationale="...", turn_citations=[8, 11]),
        meaningful_choices=_axis(3),
        strategic_depth=_axis(2),
        loop_clarity=_axis(4),
    )


def test_persona_run_verdict_full_shape() -> None:
    verdict = PersonaRunVerdict(
        axes=_axes(),
        difficulty=_axis(3),
        bugs_noticed=["Fuel effect said +12, bar moved +2."],
        continue_tomorrow=ContinueTomorrow(decision=True, reason="dissident path landed"),
        closing_paragraph="...",
    )
    assert verdict.axes.stakes_felt_real.score == 4
    assert verdict.axes.strategic_depth.score == 2
    assert verdict.difficulty.score == 3
    assert verdict.continue_tomorrow.decision is True


def test_verdict_requires_new_axes_and_difficulty() -> None:
    with pytest.raises(ValidationError):
        VerdictAxes(
            stakes_felt_real=_axis(),
            tonal_coherence=_axis(),
            advisor_distinctness=_axis(),
            dramatic_arc=_axis(),
        )
    with pytest.raises(ValidationError, match="difficulty"):
        PersonaRunVerdict.model_validate(
            {
                "axes": _axes().model_dump(),
                "continue_tomorrow": {"decision": True, "reason": "ok"},
                "closing_paragraph": "...",
            }
        )


def test_bugs_noticed_defaults_empty_and_caps_at_five() -> None:
    base = {
        "axes": _axes().model_dump(),
        "difficulty": _axis(3).model_dump(),
        "continue_tomorrow": {"decision": True, "reason": "ok"},
        "closing_paragraph": "...",
    }
    assert PersonaRunVerdict.model_validate(base).bugs_noticed == []
    many = PersonaRunVerdict.model_validate(
        {**base, "bugs_noticed": [f"bug {i}" for i in range(8)]}
    )
    assert many.bugs_noticed == [f"bug {i}" for i in range(5)]
    long = PersonaRunVerdict.model_validate({**base, "bugs_noticed": ["x" * 500]})
    assert len(long.bugs_noticed[0]) <= 200


def test_turn_output_new_fields_default_safely() -> None:
    """Older/terse outputs without the new fields still parse."""
    out = PersonaTurnOutput.model_validate(
        {
            "turn": 1,
            "displayed_order": ["a", "b", "c"],
            "chosen_proposal_id": "a",
            "shown_position_of_chosen": 0,
            "reaction": "r",
            "flags": {
                "surprise": False,
                "tone_break": False,
                "advisor_oc": False,
                "stakes_low": False,
            },
        }
    )
    assert out.outcome_matched_expectation is None
    assert out.flags.confused is False
    assert out.flags.suspected_bug is False


def test_turn_output_accepts_new_fields() -> None:
    out = PersonaTurnOutput.model_validate(
        {
            "turn": 2,
            "displayed_order": ["a", "b", "c"],
            "chosen_proposal_id": "b",
            "shown_position_of_chosen": 1,
            "reaction": "r",
            "outcome_matched_expectation": False,
            "flags": {
                "surprise": False,
                "tone_break": False,
                "advisor_oc": False,
                "stakes_low": False,
                "confused": True,
                "suspected_bug": True,
            },
            "quotes": {"confused": "Day 00 / 9", "suspected_bug": "Fuel +2"},
        }
    )
    assert out.outcome_matched_expectation is False
    assert out.flags.confused and out.flags.suspected_bug


def test_persona_shape() -> None:
    p = Persona(
        id="optimizer",
        picking_rule="Maximize state.",
        evaluation_lens="Outcomes.",
        voice_traits="Analytical, terse.",
    )
    assert p.id == "optimizer"


def test_persona_id_must_be_known() -> None:
    with pytest.raises(ValidationError):
        Persona(
            id="not_a_real_persona",  # type: ignore[arg-type]
            picking_rule="x",
            evaluation_lens="x",
            voice_traits="x",
        )
