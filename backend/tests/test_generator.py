"""Unit tests for app.game.generator — procedural run generation.

All tests are synchronous; no asyncio, no DB, no LLM. Setting YAMLs are read
from disk but are static content, so the tests are fast and deterministic.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from app.game.generator import MAX_TURNS, MIN_TURNS, generate_run
from app.game.schemas import Quadrant

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

USER_ID = UUID("bbbbbbbb-0000-0000-0000-000000000002")
USER_ID_ALT = UUID("cccccccc-0000-0000-0000-000000000003")
SEED = 42

# Every launch setting in app/game/settings/.
LAUNCH_SETTINGS = ["arctic_station_v1", "desert_outpost_v1", "orbital_colony_v1"]


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_generate_run_is_deterministic() -> None:
    """Same (user_id, setting_id, seed) twice → identical state dumps."""
    s1 = generate_run(user_id=USER_ID, setting_id="arctic_station_v1", seed=SEED)
    s2 = generate_run(user_id=USER_ID, setting_id="arctic_station_v1", seed=SEED)
    assert s1.model_dump() == s2.model_dump()


def test_generate_run_is_deterministic_three_times() -> None:
    """Belt-and-suspenders: three consecutive calls all match."""
    dumps = [
        generate_run(user_id=USER_ID, setting_id="arctic_station_v1", seed=SEED).model_dump()
        for _ in range(3)
    ]
    assert dumps[0] == dumps[1] == dumps[2]


def test_generate_run_different_seeds_differ() -> None:
    """Different seeds → different agent rolls (names or axes)."""
    s1 = generate_run(user_id=USER_ID, setting_id="arctic_station_v1", seed=1)
    s2 = generate_run(user_id=USER_ID, setting_id="arctic_station_v1", seed=2)

    names_differ = [a.name for a in s1.agents] != [a.name for a in s2.agents]
    axes_differ = any(
        (a1.ethics, a1.temperament, a1.loyalty)
        != (a2.ethics, a2.temperament, a2.loyalty)
        for a1, a2 in zip(s1.agents, s2.agents, strict=True)
    )
    assert names_differ or axes_differ


def test_generate_run_different_users_differ() -> None:
    """Different user_id → different rolls."""
    s1 = generate_run(user_id=USER_ID, setting_id="arctic_station_v1", seed=SEED)
    s2 = generate_run(user_id=USER_ID_ALT, setting_id="arctic_station_v1", seed=SEED)
    # Game ids should differ because they're derived from user_id.
    assert s1.game_id != s2.game_id


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("setting_id", LAUNCH_SETTINGS)
def test_generate_run_covers_all_quadrants(setting_id: str) -> None:
    state = generate_run(user_id=USER_ID, setting_id=setting_id, seed=SEED)
    assert len(state.agents) == 4
    quadrants = {a.archetype_quadrant for a in state.agents}
    assert quadrants == set(Quadrant)


@pytest.mark.parametrize("setting_id", LAUNCH_SETTINGS)
def test_generate_run_resources_match_setting(setting_id: str) -> None:
    """Every resource from the setting YAML appears in the generated state
    with matching name, icon, starting value, max, and critical_threshold."""
    from app.game.setting_loader import load_setting

    setting = load_setting(setting_id)
    state = generate_run(user_id=USER_ID, setting_id=setting_id, seed=SEED)

    assert set(state.resources.keys()) == {r.id for r in setting.resources}
    for rdef in setting.resources:
        got = state.resources[rdef.id]
        assert got.name == rdef.name
        assert got.icon == rdef.icon
        assert got.value == rdef.starting
        assert got.max == rdef.max
        assert got.critical_threshold == rdef.critical_threshold


# ---------------------------------------------------------------------------
# Ranges & initial state
# ---------------------------------------------------------------------------


def test_generate_run_max_turns_in_range() -> None:
    """Over 100 seeds, max_turns always in [MIN_TURNS, MAX_TURNS]."""
    for s in range(100):
        state = generate_run(user_id=USER_ID, setting_id="arctic_station_v1", seed=s)
        assert MIN_TURNS <= state.max_turns <= MAX_TURNS


def test_generate_run_initial_legitimacy_100_health_100() -> None:
    state = generate_run(user_id=USER_ID, setting_id="arctic_station_v1", seed=SEED)
    assert state.legitimacy == 100
    assert state.player_health == 100
    assert state.turn_number == 0
    assert state.end_state is None
    assert state.consequence_seeds == []
    assert state.event_log == []
    assert state.current_event is None
    assert state.turn_proposals == []
    assert state.turn_tool_calls == []


def test_generate_run_agents_are_alive_and_healthy() -> None:
    state = generate_run(user_id=USER_ID, setting_id="arctic_station_v1", seed=SEED)
    for agent in state.agents:
        assert agent.is_alive is True
        assert agent.health == 100
        assert agent.morale == 80
        assert agent.grievance == 0
        assert agent.memory == []
        assert agent.relationships == {}
        assert agent.transformation is None


# ---------------------------------------------------------------------------
# Agent attributes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("setting_id", LAUNCH_SETTINGS)
def test_generate_run_agents_have_verbal_tics(setting_id: str) -> None:
    state = generate_run(user_id=USER_ID, setting_id=setting_id, seed=SEED)
    for agent in state.agents:
        assert agent.verbal_tic  # non-empty


@pytest.mark.parametrize("setting_id", LAUNCH_SETTINGS)
def test_generate_run_agents_have_portrait_seeds(setting_id: str) -> None:
    state = generate_run(user_id=USER_ID, setting_id=setting_id, seed=SEED)
    seeds = [a.portrait_seed for a in state.agents]
    assert all(seeds)                          # all non-empty
    assert len(set(seeds)) == len(seeds)       # all unique


@pytest.mark.parametrize("setting_id", LAUNCH_SETTINGS)
def test_generate_run_agent_uuids_unique(setting_id: str) -> None:
    state = generate_run(user_id=USER_ID, setting_id=setting_id, seed=SEED)
    ids = [a.id for a in state.agents]
    assert len(set(ids)) == len(ids)


def test_generate_run_agent_game_id_matches_state() -> None:
    state = generate_run(user_id=USER_ID, setting_id="arctic_station_v1", seed=SEED)
    for agent in state.agents:
        assert agent.game_id == state.game_id


# ---------------------------------------------------------------------------
# Quadrant bias distribution
# ---------------------------------------------------------------------------
#
# Rationale: the _biased_roll formula sign*(0.5 + rng.random()*0.5) always
# returns a value on the biased side — 100% of rolls. So the 90% threshold is
# a conservative guard against regressions (e.g. if someone changes the bias
# formula to a weaker Gaussian). We run 100 seeds per quadrant; that's enough
# to surface any bias inversion without burning test time.


def _runs_by_quadrant(n: int) -> dict[Quadrant, list]:
    """Generate n runs and group each run's agents by quadrant."""
    out: dict[Quadrant, list] = {q: [] for q in Quadrant}
    for s in range(n):
        state = generate_run(user_id=USER_ID, setting_id="arctic_station_v1", seed=s)
        for agent in state.agents:
            out[agent.archetype_quadrant].append(agent)
    return out


def test_generate_run_axes_biased_per_quadrant() -> None:
    """Aggregate over 100 seeds per quadrant; check the bias signs."""
    runs = _runs_by_quadrant(100)

    # ACCOUNTANT: pragmatist (ethics < 0) AND cautious (temperament < 0).
    acct = runs[Quadrant.ACCOUNTANT]
    assert sum(a.ethics < 0 for a in acct) / len(acct) >= 0.9
    assert sum(a.temperament < 0 for a in acct) / len(acct) >= 0.9

    # OPERATOR: pragmatist (ethics < 0) AND bold (temperament > 0).
    op = runs[Quadrant.OPERATOR]
    assert sum(a.ethics < 0 for a in op) / len(op) >= 0.9
    assert sum(a.temperament > 0 for a in op) / len(op) >= 0.9

    # CONSCIENCE: idealist (ethics > 0) AND cautious (temperament < 0).
    con = runs[Quadrant.CONSCIENCE]
    assert sum(a.ethics > 0 for a in con) / len(con) >= 0.9
    assert sum(a.temperament < 0 for a in con) / len(con) >= 0.9

    # CRUSADER: idealist (ethics > 0) AND bold (temperament > 0).
    cru = runs[Quadrant.CRUSADER]
    assert sum(a.ethics > 0 for a in cru) / len(cru) >= 0.9
    assert sum(a.temperament > 0 for a in cru) / len(cru) >= 0.9


def test_generate_run_loyalty_independently_rolled() -> None:
    """Loyalty is NOT archetype-biased — over 100 runs per quadrant, both
    signs should show up. We use a loose bound (at least 20% each side)
    because n=100 of uniform(-1, 1) gives ~50/50 ± noise; 20% guards against
    all-positive or all-negative regressions."""
    runs = _runs_by_quadrant(100)
    for quadrant, agents in runs.items():
        pos = sum(a.loyalty > 0 for a in agents)
        neg = sum(a.loyalty < 0 for a in agents)
        total = len(agents)
        assert pos / total >= 0.2, f"{quadrant}: loyalty pos ratio {pos/total}"
        assert neg / total >= 0.2, f"{quadrant}: loyalty neg ratio {neg/total}"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_generate_run_unknown_setting_raises() -> None:
    with pytest.raises(FileNotFoundError):
        generate_run(user_id=USER_ID, setting_id="nonexistent_setting", seed=SEED)
