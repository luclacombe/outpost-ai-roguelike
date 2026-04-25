"""Inter-run narrative similarity metric.

Measures stylistic/narrative drift OR sameness across multiple runs of the
SAME setting. We concatenate per-turn narration into a single document per
run, embed each document with the same MiniLM-L6-v2 model used by
`personality.py`, then take the mean pairwise cosine similarity across runs.

Threshold band `[0.55, 0.80]`:
- below low → "incoherent" (each run reads like a different game)
- above high → "repetitive" (the LLM is producing the same prose every run)
- inside the band → "healthy" stylistic consistency with novelty

This metric is engine-only at compute time (no LLM calls); it relies solely
on the embedding model already loaded by `personality._get_model`.

Spec: `docs/PLAN.md` Phase 5 / Task 5.3 step 6.
"""

from __future__ import annotations

from itertools import combinations
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel

from app.eval.personality import _cosine, _get_model
from app.eval.types import RunRecord

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INTER_RUN_SIM_BAND: tuple[float, float] = (0.55, 0.80)
"""[low, high] band — mean pairwise cosine outside this range flags."""

_NARRATION_SEPARATOR = "\n\n"


# ---------------------------------------------------------------------------
# Result schema
# ---------------------------------------------------------------------------


class InterRunSimilarityResult(BaseModel):
    """Result of `inter_run_narrative_similarity`."""

    setting_id: str
    n_runs: int
    mean_pairwise_cosine: float
    band: tuple[float, float]
    verdict: Literal["healthy", "incoherent", "repetitive", "insufficient_data"]
    pairs: list[tuple[int, int, float]]
    """All `i < j` index pairs with their cosine similarity, in lexicographic order."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def compose_run_text(run: RunRecord) -> str:
    """Concatenate per-turn narrations into a single string.

    Empty/whitespace-only entries are skipped. Returns an empty string when
    no usable narrations exist on the run.
    """
    parts = [n.strip() for n in run.narration_per_turn if n and n.strip()]
    return _NARRATION_SEPARATOR.join(parts)


def _verdict_for_mean(
    mean: float, band: tuple[float, float]
) -> Literal["healthy", "incoherent", "repetitive"]:
    low, high = band
    if mean < low:
        return "incoherent"
    if mean > high:
        return "repetitive"
    return "healthy"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def inter_run_narrative_similarity(
    runs: list[RunRecord],
    *,
    model: Any | None = None,
) -> InterRunSimilarityResult:
    """Mean pairwise cosine similarity of run-level narration embeddings.

    Args:
        runs: Run records, all of which must share the same `setting_id`.
        model: Optional injected encoder for tests. Production passes `None`
            and the function falls back to the lazy-loaded MiniLM via
            `personality._get_model`.

    Raises:
        ValueError: when runs span more than one `setting_id`. The caller
            must group runs by setting before calling this metric.

    Returns:
        `InterRunSimilarityResult` with the mean, all pairwise sims, and a
        verdict string. When fewer than two runs have non-empty narration,
        verdict is `"insufficient_data"` and `mean_pairwise_cosine` is `0.0`.
    """
    if not runs:
        return InterRunSimilarityResult(
            setting_id="",
            n_runs=0,
            mean_pairwise_cosine=0.0,
            band=INTER_RUN_SIM_BAND,
            verdict="insufficient_data",
            pairs=[],
        )

    setting_ids = {r.setting_id for r in runs}
    if len(setting_ids) > 1:
        raise ValueError(
            f"inter_run_narrative_similarity requires all runs share a setting_id; "
            f"got {sorted(setting_ids)!r}. Group runs by setting before calling."
        )
    setting_id = next(iter(setting_ids))

    # Compose then filter: only keep runs that actually have narration.
    composed = [compose_run_text(r) for r in runs]
    kept = [text for text in composed if text]
    n_kept = len(kept)

    if n_kept < 2:
        return InterRunSimilarityResult(
            setting_id=setting_id,
            n_runs=n_kept,
            mean_pairwise_cosine=0.0,
            band=INTER_RUN_SIM_BAND,
            verdict="insufficient_data",
            pairs=[],
        )

    encoder = model if model is not None else _get_model()
    vecs = np.asarray(encoder.encode(kept), dtype=np.float32)

    pairs: list[tuple[int, int, float]] = []
    for i, j in combinations(range(n_kept), 2):
        sim = _cosine(np.asarray(vecs[i], dtype=np.float32), np.asarray(vecs[j], dtype=np.float32))
        pairs.append((i, j, sim))

    mean = float(sum(s for _, _, s in pairs) / len(pairs))
    verdict = _verdict_for_mean(mean, INTER_RUN_SIM_BAND)

    return InterRunSimilarityResult(
        setting_id=setting_id,
        n_runs=n_kept,
        mean_pairwise_cosine=mean,
        band=INTER_RUN_SIM_BAND,
        verdict=verdict,
        pairs=pairs,
    )
