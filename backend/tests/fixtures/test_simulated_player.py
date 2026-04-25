"""Tests for the deterministic simulated-player policy.

Pure-logic unit tests — no graph, no DB. The eval pipeline tests in
`tests/eval/` exercise the same policy through the streaming HTTP surface.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.game.schemas import Proposal, ProposalFlavor, ToolCall
from app.game.schemas_io import ProposalPublic
from tests.fixtures.simulated_player import SimulatedPlayer


def _proposal(flavor: ProposalFlavor, idx: int = 0) -> Proposal:
    return Proposal(
        id=f"prop_{flavor.value.lower()}_{idx}",
        proposed_by_agent_id=uuid4(),
        flavor=flavor,
        name=f"{flavor.value} plan {idx}",
        description="A plan.",
        effects_preview=[],
        tool_calls_on_pick=[
            ToolCall(name="adjust_resource", caller_id=None, args={"resource_id": "x", "delta": 1})
        ],
    )


def _public(p: Proposal) -> dict:
    return ProposalPublic(
        id=p.id,
        proposed_by_agent_id=p.proposed_by_agent_id,
        flavor=p.flavor,
        name=p.name,
        description=p.description,
        effects_preview=p.effects_preview,
    ).model_dump(mode="json")


def test_moral_policy_prefers_moral_flavor() -> None:
    proposals = [
        _proposal(ProposalFlavor.DOMINANT),
        _proposal(ProposalFlavor.MORAL),
        _proposal(ProposalFlavor.WILDCARD),
    ]
    player = SimulatedPlayer("moral")
    chosen = player.pick(proposals)
    moral_id = next(p.id for p in proposals if p.flavor == ProposalFlavor.MORAL)
    assert chosen == moral_id


def test_moral_policy_falls_back_to_dominant_when_no_moral() -> None:
    proposals = [
        _proposal(ProposalFlavor.DOMINANT),
        _proposal(ProposalFlavor.WILDCARD),
    ]
    player = SimulatedPlayer("moral")
    chosen = player.pick(proposals)
    dominant_id = next(p.id for p in proposals if p.flavor == ProposalFlavor.DOMINANT)
    assert chosen == dominant_id


def test_moral_policy_falls_back_to_wildcard_last() -> None:
    proposals = [_proposal(ProposalFlavor.WILDCARD)]
    player = SimulatedPlayer("moral")
    chosen = player.pick(proposals)
    assert chosen == proposals[0].id


def test_pragmatic_policy_prefers_dominant_flavor() -> None:
    proposals = [
        _proposal(ProposalFlavor.MORAL),
        _proposal(ProposalFlavor.DOMINANT),
        _proposal(ProposalFlavor.WILDCARD),
    ]
    player = SimulatedPlayer("pragmatic")
    chosen = player.pick(proposals)
    dominant_id = next(p.id for p in proposals if p.flavor == ProposalFlavor.DOMINANT)
    assert chosen == dominant_id


def test_pragmatic_policy_falls_back_to_moral_then_wildcard() -> None:
    proposals = [
        _proposal(ProposalFlavor.MORAL),
        _proposal(ProposalFlavor.WILDCARD),
    ]
    chosen = SimulatedPlayer("pragmatic").pick(proposals)
    moral_id = next(p.id for p in proposals if p.flavor == ProposalFlavor.MORAL)
    assert chosen == moral_id


def test_random_policy_is_seeded_deterministic() -> None:
    proposals = [
        _proposal(ProposalFlavor.DOMINANT),
        _proposal(ProposalFlavor.MORAL),
        _proposal(ProposalFlavor.WILDCARD),
    ]
    a = SimulatedPlayer("random", seed=42).pick(proposals)
    b = SimulatedPlayer("random", seed=42).pick(proposals)
    assert a == b
    # Different seed should generally produce a different outcome across the
    # 3-element option space — pick a seed pair that we can pin behaviorally.
    c = SimulatedPlayer("random", seed=99).pick(proposals)
    assert c in {p.id for p in proposals}


def test_pick_accepts_dicts_from_sse_payload() -> None:
    proposals = [
        _proposal(ProposalFlavor.DOMINANT),
        _proposal(ProposalFlavor.MORAL),
        _proposal(ProposalFlavor.WILDCARD),
    ]
    payload = [_public(p) for p in proposals]
    player = SimulatedPlayer("moral")
    chosen = player.pick(payload)
    assert chosen == next(p.id for p in proposals if p.flavor == ProposalFlavor.MORAL)


def test_unknown_policy_raises() -> None:
    with pytest.raises(ValueError):
        SimulatedPlayer("zealot")  # type: ignore[arg-type]


def test_empty_proposals_raises() -> None:
    with pytest.raises(ValueError):
        SimulatedPlayer("moral").pick([])
