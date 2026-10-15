"""Choice-spread oracle: how different are the three options, mechanically?

At a paused turn, apply each offered proposal to its own copy of the colony
through the same path `nodes.resolve` uses (`preview.simulate_option`).
Pure: no LLM, no DB, no LangGraph checkpoint forking (`advance_turn` is the sole DB-write node, so
forking the graph would clobber the real game row).

Scope: the immediate resolution only. End-of-turn decay is excluded (it is
the same for every option and shown once in the UI); autonomous actions and
seeds come after the pick and are not simulated.

Spread metric: each option becomes a vector of
  [Δresource / resource.max for each resource (sorted id)] + [Δlegitimacy / 100,
   Δplayer_health / 100].
`spread_l1_max` is the largest pairwise L1 distance between option vectors
(0 = the choice changes nothing; 1.0 ≈ one full resource bar of difference).
`spread_l1_mean` is the mean pairwise distance.
"""

from __future__ import annotations

from itertools import combinations
from statistics import mean, median
from typing import Any

from app.game.preview import simulate_option
from app.game.schemas import ColonyState, Proposal


def _l1(a: list[float], b: list[float]) -> float:
    return sum(abs(x - y) for x, y in zip(a, b, strict=True))


def compute_choice_spread(colony: ColonyState, proposals: list[Proposal]) -> dict[str, Any]:
    """Per-option outcomes + spread for one paused turn. Never mutates input."""
    options = [simulate_option(colony, p, proposals).to_spread_dict(p) for p in proposals]
    dists = [_l1(a["vector"], b["vector"]) for a, b in combinations(options, 2)]
    spread_max = max(dists) if dists else 0.0
    return {
        "turn_number": colony.turn_number,
        "options": options,
        "spread_l1_max": spread_max,
        "spread_l1_mean": mean(dists) if dists else 0.0,
        "identical_options": spread_max == 0.0,
        "any_option_ends_run": any(o["ends_run"] is not None for o in options),
    }


def summarize_choice_spread(turns: list[dict[str, Any]]) -> dict[str, Any]:
    """Run-level roll-up of per-turn spread records.

    `picked_*` fields use `picked_proposal_id`, which the harness stamps on
    each turn record after the player chooses. They capture resolve-time
    rejections that `turn_logs` never persists. Records carrying `error`
    (the harness hook failed for that turn) are counted, not summarized.
    """
    errors = sum(1 for t in turns if "error" in t)
    turns = [t for t in turns if "error" not in t]
    if not turns:
        return {"n_turns": 0, "oracle_errors": errors}
    spreads = [t["spread_l1_max"] for t in turns]
    picked_calls = 0
    picked_rejected = 0
    for t in turns:
        pid = t.get("picked_proposal_id")
        opt = next((o for o in t["options"] if o["proposal_id"] == pid), None)
        if opt is not None:
            picked_calls += opt["n_calls"]
            picked_rejected += len(opt["rejected_calls"])
    return {
        "n_turns": len(turns),
        "spread_l1_max_mean": mean(spreads),
        "spread_l1_max_median": median(spreads),
        "spread_l1_max_min": min(spreads),
        "zero_spread_turns": sum(1 for t in turns if t["identical_options"]),
        "turns_with_run_ending_option": sum(1 for t in turns if t["any_option_ends_run"]),
        "picked_calls": picked_calls,
        "picked_rejected_calls": picked_rejected,
        "oracle_errors": errors,
    }
