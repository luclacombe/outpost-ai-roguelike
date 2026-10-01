"""Paired comparison across players on the same (setting, seed) cell.

The "do decisions matter" signal: the harness gives every persona (play_test)
or policy (sweep) the same starting colony and RNG streams per cell (see
`app/eval/playtest/seeds.py`), so outcome differences inside a cell come
from the choices plus engine-LLM sampling noise. If cells rarely differ,
choices are cosmetic.

Input rows are plain dicts so both harnesses can feed it:
  player, setting, seed, end_state, final_legitimacy, final_resource_frac,
  turns_played
"""

from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any

from app.game.schemas import ColonyState


def resource_fraction(colony: ColonyState) -> float:
    """Mean of value / max over the colony's resources (0 = empty, 1 = full)."""
    res = list(colony.resources.values())
    if not res:
        return 0.0
    return sum(r.value / max(1, r.max) for r in res) / len(res)


def _range(values: list[Any]) -> float | int | None:
    if any(v is None for v in values) or not values:
        return None
    return max(values) - min(values)


def _mean_or_none(values: list[Any]) -> float | None:
    present = [v for v in values if v is not None]
    return mean(present) if present else None


def paired_comparison(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-cell spread of outcomes across players + a corpus summary."""
    by_cell: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_cell[(r["setting"], r["seed"])].append(r)

    cells: list[dict[str, Any]] = []
    for (setting, seed), group in sorted(by_cell.items()):
        end_states = {r["player"]: r["end_state"] for r in group}
        cells.append(
            {
                "setting": setting,
                "seed": seed,
                "n_players": len(group),
                "end_states": end_states,
                "end_states_differ": len(set(end_states.values())) > 1,
                "legitimacy_range": _range([r.get("final_legitimacy") for r in group]),
                "resource_frac_range": _range([r.get("final_resource_frac") for r in group]),
                "turns_range": _range([r.get("turns_played") for r in group]),
            }
        )

    paired = [c for c in cells if c["n_players"] >= 2]
    summary = {
        "n_paired_cells": len(paired),
        "share_end_state_differs": (
            sum(c["end_states_differ"] for c in paired) / len(paired) if paired else None
        ),
        "mean_legitimacy_range": _mean_or_none([c["legitimacy_range"] for c in paired]),
        "mean_resource_frac_range": _mean_or_none([c["resource_frac_range"] for c in paired]),
        "mean_turns_range": _mean_or_none([c["turns_range"] for c in paired]),
    }
    return {"cells": cells, "summary": summary}
