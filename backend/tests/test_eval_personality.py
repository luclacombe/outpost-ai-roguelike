"""Tests for app.eval.personality.

The real sentence-transformers model is never loaded here — tests inject a
fake encoder via the `model` argument or monkeypatch `_get_model`. The real
~80MB download path is exercised manually (see PR notes), not in CI.

The Anthropic client is also never hit — Haiku-as-judge tests inject a stub
client whose `messages.create` returns a synthetic response object.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import numpy as np
import pytest

from app.eval.personality import (
    build_profile_text,
    score_personality_consistency,
    score_personality_consistency_embedding,
    score_personality_consistency_judge,
)
from app.game.schemas import Agent, Quadrant

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _agent(**over) -> Agent:
    base = {
        "id": uuid4(),
        "game_id": uuid4(),
        "name": "Vera",
        "role": "Quartermaster",
        "archetype_quadrant": Quadrant.ACCOUNTANT,
        "ethics": -0.6,
        "temperament": -0.3,
        "loyalty": 0.4,
        "portrait_seed": "vera-seed",
        "health": 100,
        "morale": 60,
        "grievance": 2,
        "is_alive": True,
        "verbal_tic": "calculates the margin",
    }
    base.update(over)
    return Agent(**base)


def _fake_encoder(vectors: list[list[float]]) -> MagicMock:
    """Return a fake encoder whose .encode returns the next chunk of vectors.

    Calls return numpy arrays. The list is sliced in call order: the first
    call returns vectors[0:1], the second vectors[1:1+n], etc., based on the
    length of the input list passed to `.encode`. This mirrors how the real
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
# build_profile_text
# ---------------------------------------------------------------------------


def test_build_profile_text_includes_required_fields():
    agent = _agent(name="Vera", role="Quartermaster")
    text = build_profile_text(agent)
    assert "Vera" in text
    assert "Quartermaster" in text
    assert "ACCOUNTANT" in text
    # Verbal tic should be present so judge/embedder picks up voice signal.
    assert "calculates the margin" in text
    # Stable run-axes only — no live state.
    assert "grievance" not in text.lower()
    assert "morale" not in text.lower()


def test_build_profile_text_word_count_in_range():
    agent = _agent()
    text = build_profile_text(agent)
    n_words = len(text.split())
    # Spec: 50-100-word character sheet. Allow a small tolerance either side
    # so future tweaks don't trigger a brittle failure.
    assert 40 <= n_words <= 130, f"profile_text was {n_words} words: {text!r}"


# ---------------------------------------------------------------------------
# Embedding score
# ---------------------------------------------------------------------------


def test_score_embedding_returns_high_for_in_character_responses():
    agent = _agent()
    # Profile vector + 3 in-character vectors all aligned along [1, 0, 0].
    encoder = _fake_encoder(
        [
            [1.0, 0.0, 0.0],  # profile
            [1.0, 0.0, 0.0],
            [0.99, 0.01, 0.0],
            [0.98, 0.02, 0.0],
        ]
    )
    score = score_personality_consistency_embedding(
        agent, ["resp1", "resp2", "resp3"], model=encoder
    )
    assert score > 0.95


def test_score_embedding_returns_low_for_out_of_character():
    agent = _agent()
    # Profile along [1, 0, 0]; responses orthogonal along [0, 1, 0].
    encoder = _fake_encoder(
        [
            [1.0, 0.0, 0.0],  # profile
            [0.0, 1.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 1.0, 0.0],
        ]
    )
    score = score_personality_consistency_embedding(agent, ["off1", "off2", "off3"], model=encoder)
    assert score == pytest.approx(0.0, abs=1e-6)


def test_score_embedding_handles_empty_responses():
    agent = _agent()
    encoder = MagicMock()  # should never be called
    score = score_personality_consistency_embedding(agent, [], model=encoder)
    assert score == 1.0
    encoder.encode.assert_not_called()


# ---------------------------------------------------------------------------
# Haiku-as-judge
# ---------------------------------------------------------------------------


def _fake_anthropic_response(text: str) -> SimpleNamespace:
    """Build the minimum shape that the judge consumes from the SDK response."""
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


async def test_judge_returns_score_and_rationale():
    agent = _agent()
    fake_client = SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(
                return_value=_fake_anthropic_response(
                    '{"score": 1.0, "reasoning": "stayed in voice and values."}'
                )
            )
        )
    )
    score, rationale = await score_personality_consistency_judge(
        agent, "We must conserve fuel — calculates the margin.", anthropic_client=fake_client
    )
    assert score == 1.0
    assert "voice" in rationale
    fake_client.messages.create.assert_awaited_once()


async def test_judge_handles_malformed_json():
    agent = _agent()
    fake_client = SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(return_value=_fake_anthropic_response("not-json-at-all"))
        )
    )
    score, rationale = await score_personality_consistency_judge(
        agent, "anything", anthropic_client=fake_client
    )
    assert score == 0.5
    assert rationale.startswith("judge response unparseable:")


async def test_judge_handles_score_clamped_to_rubric():
    """If the model returns 0.7, we accept it as-is (no rubric snap) but it
    must still be a float in [0, 1]. Out-of-range values fall back to 0.5."""
    agent = _agent()
    fake_client = SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(
                return_value=_fake_anthropic_response('{"score": 1.5, "reasoning": "weird"}')
            )
        )
    )
    score, rationale = await score_personality_consistency_judge(
        agent, "anything", anthropic_client=fake_client
    )
    assert score == 0.5
    assert "out of range" in rationale or "unparseable" in rationale


# ---------------------------------------------------------------------------
# Combined entry point
# ---------------------------------------------------------------------------


async def test_combined_score_without_judge(monkeypatch):
    agent = _agent()
    encoder = _fake_encoder(
        [
            [1.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
        ]
    )
    monkeypatch.setattr("app.eval.personality._get_model", lambda: encoder)

    result = await score_personality_consistency(agent, ["a", "b"], judge=False)
    assert result["judge_scores"] is None
    assert result["judge_mean"] is None
    assert result["n_responses"] == 2
    assert result["embedding_mean"] > 0.95


async def test_combined_score_with_judge(monkeypatch):
    agent = _agent()
    encoder = _fake_encoder(
        [
            [1.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
        ]
    )
    monkeypatch.setattr("app.eval.personality._get_model", lambda: encoder)

    fake_client = SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(
                return_value=_fake_anthropic_response(
                    '{"score": 1.0, "reasoning": "in character."}'
                )
            )
        )
    )
    monkeypatch.setattr("app.eval.personality._client", fake_client, raising=False)

    result = await score_personality_consistency(agent, ["a", "b"], judge=True)
    assert result["judge_scores"] == [1.0, 1.0]
    assert result["judge_mean"] == 1.0
    assert result["embedding_mean"] > 0.95
    assert result["n_responses"] == 2
