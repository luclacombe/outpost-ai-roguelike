"""Tests for the playtest harness's run allocation + setting-pinning logic.

Allocation is paired: seeds come from (setting, run_idx) via a stable hash,
and every persona plays the same (setting, seed) cells.

Setting pinning covers a failure mode from the 1-game smoke:
  * `_drive_one_game` used `uuid.uuid4()` directly, but the engine's
    deterministic per-user setting picker (`_pick_setting_id`) ignored the
    harness's requested setting — fresh uuids hashed to whatever setting
    the picker happened to return.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.eval.playtest.personas import PERSONAS
from app.eval.playtest.seeds import paired_cells
from app.game.service import _pick_setting_id
from scripts.play_test import PlaytestConfig, _allocate_runs, _uuid_for_setting


def test_allocate_runs_production_size_is_paired() -> None:
    """Production allocation (7 personas × 6 runs × 3 settings) = 42 games:
    2 seeds per setting, and every persona plays the SAME 6 (setting, seed)
    cells. That pairing is what lets the aggregator ask whether different
    players reach different outcomes on the same colony."""
    config = PlaytestConfig(
        personas=[p.id for p in PERSONAS],
        runs_per_persona=6,
        settings=["arctic_station_v1", "orbital_colony_v1", "desert_outpost_v1"],
    )

    allocations = _allocate_runs(config)
    assert len(allocations) == 42

    cells_by_persona: dict[str, list[tuple[str, int]]] = {}
    for persona_id, setting_id, seed in allocations:
        cells_by_persona.setdefault(persona_id, []).append((setting_id, seed))

    reference = cells_by_persona[config.personas[0]]
    assert len(set(reference)) == 6
    for persona in config.personas:
        assert cells_by_persona[persona] == reference
    per_setting = {s: sum(1 for cs, _ in reference if cs == s) for s in config.settings}
    assert per_setting == {s: 2 for s in config.settings}


def test_allocate_runs_seeds_are_stable_and_persona_independent() -> None:
    """Seeds depend on (setting, run_idx) only: no per-process hash salt,
    no persona term."""
    config = PlaytestConfig(personas=["optimizer", "pessimist"], runs_per_persona=3)
    first = _allocate_runs(config)
    assert first == _allocate_runs(config)
    assert [(s, sd) for p, s, sd in first if p == "optimizer"] == paired_cells(config.settings, 3)


def test_allocate_runs_small_n_shares_one_cell() -> None:
    """With runs_per_persona=1 every persona plays the same first cell
    (paired beats spreading one run per setting)."""
    config = PlaytestConfig(
        personas=["optimizer", "pessimist", "narrative_driven", "brinksmanship", "drop_off_risk"],
        runs_per_persona=1,
        settings=["arctic_station_v1", "orbital_colony_v1", "desert_outpost_v1"],
    )

    allocations = _allocate_runs(config)
    assert len(allocations) == 5
    assert {(s, sd) for _p, s, sd in allocations} == set(paired_cells(config.settings, 1))


def test_uuid_for_setting_returns_user_id_that_picker_maps_to_requested() -> None:
    """The harness must be able to drive a specific setting per game by
    generating a user_id whose `_pick_setting_id` hash matches the requested
    setting — otherwise per-cell aggregation is binomial-noisy."""
    now_utc = datetime.now(UTC)
    for setting_id in ("arctic_station_v1", "orbital_colony_v1", "desert_outpost_v1"):
        uid = _uuid_for_setting(setting_id, now_utc)
        assert _pick_setting_id(uid, now_utc) == setting_id


def test_uuid_for_setting_raises_for_unknown_setting() -> None:
    """An unknown setting id can never match the picker; fail loud rather
    than spinning the brute-force loop until max_attempts."""
    now_utc = datetime.now(UTC)
    with pytest.raises(ValueError, match="not in available settings"):
        _uuid_for_setting("nonexistent_v1", now_utc)
