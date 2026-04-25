"""Decision-coherence metric (Haiku-as-judge).

Asks Haiku whether each agent's chosen ACTION matches their stated REASONING
and stable personality. Produces 0/0.5/1.0 per (agent, turn). Spec:
`docs/research/05-eval-observability.md` §"Decision Coherence (LLM-as-Judge)".

A turn is *incoherent* when any agent on it scored below
`COHERENCE_THRESHOLD_PER_TURN`. A run is *flagged* when more than
`INCOHERENT_TURNS_BUDGET` turns are incoherent.

Cost ~$0.003/turn × 4 agents per run. Concurrency is capped at
`_CONCURRENCY_CAP` (4) so a 20-game eval batch doesn't burst the Anthropic
rate limit.

No prompt caching — per-call prompts are small and per-turn unique; cache-write
overhead would dominate. (Same trade-off as the personality judge.)
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
from app.eval.personality import build_profile_text
from app.eval.types import RunCoherenceInput
from app.game.schemas import Agent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Thresholds — single source of truth for the eval pipeline.
# ---------------------------------------------------------------------------

COHERENCE_THRESHOLD_PER_TURN = 0.6
"""Per-turn pass threshold. Below this, the turn counts as incoherent."""

INCOHERENT_TURNS_BUDGET = 2
"""A run is flagged when strictly more than this many turns are incoherent."""

_CONCURRENCY_CAP = 4
"""Cap on in-flight Haiku judge calls when scoring a whole run."""

_JUDGE_SYSTEM_PROMPT = (
    "You are evaluating whether an AI agent in a narrative game made a decision "
    "that coheres with their stated reasoning AND stable personality. Respond "
    'with JSON only, matching the schema {"score": 0.0|0.5|1.0, "reasoning": '
    '"one sentence"}. Use the binary-anchored rubric: 1.0 = action matches both '
    "reasoning and personality; 0.5 = partially aligned but generic or off-tone; "
    "0.0 = action contradicts the reasoning or the character's values."
)


# ---------------------------------------------------------------------------
# Output records
# ---------------------------------------------------------------------------


class TurnCoherenceObservation(BaseModel):
    """One judge observation: (agent, turn) → snapped score + rationale.

    The (reasoning, action) pair is preserved so dashboards can surface what
    was judged without re-running the eval.
    """

    agent_id: UUID
    turn_number: int
    score: float = Field(ge=0.0, le=1.0)
    rationale: str
    reasoning: str
    action: str


class RunCoherenceResult(BaseModel):
    """Aggregated coherence over a whole run."""

    observations: list[TurnCoherenceObservation]
    incoherent_turns: int
    flagged: bool
    mean_score: float


# ---------------------------------------------------------------------------
# Score snapping
# ---------------------------------------------------------------------------


def _snap_score(raw: float) -> float:
    """Clamp into [0, 1] then round to nearest of {0.0, 0.5, 1.0}.

    Tie-break: ties go to the higher anchor (0.25 -> 0.5, 0.75 -> 1.0). The
    test suite locks the boundary cases (0.3 -> 0.5, 0.7 -> 0.5, 0.85 -> 1.0).
    """
    clamped = max(0.0, min(1.0, raw))
    anchors = (0.0, 0.5, 1.0)
    # min by (distance, -anchor) so ties prefer the larger anchor.
    return min(anchors, key=lambda a: (abs(clamped - a), -a))


# ---------------------------------------------------------------------------
# Per-turn judge
# ---------------------------------------------------------------------------


def _build_user_message(agent: Agent, reasoning: str, action: str, turn_number: int) -> str:
    profile = build_profile_text(agent)
    return (
        f"CHARACTER SHEET:\n{profile}\n\n"
        f"GAME CONTEXT (turn {turn_number}, agent_id={agent.id}): ongoing colony run.\n\n"
        f"AGENT'S REASONING:\n{reasoning}\n\n"
        f"AGENT'S ACTION:\n{action}\n\n"
        "Score whether the ACTION coheres with the REASONING and the character "
        "sheet. Return JSON: "
        '{"score": 0.0|0.5|1.0, "reasoning": "one sentence"}'
    )


async def score_turn_coherence(
    *,
    agent: Agent,
    reasoning: str,
    action: str,
    turn_number: int,
    anthropic_client: Any | None = None,
) -> TurnCoherenceObservation:
    """Score one (agent, turn) for action-vs-reasoning coherence.

    Score is snapped to {0.0, 0.5, 1.0}. On parse failure, returns 0.5 with
    a rationale starting "judge response unparseable:". Never raises on the
    judge's output — only on transport / API errors from the client itself.
    """
    api = anthropic_client if anthropic_client is not None else _client
    user_message = _build_user_message(agent, reasoning, action, turn_number)

    completion = await api.messages.create(
        model=MODEL_HAIKU,
        max_tokens=80,
        system=_JUDGE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    raw = _extract_text(completion)
    score, rationale, err = _parse_judge_json(raw)
    if err is not None:
        logger.warning("coherence judge unparseable: %s; raw=%r", err, raw[:200])
        return TurnCoherenceObservation(
            agent_id=agent.id,
            turn_number=turn_number,
            score=0.5,
            rationale=err,
            reasoning=reasoning,
            action=action,
        )
    assert score is not None  # for type checkers
    snapped = _snap_score(score)
    return TurnCoherenceObservation(
        agent_id=agent.id,
        turn_number=turn_number,
        score=snapped,
        rationale=rationale,
        reasoning=reasoning,
        action=action,
    )


# ---------------------------------------------------------------------------
# Run-level scoring
# ---------------------------------------------------------------------------


async def score_run_coherence(
    run: RunCoherenceInput,
    *,
    anthropic_client: Any | None = None,
) -> RunCoherenceResult:
    """Score every turn in `run` and aggregate.

    Concurrency is capped at `_CONCURRENCY_CAP`. A bare `asyncio.gather` is
    forbidden in this module — the eval pipeline may run 20 games and a
    burst would risk rate-limiting.

    Real exceptions (rate-limit, network, programming bugs) propagate. Only
    judge-output parse failures are absorbed (into a 0.5 observation).
    """
    sem = asyncio.Semaphore(_CONCURRENCY_CAP)

    async def _score_one(turn) -> TurnCoherenceObservation:
        async with sem:
            agent = run.agents_by_id[turn.agent_id]
            return await score_turn_coherence(
                agent=agent,
                reasoning=turn.reasoning,
                action=turn.action,
                turn_number=turn.turn_number,
                anthropic_client=anthropic_client,
            )

    observations = await asyncio.gather(*(_score_one(t) for t in run.turns))

    # A turn is incoherent if ANY agent on it scored below threshold.
    incoherent_turn_numbers: set[int] = {
        obs.turn_number for obs in observations if obs.score < COHERENCE_THRESHOLD_PER_TURN
    }
    incoherent_turns = len(incoherent_turn_numbers)
    mean_score = (
        float(sum(o.score for o in observations) / len(observations)) if observations else 1.0
    )
    return RunCoherenceResult(
        observations=list(observations),
        incoherent_turns=incoherent_turns,
        flagged=incoherent_turns > INCOHERENT_TURNS_BUDGET,
        mean_score=mean_score,
    )
