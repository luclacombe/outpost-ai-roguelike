"""Aggregate game-balance metrics across a batch of simulated runs.

Pure-Python; no LLM. Inputs are `RunRecord` instances built by the
simulation runner (Task 5.3c). The metrics here are the diagnostic
side of the eval pipeline: they surface "the colony always dies on
turn 3" or "stockpile resource never moves" before the LLM judges run.
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from pydantic import BaseModel

from app.eval.types import RunRecord


class GameBalanceMetrics(BaseModel):
    """Aggregated balance signals over a batch of `RunRecord`s."""

    survival_rate: float
    """Fraction of runs ending with `final_player_health > 0`."""

    agent_survival_rate: float
    """Mean fraction of agents alive at end across runs."""

    resource_volatility: dict[str, float]
    """Resource id -> mean per-run population stddev."""

    forced_choice_rate: float
    """Mean per-run share of turns where only one viable proposal existed."""

    mean_turns_played: float
    mean_cost_cents: float


def _empty_metrics() -> GameBalanceMetrics:
    return GameBalanceMetrics(
        survival_rate=0.0,
        agent_survival_rate=0.0,
        resource_volatility={},
        forced_choice_rate=0.0,
        mean_turns_played=0.0,
        mean_cost_cents=0.0,
    )


def game_balance_metrics(runs: list[RunRecord]) -> GameBalanceMetrics:
    """Aggregate balance metrics over a batch of runs.

    An empty `runs` list returns all-zero metrics rather than raising — the
    caller (eval pipeline) prefers a no-op rollup over a crash.
    """
    if not runs:
        return _empty_metrics()

    n = len(runs)

    survival_rate = sum(1 for r in runs if r.final_player_health > 0) / n

    def _agent_alive_fraction(r: RunRecord) -> float:
        if r.total_agents == 0:
            return 0.0
        return r.agents_alive_at_end / r.total_agents

    agent_survival_rate = sum(_agent_alive_fraction(r) for r in runs) / n

    # Per-resource population stddev per run, averaged across runs.
    per_resource_stdevs: dict[str, list[float]] = defaultdict(list)
    for r in runs:
        for resource_id, history in r.resource_history.items():
            if len(history) >= 1:
                # pstdev on a 1-element list returns 0.0; treat as no volatility.
                per_resource_stdevs[resource_id].append(statistics.pstdev(history))
    resource_volatility = {
        rid: (sum(stdevs) / len(stdevs)) if stdevs else 0.0
        for rid, stdevs in per_resource_stdevs.items()
    }

    def _forced_share(r: RunRecord) -> float:
        if r.turns_played == 0:
            return 0.0
        return r.forced_choice_turns / r.turns_played

    forced_choice_rate = sum(_forced_share(r) for r in runs) / n

    mean_turns_played = sum(r.turns_played for r in runs) / n
    mean_cost_cents = sum(r.cost_cents for r in runs) / n

    return GameBalanceMetrics(
        survival_rate=survival_rate,
        agent_survival_rate=agent_survival_rate,
        resource_volatility=resource_volatility,
        forced_choice_rate=forced_choice_rate,
        mean_turns_played=mean_turns_played,
        mean_cost_cents=mean_cost_cents,
    )
