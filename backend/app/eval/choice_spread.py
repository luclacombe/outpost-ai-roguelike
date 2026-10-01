"""Choice-spread oracle: how different are the three options, mechanically?

At a paused turn, apply each offered proposal to its own copy of the colony
through the same path `nodes.resolve` uses
(`engine.derive_resolution_consequences` + `engine.apply_tool_calls` on
`tool_calls_on_pick`), then `engine.check_deaths`. Pure: no LLM, no DB, no
LangGraph checkpoint forking (`advance_turn` is the sole DB-write node, so
forking the graph would clobber the real game row).

Scope: the immediate resolution only. Autonomous actions, narration,
consequence seeds and `advance_turn` decay come after and are not
simulated, so this measures what the choice itself does.

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

from app.game import engine
from app.game.schemas import ColonyState, Proposal


def _agent_status(agent: Any) -> str:
    return "DEAD" if not agent.is_alive else str(agent.status.value)


def _option_outcome(
    colony: ColonyState, picked: Proposal, proposals: list[Proposal]
) -> dict[str, Any]:
    before = colony.model_copy(deep=True)
    derived = engine.derive_resolution_consequences(before, picked, proposals)
    calls = [*picked.tool_calls_on_pick, *derived]
    after = engine.apply_tool_calls(before, calls)
    after = engine.check_deaths(after)

    new_calls = after.turn_tool_calls[len(before.turn_tool_calls) :]
    rejected = [
        {"name": tc.name, "reason": tc.rejection_reason}
        for tc in new_calls
        if tc.rejection_reason is not None
    ]

    resource_ids = sorted(before.resources)
    resource_deltas = {
        rid: after.resources[rid].value - before.resources[rid].value for rid in resource_ids
    }
    legitimacy_delta = after.legitimacy - before.legitimacy
    player_health_delta = after.player_health - before.player_health

    before_agents = {a.id: a for a in before.agents}
    grievance_deltas: dict[str, int] = {}
    status_changes: list[dict[str, str]] = []
    for a in after.agents:
        prev = before_agents.get(a.id)
        if prev is None:
            continue
        if a.grievance != prev.grievance:
            grievance_deltas[a.name] = a.grievance - prev.grievance
        if _agent_status(a) != _agent_status(prev):
            status_changes.append(
                {"agent": a.name, "from": _agent_status(prev), "to": _agent_status(a)}
            )

    vector = [resource_deltas[rid] / max(1, before.resources[rid].max) for rid in resource_ids]
    vector += [legitimacy_delta / 100, player_health_delta / 100]

    end_state = engine.compute_end_state(after)
    return {
        "proposal_id": picked.id,
        "flavor": str(picked.flavor.value),
        "name": picked.name,
        "resource_deltas": resource_deltas,
        "legitimacy_delta": legitimacy_delta,
        "player_health_delta": player_health_delta,
        "grievance_deltas": grievance_deltas,
        "status_changes": status_changes,
        "n_calls": len(new_calls),
        "rejected_calls": rejected,
        "ends_run": end_state.value if end_state is not None else None,
        "vector": vector,
    }


def _l1(a: list[float], b: list[float]) -> float:
    return sum(abs(x - y) for x, y in zip(a, b, strict=True))


def compute_choice_spread(colony: ColonyState, proposals: list[Proposal]) -> dict[str, Any]:
    """Per-option outcomes + spread for one paused turn. Never mutates input."""
    options = [_option_outcome(colony, p, proposals) for p in proposals]
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
    rejections that `turn_logs` never persists.
    """
    if not turns:
        return {"n_turns": 0}
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
    }
