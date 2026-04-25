"""Narrative Repetition Index — pairwise BLEU-2 similarity over agent prose.

A high index across responses suggests the agent is recycling phrasing
(an early sign of cache collapse or under-conditioning). Pure-Python via
NLTK's BLEU implementation; no LLM cost.

Tokenisation is intentionally crude (whitespace + lowercase + ASCII filter)
because BLEU-2 only needs unigram and bigram overlap, and the responses are
short. We use NLTK `SmoothingFunction().method1` so degenerate-but-not-zero
overlaps don't crash with `ZeroDivisionError`.
"""

from __future__ import annotations

from itertools import combinations

from nltk.translate.bleu_score import SmoothingFunction, sentence_bleu

REPETITION_THRESHOLD = 0.35
"""Above this mean BLEU-2, the run is flagged as repetitive for review."""

_SMOOTHING = SmoothingFunction().method1
_BLEU2_WEIGHTS = (0.5, 0.5, 0.0, 0.0)


def _tokenize(text: str) -> list[str]:
    """Lowercase, whitespace-split, ASCII-only filter.

    Drops tokens with any non-ASCII character. Good enough for English
    narrative prose; wider Unicode handling can come if/when settings ship
    in other languages.
    """
    return [tok for tok in text.lower().split() if tok.isascii()]


def _bleu2(reference: list[str], hypothesis: list[str]) -> float:
    """BLEU-2 between two token lists, smoothed with method1."""
    if not reference or not hypothesis:
        return 0.0
    return float(
        sentence_bleu(
            [reference],
            hypothesis,
            weights=_BLEU2_WEIGHTS,
            smoothing_function=_SMOOTHING,
        )
    )


def narrative_repetition_index(responses: list[str]) -> float:
    """Mean BLEU-2 similarity across all unique pairs of `responses`.

    Returns a float in [0.0, 1.0]. With 0 or 1 responses there are no
    pairs, so we return 0.0 (no signal).
    """
    if len(responses) < 2:
        return 0.0
    tokens = [_tokenize(r) for r in responses]
    scores: list[float] = []
    for i, j in combinations(range(len(tokens)), 2):
        scores.append(_bleu2(tokens[i], tokens[j]))
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def repetitive_pairs(
    responses: list[str], *, threshold: float = REPETITION_THRESHOLD
) -> list[tuple[int, int, float]]:
    """Return `(i, j, bleu2)` tuples for every pair scoring above `threshold`.

    Surfaces the specific duplicates rather than collapsing them to a mean.
    Pairs are returned in lexicographic `(i, j)` order with `i < j`.
    """
    if len(responses) < 2:
        return []
    tokens = [_tokenize(r) for r in responses]
    out: list[tuple[int, int, float]] = []
    for i, j in combinations(range(len(tokens)), 2):
        score = _bleu2(tokens[i], tokens[j])
        if score > threshold:
            out.append((i, j, score))
    return out
