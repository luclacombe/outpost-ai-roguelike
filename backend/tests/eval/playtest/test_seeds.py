"""Stable, paired seed allocation shared by play_test and the balance sweep."""

from __future__ import annotations

from app.eval.playtest.seeds import paired_cells, stable_seed

SETTINGS = ["arctic_station_v1", "orbital_colony_v1", "desert_outpost_v1"]


def test_stable_seed_snapshot() -> None:
    """Pinned values: a change here silently breaks cross-run comparisons."""
    assert stable_seed("arctic_station_v1", 0) == 5729134
    assert stable_seed("arctic_station_v1", 1) == 840323896


def test_stable_seed_fits_signed_int32() -> None:
    for s in SETTINGS:
        for i in range(50):
            assert 0 <= stable_seed(s, i) <= 0x7FFFFFFF


def test_stable_seed_differs_by_setting_and_index() -> None:
    seeds = {stable_seed(s, i) for s in SETTINGS for i in range(10)}
    assert len(seeds) == 30


def test_paired_cells_interleave_settings() -> None:
    cells = paired_cells(SETTINGS, 6)
    assert [s for s, _ in cells] == SETTINGS * 2
    assert cells[0] == ("arctic_station_v1", stable_seed("arctic_station_v1", 0))
    assert cells[3] == ("arctic_station_v1", stable_seed("arctic_station_v1", 1))


def test_paired_cells_prefix_stable() -> None:
    """A smaller run is a prefix of a larger one, so smoke cells reappear in prod."""
    assert paired_cells(SETTINGS, 2) == paired_cells(SETTINGS, 9)[:2]
