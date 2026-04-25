"""Personality consistency metric.

Two layers, both spec'd in `docs/research/05-eval-observability.md`:

1. **Embedding cosine similarity** (deterministic, $0 LLM cost). We embed the
   agent's stable identity sheet and each response with sentence-transformers
   MiniLM-L6-v2, then average cosine similarity.
2. **Haiku-as-judge** (opt-in, ~$0.008/turn). A binary-anchored 0/0.5/1.0 rubric
   with a one-sentence rationale per response.

The threshold for run-level personality consistency is 0.72 (root CLAUDE.md
invariant #10). Drift between turns 1-4 vs 8-12 is the interesting signal —
that comparison is left to the eval pipeline; this module just computes the
per-call score.

Anti-patterns to avoid:
- Loading the SentenceTransformer at module import. The 80MB download is lazy.
- Hardcoding the model name in two places — `EMBEDDING_MODEL_NAME` is the
  single source of truth, including for the Dockerfile pre-cache layer.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import numpy as np

from app.agent.anthropic_client import client as _client
from app.agent.constants import MODEL_HAIKU
from app.game.schemas import Agent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — keep in sync with eval pipeline + Dockerfile
# ---------------------------------------------------------------------------

PERSONALITY_THRESHOLD = 0.72
"""Run-level pass threshold per ARCHITECTURE §12 / SPEC eval gate."""

EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
"""384-dim sentence-transformers model. ~80MB; cached on disk after first use.
If you change this, also update the Dockerfile pre-cache RUN line."""

JUDGE_SYSTEM_PROMPT = (
    "You are evaluating whether an AI agent in a narrative game stayed in "
    "character. Respond with JSON only, matching the schema "
    '{"score": 0.0|0.5|1.0, "reasoning": "one sentence"}. '
    "Use the binary-anchored rubric: 1.0 = fully in character; "
    "0.5 = somewhat in character but generic; 0.0 = clearly out of character."
)


# Lazy-loaded model. Tests should never trigger this path — they pass an
# explicit `model=` to the embedding scorer or monkeypatch `_get_model`.
_model: Any = None


def _get_model() -> Any:
    """Lazy-load the SentenceTransformer once per process.

    The first call downloads ~80MB on a cold cache. We set
    SENTENCE_TRANSFORMERS_HOME to a sensible default if unset so the cache
    persists across runs in containers that mount it.
    """
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer  # heavy import

        os.environ.setdefault(
            "SENTENCE_TRANSFORMERS_HOME",
            os.path.expanduser("~/.cache/sentence-transformers"),
        )
        logger.info("loading sentence-transformers model %s", EMBEDDING_MODEL_NAME)
        _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _model


# ---------------------------------------------------------------------------
# Profile rendering
# ---------------------------------------------------------------------------


def _axis_label(axis: str, value: float) -> str:
    """Render a personality axis value as a human-readable phrase."""
    poles = {
        "ethics": ("pragmatic", "idealistic"),
        "temperament": ("cautious", "bold"),
        "loyalty": ("individualist", "collectivist"),
    }
    low, high = poles[axis]
    if value <= -0.4:
        return f"strongly {low}"
    if value < 0:
        return f"leans {low}"
    if value == 0:
        return "balanced"
    if value < 0.4:
        return f"leans {high}"
    return f"strongly {high}"


def build_profile_text(agent: Agent) -> str:
    """Render a 50-100-word stable character sheet for embedding/judging.

    Stable for the run — does NOT include grievance/morale/health, which drift
    turn-to-turn and would contaminate the consistency signal.
    """
    ethics = _axis_label("ethics", agent.ethics)
    temperament = _axis_label("temperament", agent.temperament)
    loyalty = _axis_label("loyalty", agent.loyalty)
    tic = agent.verbal_tic.strip() or "no signature mannerism"
    return (
        f"{agent.name} is the colony's {agent.role}, archetype {agent.archetype_quadrant.value}. "
        f"Their core values are {ethics} on ethics, {temperament} on temperament, "
        f"and {loyalty} on group loyalty. "
        f"They reason and speak from this stable identity — voice signature: {tic}. "
        f"In every situation, {agent.name} brings the priorities and verbal habits "
        f"of a {agent.archetype_quadrant.value.lower()}-archetype {agent.role}."
    )


# ---------------------------------------------------------------------------
# Embedding scorer
# ---------------------------------------------------------------------------


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)


def score_personality_consistency_embedding(
    agent: Agent,
    responses: list[str],
    *,
    model: Any | None = None,
) -> float:
    """Mean cosine similarity between profile and each response embedding.

    Returns a float in [-1.0, 1.0]; in practice ~0.0-0.95 for natural prose.
    Empty `responses` returns 1.0 — vacuously consistent, no signal to score.
    The optional `model` arg lets tests inject a fake encoder.
    """
    if not responses:
        return 1.0
    encoder = model if model is not None else _get_model()
    profile_text = build_profile_text(agent)
    profile_vec = np.asarray(encoder.encode([profile_text])[0], dtype=np.float32)
    response_vecs = np.asarray(encoder.encode(list(responses)), dtype=np.float32)
    sims = [_cosine(profile_vec, np.asarray(v, dtype=np.float32)) for v in response_vecs]
    return float(sum(sims) / len(sims))


# ---------------------------------------------------------------------------
# Haiku-as-judge
# ---------------------------------------------------------------------------


def _build_judge_user_message(agent: Agent, response: str, turn_number: int | None) -> str:
    profile = build_profile_text(agent)
    turn_line = (
        f"GAME CONTEXT (turn {turn_number}):" if turn_number is not None else "GAME CONTEXT:"
    )
    return (
        f"CHARACTER SHEET:\n{profile}\n\n"
        f"{turn_line} ongoing colony run.\n\n"
        f"AGENT'S RESPONSE:\n{response}\n\n"
        'Return JSON: {"score": 0.0|0.5|1.0, "reasoning": "one sentence"}'
    )


def _extract_text(response: Any) -> str:
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text":
            return getattr(block, "text", "") or ""
    return ""


async def score_personality_consistency_judge(
    agent: Agent,
    response: str,
    *,
    anthropic_client: Any | None = None,
    turn_number: int | None = None,
) -> tuple[float, str]:
    """Haiku judges a single response. Returns (score, one-sentence rationale).

    Score is in {0.0, 0.5, 1.0} per the binary-anchored rubric. On any parse
    or range failure, returns (0.5, "judge response unparseable: <err>").

    No prompt caching — this prompt is small and per-call; the cache write
    overhead would dominate.
    """
    api = anthropic_client if anthropic_client is not None else _client
    user_message = _build_judge_user_message(agent, response, turn_number)
    completion = await api.messages.create(
        model=MODEL_HAIKU,
        max_tokens=80,
        system=JUDGE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    raw = _extract_text(completion).strip()
    try:
        # Strip common code-fence wrappers if Haiku decorates the JSON.
        if raw.startswith("```"):
            raw = raw.strip("`")
            # remove an optional language tag like "json\n"
            if "\n" in raw:
                raw = raw.split("\n", 1)[1]
        parsed = json.loads(raw)
        score = float(parsed["score"])
        reasoning = str(parsed.get("reasoning", "")).strip() or "no rationale provided"
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as err:
        logger.warning("judge response unparseable: %s; raw=%r", err, raw[:200])
        return 0.5, f"judge response unparseable: {err}"
    if not (0.0 <= score <= 1.0):
        return 0.5, f"judge score out of range: {score}"
    return score, reasoning


# ---------------------------------------------------------------------------
# Combined entry point
# ---------------------------------------------------------------------------


async def score_personality_consistency(
    agent: Agent,
    responses: list[str],
    *,
    judge: bool = False,
) -> dict[str, Any]:
    """Combined scorer. Embedding always runs; Haiku judge is opt-in.

    Returns:
        {
            "embedding_mean": float,
            "judge_scores": list[float] | None,  # None when judge=False
            "judge_mean": float | None,
            "n_responses": int,
        }
    """
    embedding_mean = score_personality_consistency_embedding(agent, responses)

    judge_scores: list[float] | None = None
    judge_mean: float | None = None
    if judge and responses:
        judge_scores = []
        for resp in responses:
            score, _rationale = await score_personality_consistency_judge(agent, resp)
            judge_scores.append(score)
        judge_mean = float(sum(judge_scores) / len(judge_scores))

    return {
        "embedding_mean": embedding_mean,
        "judge_scores": judge_scores,
        "judge_mean": judge_mean,
        "n_responses": len(responses),
    }
