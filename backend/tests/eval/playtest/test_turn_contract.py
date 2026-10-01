"""Per-turn + verdict output contract: new fields, quote rules, prompt text."""

from __future__ import annotations

import json
import re
from typing import Any

from app.eval.playtest.persona_session import (
    _build_system_prompt,
    _build_verdict_prompt,
    _format_turn_user_message,
    _validate_turn_output,
)
from app.eval.playtest.personas import OPTIMIZER
from app.eval.playtest.schemas import PersonaRunVerdict, PersonaTurnFlags, PersonaTurnOutput

_ORDER = ["p_mor", "p_dom", "p_wld"]


def _out(
    *,
    flags: dict[str, bool] | None = None,
    quotes: dict[str, str] | None = None,
    outcome: bool | None = None,
) -> PersonaTurnOutput:
    base = {"surprise": False, "tone_break": False, "advisor_oc": False, "stakes_low": False}
    base.update(flags or {})
    return PersonaTurnOutput(
        turn=2,
        displayed_order=_ORDER,
        chosen_proposal_id="p_dom",
        shown_position_of_chosen=1,
        reaction="r",
        outcome_matched_expectation=outcome,
        flags=PersonaTurnFlags(**base),
        quotes=quotes or {},
    )


def test_new_flag_kept_with_verbatim_quote(fixture_turn_input_t2: dict[str, Any]) -> None:
    msg = _format_turn_user_message(turn_input=fixture_turn_input_t2, displayed_order=_ORDER)
    out = _out(
        flags={"suspected_bug": True, "confused": True},
        quotes={
            # Case and whitespace differences are tolerated; ellipsis joins fragments.
            "suspected_bug": "effects shown:  +12 FUEL; -5 morale ... Legitimacy -4",
            "confused": "Day 01 / 9",
        },
    )
    cleaned = _validate_turn_output(out, msg)
    assert cleaned.flags.suspected_bug is True
    assert cleaned.flags.confused is True
    assert cleaned.quotes == out.quotes


def test_new_flag_dropped_without_quote(fixture_turn_input_t2: dict[str, Any]) -> None:
    msg = _format_turn_user_message(turn_input=fixture_turn_input_t2, displayed_order=_ORDER)
    cleaned = _validate_turn_output(_out(flags={"confused": True}), msg)
    assert cleaned.flags.confused is False


def test_new_flag_dropped_with_paraphrased_quote(fixture_turn_input_t2: dict[str, Any]) -> None:
    msg = _format_turn_user_message(turn_input=fixture_turn_input_t2, displayed_order=_ORDER)
    out = _out(flags={"suspected_bug": True}, quotes={"suspected_bug": "fuel went up by twelve"})
    cleaned = _validate_turn_output(out, msg)
    assert cleaned.flags.suspected_bug is False
    assert "suspected_bug" not in cleaned.quotes


def test_outcome_judgement_cleared_without_last_turn(fixture_turn_input: dict[str, Any]) -> None:
    msg = _format_turn_user_message(turn_input=fixture_turn_input, displayed_order=_ORDER)
    assert _validate_turn_output(_out(outcome=True), msg).outcome_matched_expectation is None


def test_outcome_judgement_kept_with_last_turn(fixture_turn_input_t2: dict[str, Any]) -> None:
    msg = _format_turn_user_message(turn_input=fixture_turn_input_t2, displayed_order=_ORDER)
    assert _validate_turn_output(_out(outcome=False), msg).outcome_matched_expectation is False


def test_fabricated_silence_guard_still_runs(fixture_turn_input: dict[str, Any]) -> None:
    msg = _format_turn_user_message(turn_input=fixture_turn_input, displayed_order=_ORDER)
    out = _out(flags={"advisor_oc": True}, quotes={"advisor_oc": "Marko: (silent)"})
    assert _validate_turn_output(out, msg).flags.advisor_oc is False


def test_system_prompt_documents_new_fields() -> None:
    prompt = _build_system_prompt(OPTIMIZER)
    for token in ("outcome_matched_expectation", "confused", "suspected_bug", "## Last turn"):
        assert token in prompt


def test_system_prompt_worked_examples_validate() -> None:
    """Every JSON example in the contract must itself pass the schema."""
    prompt = _build_system_prompt(OPTIMIZER)
    tail = prompt[prompt.index("Worked example") :]
    blocks = re.findall(r"```json\n(.*?)\n```", tail, flags=re.DOTALL)
    assert len(blocks) >= 2
    parsed = [PersonaTurnOutput.model_validate(json.loads(b)) for b in blocks]
    assert parsed[0].outcome_matched_expectation is None
    assert any(p.outcome_matched_expectation is False for p in parsed[1:])


def test_verdict_prompt_documents_new_axes_and_difficulty() -> None:
    prompt = _build_verdict_prompt({"end_state": "ENDURED"})
    for token in (
        "meaningful_choices",
        "strategic_depth",
        "loop_clarity",
        "difficulty",
        "bugs_noticed",
        "3 = well tuned",
    ):
        assert token in prompt
    example = re.search(r"Example:\n```json\n(.*?)\n```", prompt, flags=re.DOTALL)
    assert example is not None
    PersonaRunVerdict.model_validate(json.loads(example.group(1)))
