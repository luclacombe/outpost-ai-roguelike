"""Tests for app.eval.inter_run_similarity.

The real sentence-transformers model is never loaded — tests inject a fake
encoder via the `model=` kwarg. The metric is purely numeric, no LLM calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from app.eval.inter_run_similarity import (
    INTER_RUN_SIM_BAND,
    InterRunSimilarityResult,
    compose_run_text,
    inter_run_narrative_similarity,
)
from app.eval.types import RunRecord

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _run(
    *,
    setting_id: str = "starlight-station",
    narrations: list[str] | None = None,
    seed: int = 0,
) -> RunRecord:
    return RunRecord(
        setting_id=setting_id,
        policy="moral",
        seed=seed,
        end_state=None,
        turns_played=len(narrations or []),
        final_player_health=50,
        agents_alive_at_end=4,
        total_agents=4,
        cost_cents=10,
        narration_per_turn=list(narrations or []),
    )


def _fake_encoder(vectors: list[list[float]]) -> MagicMock:
    """Return a fake encoder whose `.encode` returns the next chunk of vectors.

    Calls return numpy arrays sliced in call order to mirror the way the real
    SentenceTransformer batches inputs.
    """
    arr = np.array(vectors, dtype=np.float32)
    cursor = {"i": 0}

    def _encode(texts, **_kwargs):
        n = len(texts)
        chunk = arr[cursor["i"] : cursor["i"] + n]
        cursor["i"] += n
        return chunk

    m = MagicMock()
    m.encode.side_effect = _encode
    return m


# ---------------------------------------------------------------------------
# compose_run_text
# ---------------------------------------------------------------------------


def test_compose_run_text_concatenates_turns_with_separator() -> None:
    run = _run(narrations=["alpha falls.", "beta holds.", "gamma drifts."])
    text = compose_run_text(run)
    assert "alpha falls." in text
    assert "beta holds." in text
    assert "gamma drifts." in text
    # All three appear in order
    assert text.index("alpha falls.") < text.index("beta holds.") < text.index("gamma drifts.")


def test_compose_run_text_skips_empty_strings() -> None:
    run = _run(narrations=["alpha.", "", "  ", "beta."])
    text = compose_run_text(run)
    assert "alpha." in text
    assert "beta." in text
    # No double-separator from empties
    assert "  " not in text.replace("alpha.", "").replace("beta.", "").strip() or text.strip() != ""

    empty_run = _run(narrations=[])
    assert compose_run_text(empty_run) == ""

    all_empty_run = _run(narrations=["", "   ", ""])
    assert compose_run_text(all_empty_run) == ""


# ---------------------------------------------------------------------------
# inter_run_narrative_similarity
# ---------------------------------------------------------------------------


def test_inter_run_similarity_healthy_band() -> None:
    # Three vectors with pairwise cosine sims that average inside [0.55, 0.80].
    # cos(v0,v1)=0.7, cos(v0,v2)=0.6, cos(v1,v2)=0.65 -> mean ~0.65
    v0 = [1.0, 0.0, 0.0]
    v1 = [0.7, np.sqrt(1 - 0.7**2), 0.0]  # cos with v0 = 0.7
    # Pick v2 so cos with v0 = 0.6 and cos with v1 ~= 0.65
    # Solve: v2 = [0.6, y, z] with norm 1; cos(v1,v2) = 0.7*0.6 + sqrt(0.51)*y
    # We want 0.65 -> 0.42 + 0.7141*y = 0.65 -> y = 0.322
    # then z = sqrt(1 - 0.36 - 0.1037) = sqrt(0.5363) = 0.7323
    v2 = [0.6, 0.322, 0.7323]
    encoder = _fake_encoder([v0, v1, v2])

    runs = [
        _run(narrations=["one"], seed=1),
        _run(narrations=["two"], seed=2),
        _run(narrations=["three"], seed=3),
    ]
    result = inter_run_narrative_similarity(runs, model=encoder)
    assert isinstance(result, InterRunSimilarityResult)
    assert result.verdict == "healthy"
    assert result.n_runs == 3
    assert INTER_RUN_SIM_BAND[0] <= result.mean_pairwise_cosine <= INTER_RUN_SIM_BAND[1]
    assert len(result.pairs) == 3  # C(3,2)
    indices = {(i, j) for i, j, _ in result.pairs}
    assert indices == {(0, 1), (0, 2), (1, 2)}


def test_inter_run_similarity_repetitive() -> None:
    # Identical vectors -> all pairwise cos = 1.0 -> repetitive
    same_vec = [0.0, 1.0, 0.0]
    encoder = _fake_encoder([same_vec, same_vec, same_vec])
    runs = [
        _run(narrations=["a"], seed=1),
        _run(narrations=["b"], seed=2),
        _run(narrations=["c"], seed=3),
    ]
    result = inter_run_narrative_similarity(runs, model=encoder)
    assert result.verdict == "repetitive"
    assert result.mean_pairwise_cosine == pytest.approx(1.0)
    assert len(result.pairs) == 3
    for _, _, sim in result.pairs:
        assert sim == pytest.approx(1.0)


def test_inter_run_similarity_incoherent() -> None:
    # Orthogonal vectors -> pairwise cos = 0.0 -> incoherent
    encoder = _fake_encoder(
        [
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ]
    )
    runs = [
        _run(narrations=["a"], seed=1),
        _run(narrations=["b"], seed=2),
        _run(narrations=["c"], seed=3),
    ]
    result = inter_run_narrative_similarity(runs, model=encoder)
    assert result.verdict == "incoherent"
    assert result.mean_pairwise_cosine == pytest.approx(0.0)


def test_inter_run_similarity_raises_on_mixed_settings() -> None:
    runs = [
        _run(setting_id="starlight-station", narrations=["a"]),
        _run(setting_id="moonbase", narrations=["b"]),
    ]
    with pytest.raises(ValueError):
        inter_run_narrative_similarity(runs, model=_fake_encoder([]))


def test_inter_run_similarity_insufficient_data_one_run() -> None:
    runs = [_run(narrations=["only one"], seed=1)]
    result = inter_run_narrative_similarity(runs, model=_fake_encoder([]))
    assert result.verdict == "insufficient_data"
    assert result.n_runs == 1
    assert result.mean_pairwise_cosine == 0.0
    assert result.pairs == []


def test_inter_run_similarity_insufficient_data_after_filter() -> None:
    runs = [
        _run(narrations=[], seed=1),
        _run(narrations=["", "  "], seed=2),
    ]
    result = inter_run_narrative_similarity(runs, model=_fake_encoder([]))
    assert result.verdict == "insufficient_data"
    assert result.n_runs == 0
    assert result.mean_pairwise_cosine == 0.0
    assert result.pairs == []
