"""Persona prompt text: system prompt, run-verdict prompt, count-retry nudge.

Frame and output contract are identical across all 7 personas; only the
persona block varies. Uniformity is load-bearing for cross-persona
comparability (spec §4). Worked examples are validated against the schema
in tests (`tests/eval/playtest/test_turn_contract.py`), so they must stay
copy-pasteable JSON.
"""

from __future__ import annotations

import json
from typing import Any

from app.eval.playtest.schemas import Persona

_FRAME = (
    "You are play-testing Outpost, a daily browser narrative roguelike. "
    "Four AI advisors run a fragile colony under your command. Permadeath. "
    "Each turn: an event, advisor reactions, three proposals with an effects "
    "preview, then an aftermath. Runs end at day 8-12 in one of: Endured, "
    "Collapsed, Ousted, Killed.\n\n"
    "You are reacting fresh: you have not seen this game before. You have "
    "no codebase context, no design documents, no prior plays. You see only "
    "what the game's screen shows. Stay in your persona's voice for "
    "in-character text; speak analytically for flags and scores. Reasoning "
    "before scoring (always).\n\n"
    "Reading discipline (load-bearing): the user message includes a "
    "`## Advisor reactions` section that lists every living advisor by "
    "name with their actual reaction text. Read it line-by-line before "
    "evaluating `advisor_oc` or claiming silence. An advisor counts as "
    "silent ONLY if the dashboard literally renders `(silent)` next to "
    "their name. Quotes you cite for any flag (especially `advisor_oc`) "
    "MUST be verbatim excerpts from the rendered dashboard text. "
    "Fabricating a `(silent)` claim against an advisor whose reaction is "
    "rendered, or paraphrasing reaction text into a quote, is a contract "
    "violation.\n\n"
    "From turn 2 on, a `## Last turn` section shows what you picked, the "
    "effects it previewed, the aftermath, and what visibly changed. Compare "
    "them before you answer."
)

_TURN_FIELDS = (
    "\n\nPer turn, respond with ONLY a single JSON code-fence, no prose "
    "before or after. Fields:\n"
    "- `turn`: the number in the `# Turn N` heading.\n"
    "- `displayed_order`: EXACTLY THREE ids, verbatim from the user message's "
    "`Pick one` line.\n"
    "- `chosen_proposal_id`: one of `displayed_order`; "
    "`shown_position_of_chosen`: its index (0, 1 or 2).\n"
    "- `reaction`: 1-2 sentences in your voice.\n"
    "- `outcome_matched_expectation`: about your PREVIOUS pick. true if the "
    "`## Last turn` changes and aftermath matched its effects preview and "
    "description, false if they did not, null on turn 1 or when there is "
    "no `## Last turn` section.\n"
    "- `flags` (all booleans): `surprise`, `tone_break`, `advisor_oc`, "
    "`stakes_low`, `confused` (you could not tell what a proposal would do, "
    "what happened, or what the screen meant), `suspected_bug` (something "
    "on screen looks wrong: an effect that did not land, a number that "
    "contradicts the text, a dead advisor acting).\n"
    '- `quotes`: `{"<flag_name>": "<=30-word verbatim excerpt>"}` for every '
    "true flag. Quotes for `confused` and `suspected_bug` are checked "
    "against the screen text; a missing or paraphrased quote voids the "
    "flag. Use `...` to join two short excerpts.\n"
)

_EXAMPLE_TURN_1 = {
    "turn": 1,
    "displayed_order": ["p_dom", "p_mor", "p_wld"],
    "chosen_proposal_id": "p_dom",
    "shown_position_of_chosen": 0,
    "reaction": "Picked the perimeter cut. Math beats sentiment for week 1.",
    "outcome_matched_expectation": None,
    "flags": {
        "surprise": False,
        "tone_break": False,
        "advisor_oc": False,
        "stakes_low": False,
        "confused": False,
        "suspected_bug": False,
    },
    "quotes": {},
}

_EXAMPLE_TURN_2 = {
    "turn": 2,
    "displayed_order": ["p_wld", "p_dom", "p_mor"],
    "chosen_proposal_id": "p_mor",
    "shown_position_of_chosen": 2,
    "reaction": "The cut promised +12 fuel and delivered +2. Trying the honest option.",
    "outcome_matched_expectation": False,
    "flags": {
        "surprise": False,
        "tone_break": False,
        "advisor_oc": False,
        "stakes_low": False,
        "confused": False,
        "suspected_bug": True,
    },
    "quotes": {"suspected_bug": "Effects shown: +12 fuel ... Fuel +2"},
}


def _fence(obj: dict[str, Any]) -> str:
    return "```json\n" + json.dumps(obj, indent=2) + "\n```"


def build_system_prompt(persona: Persona) -> str:
    """The 3-section persona prompt: frame + persona block + output contract."""
    persona_block = (
        f"\n\nPicking rule: {persona.picking_rule}\n"
        f"Evaluation lens: {persona.evaluation_lens}\n"
        f"Voice traits: {persona.voice_traits}"
    )
    examples = (
        "Worked example, turn 1 (illustrative; your actual ids will differ):\n"
        + _fence(_EXAMPLE_TURN_1)
        + "\nWorked example, a later turn where the previous pick under-delivered:\n"
        + _fence(_EXAMPLE_TURN_2)
        + "\n`displayed_order` MUST be a JSON array of EXACTLY THREE strings, "
        "copied verbatim from the user message: never a single id, never "
        "fewer than three, never paraphrased. The harness will re-prompt once "
        "if your JSON is malformed."
    )
    return _FRAME + persona_block + _TURN_FIELDS + examples


def build_count_correction_message(*, displayed_order: list[str]) -> str:
    """Re-prompt used when the prior turn returned a `displayed_order` list of
    length != 3. Echoes the actual ids so the model has a hard target."""
    return (
        "Your previous response had `displayed_order` with the wrong count "
        "of ids. The list MUST have EXACTLY THREE strings. Re-emit the "
        f"full per-turn JSON now with `displayed_order` set to exactly: "
        f"{displayed_order}. Pick one of those three for `chosen_proposal_id`. "
        "Same JSON shape as before. No prose outside the code-fence."
    )


def _axis_example(score: int, rationale: str, cites: list[int]) -> dict[str, Any]:
    return {"rationale": rationale, "score": score, "turn_citations": cites}


_VERDICT_EXAMPLE = {
    "axes": {
        "stakes_felt_real": _axis_example(4, "The day-6 ration cut cost Nadia.", [6]),
        "tonal_coherence": _axis_example(4, "Arctic grit held until the last line.", []),
        "advisor_distinctness": _axis_example(3, "Marko and Yuri blurred late.", [7, 8]),
        "dramatic_arc": _axis_example(3, "Pressure peaked early, then flattened.", [3]),
        "meaningful_choices": _axis_example(2, "Two options per turn did the same.", [2, 5]),
        "strategic_depth": _axis_example(2, "No reason to plan past the next day.", []),
        "loop_clarity": _axis_example(4, "Event, debate, pick, aftermath read cleanly.", []),
    },
    "difficulty": _axis_example(2, "Fuel never dropped below the warning tone.", [4]),
    "bugs_noticed": ["Turn 2: effects said +12 fuel, the bar moved +2."],
    "continue_tomorrow": {"decision": True, "reason": "I want to see the coup path."},
    "closing_paragraph": "About 100 words in your evaluation voice.",
}


def build_verdict_prompt(post_mortem_json: dict[str, Any]) -> str:
    """End-of-run prompt: post-mortem in, run-level verdict JSON out."""
    return (
        "# End of run\n\n"
        "Below is the full post-mortem (counterfactuals, dormant seeds, "
        "near-miss coups, end_state). Read it carefully, then emit ONLY a "
        "JSON code-fence with the run-level verdict.\n\n"
        "Scoring (every score is an int 1-5; write the rationale before you "
        "pick the number, and cite turns where you can):\n"
        "- `axes.*` are quality axes, higher = better: `stakes_felt_real`, "
        "`tonal_coherence`, `advisor_distinctness`, `dramatic_arc`, "
        "`meaningful_choices` (did different picks lead to visibly different "
        "results?), `strategic_depth` (was there a plan worth forming beyond "
        "reacting turn by turn?), `loop_clarity` (was the event, debate, pick, "
        "aftermath loop easy to follow?).\n"
        "- `difficulty` is NOT higher-is-better: 1 = trivially easy, 2 = easy, "
        "3 = well tuned, 4 = hard, 5 = unfairly hard.\n"
        "- `bugs_noticed`: up to 5 short strings, each one thing that looked "
        "broken, with its turn. [] if none.\n"
        '- `continue_tomorrow`: {"decision": <bool>, "reason": "<=20 words"}.\n'
        "- `closing_paragraph`: ~100 words in your evaluation voice.\n\n"
        "Example:\n" + _fence(_VERDICT_EXAMPLE) + "\n\n"
        "## Post-mortem\n\n"
        f"```json\n{json.dumps(post_mortem_json, indent=2, default=str)}\n```"
    )
