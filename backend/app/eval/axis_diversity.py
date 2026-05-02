"""Axis-diversity Haiku judge.

Scores how many distinct §6.2 axis-quadrants a turn's three proposals occupy.
A turn that produces three engineering variants of one option (all in the
same quadrant) scores 1/3; a turn that produces three genuinely different
worldviews scores 1.0.

Quadrants are over (ethics × temperament). Loyalty is rolled out (deferred
per spec §6).

No prompt caching — per-call prompts are small and per-turn unique.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.agent.anthropic_client import client as _client
from app.agent.constants import MODEL_HAIKU
from app.eval._judge_utils import _extract_text, _strip_code_fence

logger = logging.getLogger(__name__)


AXIS_DIVERSITY_THRESHOLD = 0.70
"""Mean axis-diversity floor for the gating eval. See spec §4.3."""


_QUADRANTS = (
    "PRAGMATIST_CAUTIOUS",
    "PRAGMATIST_BOLD",
    "IDEALIST_CAUTIOUS",
    "IDEALIST_BOLD",
)


_JUDGE_SYSTEM_PROMPT = (
    "You are evaluating whether three proposals in a narrative game span "
    "different worldviews. Each proposal was authored by an advisor with "
    "ethics (-1=pragmatist..+1=idealist) and temperament (-1=cautious.."
    "+1=bold) axes. Classify each proposal into one of these four quadrants "
    "based on the values revealed by the proposal's NAME, DESCRIPTION, and "
    "EFFECTS — not by the authoring advisor's stated axes. A proposal "
    "whose body argues for resource-conservation reads PRAGMATIST_CAUTIOUS "
    "regardless of who wrote it. Respond with JSON only matching: "
    '{"per_proposal": [{"advisor_id": "<uuid>", "quadrant": '
    '"PRAGMATIST_CAUTIOUS|PRAGMATIST_BOLD|IDEALIST_CAUTIOUS|IDEALIST_BOLD"}, '
    '...], "reasoning": "one sentence"}'
)


class ProposalForJudge(BaseModel):
    """Per-proposal payload the judge sees."""

    advisor_id: UUID
    advisor_axes: dict[str, float]  # keys: ethics, temperament, loyalty
    name: str
    description: str
    effects_preview: list[str]
    flavor: str  # DOMINANT / MORAL / WILDCARD


class PerProposalQuadrant(BaseModel):
    advisor_id: UUID
    quadrant: str


class AxisDiversityResult(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    distinct_quadrants: int = Field(ge=0, le=4)
    per_proposal: list[PerProposalQuadrant]
    rationale: str


def _build_user_message(event_text: str, proposals: list[ProposalForJudge]) -> str:
    lines = [f"EVENT:\n{event_text}\n"]
    for i, p in enumerate(proposals, start=1):
        lines.append(
            f"\nPROPOSAL {i} (advisor_id={p.advisor_id}, flavor={p.flavor}):\n"
            f"  axes: ethics={p.advisor_axes.get('ethics', 0):+.2f}, "
            f"temperament={p.advisor_axes.get('temperament', 0):+.2f}, "
            f"loyalty={p.advisor_axes.get('loyalty', 0):+.2f}\n"
            f"  name: {p.name}\n"
            f"  description: {p.description}\n"
            f"  effects: {' | '.join(p.effects_preview)}"
        )
    lines.append(
        "\n\nClassify each proposal into one of "
        "PRAGMATIST_CAUTIOUS / PRAGMATIST_BOLD / IDEALIST_CAUTIOUS / IDEALIST_BOLD."
    )
    return "".join(lines)


async def score_axis_diversity(
    *,
    event_text: str,
    proposals: list[ProposalForJudge],
    anthropic_client: Any | None = None,
) -> AxisDiversityResult:
    """Score one turn's three proposals on quadrant diversity.

    Score is `count_distinct_quadrants / 3.0` (capped at 1.0). Three
    distinct quadrants -> 1.0, two -> ~0.67, one -> ~0.33. Unparseable
    judge output returns score=0.5 with rationale starting "judge
    response unparseable:".
    """
    if len(proposals) != 3:
        raise ValueError(f"expected exactly 3 proposals, got {len(proposals)}")

    api = anthropic_client if anthropic_client is not None else _client
    user_message = _build_user_message(event_text, proposals)

    completion = await api.messages.create(
        model=MODEL_HAIKU,
        # 600 (not the obvious 200) because the response is a JSON object
        # with a 3-element list (each ~70 chars including UUID) plus a
        # reasoning sentence; Haiku 4.5 occasionally truncates mid-UUID at
        # lower budgets and the JSON then fails to parse (score falls back
        # to neutral 0.5). Headroom is cheap (~$0.0003/call).
        max_tokens=600,
        system=_JUDGE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    raw = _extract_text(completion)
    parsed, err = _parse_judge_per_proposal(raw)
    if err is not None:
        logger.warning("axis_diversity judge unparseable: %s; raw=%r", err, raw[:200])
        return AxisDiversityResult(
            score=0.5,
            distinct_quadrants=0,
            per_proposal=[],
            rationale=err,
        )
    per_proposal, reasoning = parsed
    distinct = len({pp.quadrant for pp in per_proposal})
    score = min(distinct / 3.0, 1.0)
    return AxisDiversityResult(
        score=score,
        distinct_quadrants=distinct,
        per_proposal=per_proposal,
        rationale=reasoning,
    )


def _parse_judge_per_proposal(
    raw: str,
) -> tuple[tuple[list[PerProposalQuadrant], str] | None, str | None]:
    """Parse the diversity judge's structured JSON.

    Returns `((per_proposal, reasoning), None)` on success or
    `(None, "judge response unparseable: <err>")` on failure. Mirrors the
    error-message convention of `_judge_utils._parse_judge_json` so callers
    can rely on the same "unparseable" prefix for the neutral-fallback path.
    """
    text = _strip_code_fence(raw.strip())
    try:
        parsed = json.loads(text)
        per_proposal_raw = parsed["per_proposal"]
        per_proposal: list[PerProposalQuadrant] = []
        for item in per_proposal_raw:
            quadrant = str(item["quadrant"])
            if quadrant not in _QUADRANTS:
                return (
                    None,
                    f"judge response unparseable: unknown quadrant {quadrant!r}",
                )
            per_proposal.append(
                PerProposalQuadrant(
                    advisor_id=UUID(str(item["advisor_id"])),
                    quadrant=quadrant,
                )
            )
        reasoning = str(parsed.get("reasoning", "")).strip() or "no rationale provided"
        return (per_proposal, reasoning), None
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as err_:
        return None, f"judge response unparseable: {err_}"
