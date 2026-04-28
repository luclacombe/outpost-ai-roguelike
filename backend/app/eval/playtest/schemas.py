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
    """Boolean analytical flags emitted per turn. Quote required when True."""

    surprise: bool
    tone_break: bool
    advisor_oc: bool
    stakes_low: bool


class PersonaTurnOutput(BaseModel):
    """One turn of persona output: pick + reaction + flags + quotes.

    `displayed_order` records the per-turn shuffled proposal order so the
    aggregator can detect position bias (Spearman pick-vs-position
    correlation). `shown_position_of_chosen` is the 0-indexed position
    where the chosen proposal appeared in `displayed_order`.
    """

    turn: int = Field(ge=1)
    displayed_order: list[str]
    chosen_proposal_id: str
    shown_position_of_chosen: int = Field(ge=0, le=2)
    reaction: str
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
    """Four scored axes — `continue_tomorrow` is separate (binary, not scored)."""

    stakes_felt_real: AxisScore
    tonal_coherence: AxisScore
    advisor_distinctness: AxisScore
    dramatic_arc: AxisScore


class PersonaRunVerdict(BaseModel):
    """End-of-run verdict: 4 scored axes + binary continue + closing prose."""

    axes: VerdictAxes
    continue_tomorrow: ContinueTomorrow
    closing_paragraph: str


class Persona(BaseModel):
    """Three-field persona definition. Picking rule is decision-policy
    (not narrative bio) per spec §3 — narrative personas drift toward
    socially-desirable normality (arXiv 2512.06867)."""

    id: PersonaId
    picking_rule: str
    evaluation_lens: str
    voice_traits: str
