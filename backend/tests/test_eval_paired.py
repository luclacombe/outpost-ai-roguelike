"""Paired comparison: same (setting, seed) cell, different players."""

from __future__ import annotations

from uuid import UUID

import pytest

from app.eval.paired import paired_comparison, resource_fraction
from app.game.generator import generate_run


def _row(player: str, seed: int, end: str | None, legit: int, frac: float, turns: int) -> dict:
    return {
        "player": player,
        "setting": "arctic_station_v1",
        "seed": seed,
        "end_state": end,
        "final_legitimacy": legit,
        "final_resource_frac": frac,
        "turns_played": turns,
    }


def test_cells_group_by_setting_and_seed() -> None:
    rows = [
        _row("moral", 1, "ENDURED", 70, 0.6, 9),
        _row("pragmatic", 1, "COLLAPSED", 40, 0.2, 6),
        _row("random", 1, "ENDURED", 55, 0.5, 9),
        _row("moral", 2, "ENDURED", 60, 0.5, 10),
        _row("pragmatic", 2, "ENDURED", 62, 0.5, 10),
    ]
    out = paired_comparison(rows)
    cells = {(c["setting"], c["seed"]): c for c in out["cells"]}
    c1 = cells[("arctic_station_v1", 1)]
    assert c1["n_players"] == 3
    assert c1["end_states_differ"] is True
    assert c1["end_states"] == {"moral": "ENDURED", "pragmatic": "COLLAPSED", "random": "ENDURED"}
    assert c1["legitimacy_range"] == 30
    assert c1["resource_frac_range"] == pytest.approx(0.4)
    assert c1["turns_range"] == 3
    assert cells[("arctic_station_v1", 2)]["end_states_differ"] is False

    s = out["summary"]
    assert s["n_paired_cells"] == 2
    assert s["share_end_state_differs"] == pytest.approx(0.5)
    assert s["mean_legitimacy_range"] == pytest.approx(16.0)
    assert s["mean_turns_range"] == pytest.approx(1.5)


def test_single_player_cells_are_not_paired() -> None:
    out = paired_comparison([_row("moral", 1, "ENDURED", 70, 0.6, 9)])
    assert out["summary"]["n_paired_cells"] == 0
    assert out["summary"]["share_end_state_differs"] is None


def test_missing_numbers_are_tolerated() -> None:
    rows = [
        _row("moral", 1, None, 70, 0.6, 9),
        {**_row("random", 1, "KILLED", 0, 0.0, 3), "final_legitimacy": None},
    ]
    cell = paired_comparison(rows)["cells"][0]
    assert cell["end_states_differ"] is True
    assert cell["legitimacy_range"] is None


def test_resource_fraction_is_mean_of_value_over_max() -> None:
    colony = generate_run(
        user_id=UUID("bbbbbbbb-0000-0000-0000-000000000002"),
        setting_id="arctic_station_v1",
        seed=1,
    )
    expected = sum(r.value / r.max for r in colony.resources.values()) / len(colony.resources)
    assert resource_fraction(colony) == pytest.approx(expected)
