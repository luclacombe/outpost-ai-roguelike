"""Tests for the playtest harness's run allocation + setting-pinning logic.

These cover the two failure modes uncovered during the 1-game smoke:
  * `_allocate_runs` did not rotate settings when `runs_per_persona < len(settings)`,
    so small smoke runs all collapsed onto `settings[0]`.
  * `_drive_one_game` used `uuid.uuid4()` directly, but the engine's
    deterministic per-user setting picker (`_pick_setting_id`) ignored the
    harness's requested setting — fresh uuids hashed to whatever setting
    the picker happened to return.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.eval.playtest.personas import PERSONAS
from app.game.service import _pick_setting_id
from scripts.play_test import PlaytestConfig, _allocate_runs, _uuid_for_setting


def test_allocate_runs_unchanged_at_production_size() -> None:
    """Production allocation (7 personas × 6 runs × 3 settings) must produce
    exactly 2 runs per (persona, setting) cell. The new allocator collapses
    to the old (persona-local) behavior in this case so the documented 42-run
    plan is preserved bit-for-bit."""
    config = PlaytestConfig(
        personas=[p.id for p in PERSONAS],
        runs_per_persona=6,
        settings=["arctic_station_v1", "orbital_colony_v1", "desert_outpost_v1"],
    )

    allocations = _allocate_runs(config)
    assert len(allocations) == 42

    cells: dict[tuple[str, str], int] = {}
    for persona_id, setting_id, _seed in allocations:
        cells[(persona_id, setting_id)] = cells.get((persona_id, setting_id), 0) + 1

    for persona in config.personas:
        for setting in config.settings:
            assert cells[(persona, setting)] == 2, (
                f"persona {persona} × setting {setting} has {cells[(persona, setting)]} runs, expected 2"
            )


def test_allocate_runs_balanced_at_small_n() -> None:
    """With runs_per_persona=1 the old allocator gave every persona settings[0].
    The new allocator rotates settings across personas so a 5-game smoke can
    actually exercise multiple settings."""
    config = PlaytestConfig(
        personas=["optimizer", "pessimist", "narrative_driven", "brinksmanship", "drop_off_risk"],
        runs_per_persona=1,
        settings=["arctic_station_v1", "orbital_colony_v1", "desert_outpost_v1"],
    )

    allocations = _allocate_runs(config)
    assert len(allocations) == 5

    settings_used = {setting_id for _persona_id, setting_id, _seed in allocations}
    assert settings_used == set(config.settings), (
        f"5-game smoke should hit all 3 settings, got {settings_used}"
    )


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
