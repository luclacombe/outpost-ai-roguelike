"""Pydantic models for the LLM play-test harness.

These mirror the JSON contract documented in the design spec
(§5 per-turn schema, §6 run-level verdict). Validation is strict at
both ends — personas emit JSON, the harness round-trips through
these models, and any malformed output triggers the re-prompt-once
fallback in §10.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

PersonaId = Literal[
    "optimizer",
    "pessimist",
    "narrative_driven",
    "shallow_reader",
    "brinksmanship",
    "veteran_roguelike",
    "drop_off_risk",
]


class PersonaTurnFlags(BaseModel):
    """Boolean analytical flags emitted per turn. Quote required when True.

    `confused` (the persona could not tell what to do or what happened) and
    `suspected_bug` (something on screen looks wrong: an effect that did not
    land, a number that contradicts the text) default to False so a terse
    output still parses. `persona_session` drops either flag when its quote
    is missing or not verbatim from the rendered turn.
    """

    surprise: bool
    tone_break: bool
    advisor_oc: bool
    stakes_low: bool
    confused: bool = False
    suspected_bug: bool = False


class PersonaTurnOutput(BaseModel):
    """One turn of persona output: pick + reaction + flags + quotes.

    `displayed_order` records the per-turn shuffled proposal order so the
    aggregator can detect position bias (Spearman pick-vs-position
    correlation). `shown_position_of_chosen` is the 0-indexed position
    where the chosen proposal appeared in `displayed_order`.

    `outcome_matched_expectation` judges the PREVIOUS pick: did the "Last
    turn" outcome match what its effects preview and description promised?
    None on turn 1 or whenever no "Last turn" section was shown.
    """

    turn: int = Field(ge=1)
    displayed_order: list[str]
    chosen_proposal_id: str
    shown_position_of_chosen: int = Field(ge=0, le=2)
    reaction: str
    outcome_matched_expectation: bool | None = None
    flags: PersonaTurnFlags
    quotes: dict[str, str] = Field(default_factory=dict)

    @field_validator("displayed_order")
    @classmethod
    def _three_ids(cls, v: list[str]) -> list[str]:
        if len(v) != 3:
            raise ValueError(f"displayed_order must have exactly 3 ids, got {len(v)}")
        return v

    @model_validator(mode="after")
    def _consistency(self) -> PersonaTurnOutput:
        if self.chosen_proposal_id not in self.displayed_order:
            raise ValueError("chosen_proposal_id must be in displayed_order")
        if self.displayed_order[self.shown_position_of_chosen] != self.chosen_proposal_id:
            raise ValueError("shown_position_of_chosen must match displayed_order index of chosen")
        return self


class AxisScore(BaseModel):
    """One axis of the per-run verdict: 1-5 score + rationale + turn citations.

    Rationale-before-score is enforced by field order in the JSON
    contract (Pydantic preserves insertion order). The persona prompt
    reinforces this — see persona_session.py.
    """

    score: int = Field(ge=1, le=5)
    rationale: str
    turn_citations: list[int] = Field(default_factory=list)


class ContinueTomorrow(BaseModel):
    """Binary commitment + ≤20-word reason for daily-game retention axis."""

    decision: bool
    reason: str

    @field_validator("reason")
    @classmethod
    def _max_twenty_words(cls, v: str) -> str:
        words = len(v.split())
        if words > 20:
            raise ValueError(f"reason must be ≤20 words, got {words}")
        return v


class VerdictAxes(BaseModel):
    """Seven quality axes, higher = better. `difficulty` lives on the verdict
    (not here) because its scale is centred, not monotonic."""

    stakes_felt_real: AxisScore
    tonal_coherence: AxisScore
    advisor_distinctness: AxisScore
    dramatic_arc: AxisScore
    # Did different picks lead to visibly different results?
    meaningful_choices: AxisScore
    # Was there a plan worth forming beyond reacting turn by turn?
    strategic_depth: AxisScore
    # Was the turn loop (event → debate → pick → aftermath) legible?
    loop_clarity: AxisScore


MAX_BUGS_NOTICED = 5
MAX_BUG_CHARS = 200


class PersonaRunVerdict(BaseModel):
    """End-of-run verdict: scored axes + difficulty + bugs + continue + prose.

    `difficulty` semantics (centred, NOT higher-is-better):
      1 = trivially easy, 2 = easy, 3 = well tuned, 4 = hard, 5 = unfairly hard.
    `bugs_noticed` is truncated to 5 short items rather than rejected, so a
    long list never costs a verdict retry.
    """

    axes: VerdictAxes
    difficulty: AxisScore
    bugs_noticed: list[str] = Field(default_factory=list)
    continue_tomorrow: ContinueTomorrow
    closing_paragraph: str

    @field_validator("bugs_noticed")
    @classmethod
    def _cap_bugs(cls, v: list[str]) -> list[str]:
        return [b.strip()[:MAX_BUG_CHARS] for b in v if b.strip()][:MAX_BUGS_NOTICED]


class Persona(BaseModel):
    """Three-field persona definition. Picking rule is decision-policy
    (not narrative bio) per spec §3 — narrative personas drift toward
    socially-desirable normality (arXiv 2512.06867)."""

    id: PersonaId
    picking_rule: str
    evaluation_lens: str
    voice_traits: str
