"""Axis-attribution Haiku judge.

Per-proposal binary scorer: does this proposal's content embody the
authoring advisor's axes? 1 = yes, 0 = no. Run-level mean over the
turn's three proposals.

Catches the failure mode where axis_diversity is high but attribution is
scrambled (Vera labelled DOMINANT proposes an Idealist+Bold action). No
prompt caching — per-call prompts vary.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.agent.anthropic_client import client as _client
from app.agent.constants import MODEL_HAIKU
from app.eval._judge_utils import _extract_text, _parse_judge_json
from app.eval.axis_diversity import ProposalForJudge

logger = logging.getLogger(__name__)


AXIS_ATTRIBUTION_THRESHOLD = 0.70
"""Mean attribution-correctness floor for the gating eval. See spec §4.3."""

_CONCURRENCY_CAP = 3
"""Per-turn this is bounded to 3 (one judge call per proposal). The cap exists
to keep multi-turn callers polite if/when this is ever pulled into a run-level
eval."""


_JUDGE_SYSTEM_PROMPT = (
    "You are evaluating whether a proposal in a narrative game embodies the "
    "values of its authoring advisor. The advisor has three axes: ethics "
    "(-1=pragmatist..+1=idealist), temperament (-1=cautious..+1=bold), "
    "loyalty (-1=individual..+1=collective). Score 1 if the proposal's "
    "name/description/effects align with at least TWO of the advisor's three "
    "axis poles; score 0 otherwise. Tie cases (axis float in [-0.20, +0.20]) "
    "count as either pole. Respond with JSON only: "
    '{"score": 0|1, "reasoning": "one sentence"}'
)


class ProposalAttributionObservation(BaseModel):
    advisor_id: UUID
    score: float = Field(ge=0.0, le=1.0)
    rationale: str


class TurnAttributionResult(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    per_proposal: list[ProposalAttributionObservation]


def _build_user_message(event_text: str, proposal: ProposalForJudge) -> str:
    a = proposal.advisor_axes
    return (
        f"EVENT:\n{event_text}\n\n"
        f"ADVISOR AXES (from persona block):\n"
        f"  ethics: {a.get('ethics', 0):+.2f} "
        f"({'idealist' if a.get('ethics', 0) > 0 else 'pragmatist'})\n"
        f"  temperament: {a.get('temperament', 0):+.2f} "
        f"({'bold' if a.get('temperament', 0) > 0 else 'cautious'})\n"
        f"  loyalty: {a.get('loyalty', 0):+.2f} "
        f"({'collective' if a.get('loyalty', 0) > 0 else 'individual'})\n\n"
        f"PROPOSAL (flavor={proposal.flavor}):\n"
        f"  name: {proposal.name}\n"
        f"  description: {proposal.description}\n"
        f"  effects: {' | '.join(proposal.effects_preview)}\n\n"
        "Score whether the proposal embodies the advisor's axes. "
        'Return JSON: {"score": 0|1, "reasoning": "one sentence"}'
    )


async def score_proposal_attribution(
    *,
    event_text: str,
    proposal: ProposalForJudge,
    anthropic_client: Any | None = None,
) -> ProposalAttributionObservation:
    """Score one proposal for axis-attribution correctness.

    Binary {0, 1} but stored as float for aggregation. Unparseable judge
    output yields 0.5 with a rationale prefix "judge response unparseable:".
    """
    api = anthropic_client if anthropic_client is not None else _client
    user_message = _build_user_message(event_text, proposal)

    completion = await api.messages.create(
        model=MODEL_HAIKU,
        max_tokens=80,
        system=_JUDGE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    raw = _extract_text(completion)
    score, rationale, err = _parse_judge_json(raw)
    if err is not None:
        logger.warning("axis_attribution judge unparseable: %s; raw=%r", err, raw[:200])
        return ProposalAttributionObservation(
            advisor_id=proposal.advisor_id,
            score=0.5,
            rationale=err,
        )
    assert score is not None
    clamped = max(0.0, min(1.0, score))
    snapped = 1.0 if clamped >= 0.5 else 0.0
    return ProposalAttributionObservation(
        advisor_id=proposal.advisor_id,
        score=snapped,
        rationale=rationale,
    )


async def score_turn_attribution(
    *,
    event_text: str,
    proposals: list[ProposalForJudge],
    anthropic_client: Any | None = None,
) -> TurnAttributionResult:
    """Score a turn's three proposals; return per-proposal + mean."""
    if len(proposals) != 3:
        raise ValueError(f"expected exactly 3 proposals, got {len(proposals)}")

    sem = asyncio.Semaphore(_CONCURRENCY_CAP)

    async def _score_one(p: ProposalForJudge) -> ProposalAttributionObservation:
        async with sem:
            return await score_proposal_attribution(
                event_text=event_text,
                proposal=p,
                anthropic_client=anthropic_client,
            )

    observations = list(await asyncio.gather(*(_score_one(p) for p in proposals)))
    mean = sum(o.score for o in observations) / len(observations)
    return TurnAttributionResult(score=mean, per_proposal=observations)
