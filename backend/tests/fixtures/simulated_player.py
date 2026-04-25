"""Deterministic simulated player for the Outpost eval pipeline.

The player's `pick` is the only decision function — given the three
proposals offered at pause, return the chosen `proposal_id`. Three policies:

  - "moral":     prefer MORAL, fall back to DOMINANT, then WILDCARD.
  - "pragmatic": prefer DOMINANT, fall back to MORAL, then WILDCARD.
  - "random":    seeded `random.choice` for reproducibility across runs.

`pick` accepts either `Proposal` / `ProposalPublic` Pydantic models OR plain
dicts of the same shape — the SSE `proposals_ready` payload ships dicts.
"""

from __future__ import annotations

import random
from typing import Any, Literal

from app.game.schemas import ProposalFlavor

Policy = Literal["moral", "pragmatic", "random"]

_PRIORITY: dict[Policy, tuple[ProposalFlavor, ...]] = {
    "moral": (ProposalFlavor.MORAL, ProposalFlavor.DOMINANT, ProposalFlavor.WILDCARD),
    "pragmatic": (ProposalFlavor.DOMINANT, ProposalFlavor.MORAL, ProposalFlavor.WILDCARD),
}


def _flavor_of(proposal: Any) -> ProposalFlavor:
    """Pull `flavor` off a Pydantic model or dict and coerce to enum."""
    raw = proposal["flavor"] if isinstance(proposal, dict) else proposal.flavor
    if isinstance(raw, ProposalFlavor):
        return raw
    return ProposalFlavor(raw)


def _id_of(proposal: Any) -> str:
    if isinstance(proposal, dict):
        return str(proposal["id"])
    return str(proposal.id)


class SimulatedPlayer:
    """Deterministic policy that picks among offered proposals.

    Construct with a seed so the random policy is reproducible across runs.
    The "moral" / "pragmatic" policies ignore the seed.
    """

    def __init__(self, policy: Policy, *, seed: int = 0) -> None:
        if policy not in {"moral", "pragmatic", "random"}:
            raise ValueError(
                f"unknown policy {policy!r}; expected one of moral / pragmatic / random"
            )
        self.policy: Policy = policy
        self.seed = seed
        # Per-instance Random — never touch global module state.
        self._rng = random.Random(seed)

    def pick(self, proposals: list[Any]) -> str:
        """Return the chosen proposal's `id` per the policy."""
        if not proposals:
            raise ValueError("pick called with empty proposals list")

        if self.policy == "random":
            return _id_of(self._rng.choice(proposals))

        priority = _PRIORITY[self.policy]
        for target in priority:
            for p in proposals:
                if _flavor_of(p) == target:
                    return _id_of(p)
        # All proposals were of unknown flavor — defensive fallback to the first.
        return _id_of(proposals[0])
