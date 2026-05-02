"""Tests for app.eval.axis_attribution."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.eval.axis_attribution import (
    AXIS_ATTRIBUTION_THRESHOLD,
    score_proposal_attribution,
    score_turn_attribution,
)
from app.eval.axis_diversity import ProposalForJudge


def _proposal(**over) -> ProposalForJudge:
    base = {
        "advisor_id": uuid4(),
        "advisor_axes": {"ethics": -0.6, "temperament": -0.4, "loyalty": 0.3},
        "name": "Ration the fuel",
        "description": "Cut consumption to 60% of current rate.",
        "effects_preview": ["-0 fuel/turn", "+0 morale"],
        "flavor": "DOMINANT",
    }
    base.update(over)
    return ProposalForJudge(**base)


def _fake_response(text: str) -> SimpleNamespace:
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


def _fake_client(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        messages=SimpleNamespace(create=AsyncMock(return_value=_fake_response(text)))
    )


async def test_proposal_match_scores_one():
    obs = await score_proposal_attribution(
        event_text="A blizzard rolls in.",
        proposal=_proposal(),
        anthropic_client=_fake_client('{"score": 1, "reasoning": "matches axes"}'),
    )
    assert obs.score == 1.0
    assert obs.rationale == "matches axes"


async def test_proposal_mismatch_scores_zero():
    obs = await score_proposal_attribution(
        event_text="A blizzard rolls in.",
        proposal=_proposal(),
        anthropic_client=_fake_client('{"score": 0, "reasoning": "wrong values"}'),
    )
    assert obs.score == 0.0


async def test_unparseable_returns_half():
    obs = await score_proposal_attribution(
        event_text="A blizzard rolls in.",
        proposal=_proposal(),
        anthropic_client=_fake_client("nonsense"),
    )
    assert obs.score == 0.5


async def test_score_turn_means_three_observations():
    proposals = [_proposal() for _ in range(3)]
    client = SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(
                side_effect=[
                    _fake_response('{"score": 1, "reasoning": "yes"}'),
                    _fake_response('{"score": 1, "reasoning": "yes"}'),
                    _fake_response('{"score": 0, "reasoning": "no"}'),
                ]
            )
        )
    )
    result = await score_turn_attribution(
        event_text="A blizzard rolls in.",
        proposals=proposals,
        anthropic_client=client,
    )
    assert result.score == pytest.approx(2.0 / 3.0)
    assert len(result.per_proposal) == 3


async def test_threshold_constant_exposed():
    assert 0.0 <= AXIS_ATTRIBUTION_THRESHOLD <= 1.0


async def test_wrong_proposal_count_raises():
    with pytest.raises(ValueError, match="expected exactly 3"):
        await score_turn_attribution(
            event_text="x",
            proposals=[_proposal(), _proposal()],
            anthropic_client=_fake_client(""),
        )


async def test_out_of_range_score_clamps():
    """A judge that emits score outside [0, 1] is clamped before snapping.

    Without clamp, score=2.5 would silently snap to 1.0 (truthy >= 0.5)
    and score=-0.3 would silently snap to 0.0 — same surface, no warning.
    """
    obs = await score_proposal_attribution(
        event_text="x",
        proposal=_proposal(),
        anthropic_client=_fake_client('{"score": 2.5, "reasoning": "weird"}'),
    )
    assert obs.score == 1.0  # clamped, not silently inflated past 1

    obs = await score_proposal_attribution(
        event_text="x",
        proposal=_proposal(),
        anthropic_client=_fake_client('{"score": -0.3, "reasoning": "weird"}'),
    )
    assert obs.score == 0.0
