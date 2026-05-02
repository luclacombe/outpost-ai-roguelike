"""Tests for app.eval.axis_diversity."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.eval.axis_diversity import (
    AXIS_DIVERSITY_THRESHOLD,
    ProposalForJudge,
    score_axis_diversity,
)


def _proposal(advisor_id, **over) -> ProposalForJudge:
    base = {
        "advisor_id": advisor_id,
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


async def test_three_distinct_quadrants_scores_one():
    a, b, c = uuid4(), uuid4(), uuid4()
    proposals = [_proposal(a), _proposal(b), _proposal(c)]
    body = (
        '{"per_proposal": ['
        f'{{"advisor_id": "{a}", "quadrant": "PRAGMATIST_CAUTIOUS"}},'
        f'{{"advisor_id": "{b}", "quadrant": "IDEALIST_BOLD"}},'
        f'{{"advisor_id": "{c}", "quadrant": "IDEALIST_CAUTIOUS"}}'
        '], "reasoning": "three distinct"}'
    )
    result = await score_axis_diversity(
        event_text="A blizzard rolls in.",
        proposals=proposals,
        anthropic_client=_fake_client(body),
    )
    assert result.score == 1.0
    assert result.distinct_quadrants == 3
    assert len(result.per_proposal) == 3


async def test_all_same_quadrant_scores_one_third():
    a, b, c = uuid4(), uuid4(), uuid4()
    proposals = [_proposal(a), _proposal(b), _proposal(c)]
    body = (
        '{"per_proposal": ['
        f'{{"advisor_id": "{a}", "quadrant": "PRAGMATIST_CAUTIOUS"}},'
        f'{{"advisor_id": "{b}", "quadrant": "PRAGMATIST_CAUTIOUS"}},'
        f'{{"advisor_id": "{c}", "quadrant": "PRAGMATIST_CAUTIOUS"}}'
        '], "reasoning": "all flat"}'
    )
    result = await score_axis_diversity(
        event_text="A blizzard rolls in.",
        proposals=proposals,
        anthropic_client=_fake_client(body),
    )
    assert result.score == pytest.approx(1.0 / 3.0)
    assert result.distinct_quadrants == 1


async def test_two_distinct_quadrants_scores_two_thirds():
    a, b, c = uuid4(), uuid4(), uuid4()
    proposals = [_proposal(a), _proposal(b), _proposal(c)]
    body = (
        '{"per_proposal": ['
        f'{{"advisor_id": "{a}", "quadrant": "PRAGMATIST_CAUTIOUS"}},'
        f'{{"advisor_id": "{b}", "quadrant": "IDEALIST_BOLD"}},'
        f'{{"advisor_id": "{c}", "quadrant": "PRAGMATIST_CAUTIOUS"}}'
        '], "reasoning": "two distinct"}'
    )
    result = await score_axis_diversity(
        event_text="A blizzard rolls in.",
        proposals=proposals,
        anthropic_client=_fake_client(body),
    )
    assert result.score == pytest.approx(2.0 / 3.0)
    assert result.distinct_quadrants == 2


async def test_unparseable_judge_returns_neutral():
    a, b, c = uuid4(), uuid4(), uuid4()
    proposals = [_proposal(a), _proposal(b), _proposal(c)]
    result = await score_axis_diversity(
        event_text="A blizzard rolls in.",
        proposals=proposals,
        anthropic_client=_fake_client("not json"),
    )
    assert result.score == 0.5
    assert "unparseable" in result.rationale.lower()


async def test_threshold_constant_exposed():
    assert AXIS_DIVERSITY_THRESHOLD >= 0.0
    assert AXIS_DIVERSITY_THRESHOLD <= 1.0


async def test_wrong_proposal_count_raises():
    with pytest.raises(ValueError, match="expected exactly 3"):
        await score_axis_diversity(
            event_text="x",
            proposals=[_proposal(uuid4()), _proposal(uuid4())],
            anthropic_client=_fake_client(""),
        )
