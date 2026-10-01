"""Shared run-verdict builders for playtest tests (keeps the shape in one place)."""

from __future__ import annotations

from typing import Any

from app.eval.playtest.schemas import PersonaRunVerdict

AXIS_NAMES = (
    "stakes_felt_real",
    "tonal_coherence",
    "advisor_distinctness",
    "dramatic_arc",
    "meaningful_choices",
    "strategic_depth",
    "loop_clarity",
)


def verdict_dict(
    *, score: int = 4, difficulty: int = 3, bugs: list[str] | None = None
) -> dict[str, Any]:
    """A valid PersonaRunVerdict JSON payload."""
    return {
        "axes": {
            name: {"score": score, "rationale": "x", "turn_citations": []} for name in AXIS_NAMES
        },
        "difficulty": {"score": difficulty, "rationale": "x", "turn_citations": []},
        "bugs_noticed": list(bugs or []),
        "continue_tomorrow": {"decision": True, "reason": "liked it"},
        "closing_paragraph": "...",
    }


def make_verdict(**kwargs: Any) -> PersonaRunVerdict:
    return PersonaRunVerdict.model_validate(verdict_dict(**kwargs))
