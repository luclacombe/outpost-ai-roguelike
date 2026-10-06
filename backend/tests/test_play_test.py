"""Unit tests for the play_test orchestrator's defensive helpers.

The full orchestrator smoke lives in `tests/eval/playtest/test_orchestrator_smoke.py`
(Postgres + LangGraph + subprocess mocks). This file isolates the pure-function
helpers — primarily `resolve_unfinished_end_state`, the harness-side guard that
fixes sub-bug (ii): runs that exit the harness loop with `end_state=None` because
`/decide` never fired for the terminal turn.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.game.schemas import (
    Agent,
    ColonyState,
    EndState,
    Quadrant,
    Resource,
)
from scripts.play_test import resolve_unfinished_end_state

GAME_ID = UUID("aaaaaaaa-0000-0000-0000-000000000001")
USER_ID = UUID("bbbbbbbb-0000-0000-0000-000000000002")


def _agent(*, alive: bool = True, health: int = 100) -> Agent:
    return Agent(
        id=uuid4(),
        game_id=GAME_ID,
        name="A",
        role="Advisor",
        archetype_quadrant=Quadrant.ACCOUNTANT,
        ethics=0.0,
        temperament=0.0,
        loyalty=0.0,
        portrait_seed="seed",
        health=health,
        morale=50,
        grievance=0,
        is_alive=alive,
    )


def _resource(*, value: int = 50, max_: int = 100, crit: int = 20) -> Resource:
    return Resource(
        id="fuel", name="Fuel", icon="X", value=value, max=max_, critical_threshold=crit
    )


def _colony(
    *,
    end_state: EndState | None = None,
    turn_number: int = 8,
    max_turns: int = 8,
    legitimacy: int = 60,
    player_health: int = 100,
    n_alive_agents: int = 4,
    resource_value: int = 50,
    resource_crit: int = 20,
) -> ColonyState:
    agents = [_agent(alive=i < n_alive_agents) for i in range(4)]
    resources = {
        "fuel": _resource(value=resource_value, crit=resource_crit),
    }
    return ColonyState(
        game_id=GAME_ID,
        user_id=USER_ID,
        setting_id="arctic_station_v1",
        seed=42,
        turn_number=turn_number,
        max_turns=max_turns,
        resources=resources,
        agents=agents,
        legitimacy=legitimacy,
        player_health=player_health,
        consequence_seeds=[],
        event_log=[],
        current_event=None,
        turn_proposals=[],
        turn_tool_calls=[],
        end_state=end_state,
    )


# ---------------------------------------------------------------------------
# Sub-bug (ii) — defensive end_state resolution at harness loop exit
# ---------------------------------------------------------------------------


def test_resolve_unfinished_end_state_returns_existing_state_when_set() -> None:
    """Idempotent: a run that already terminated (end_state set by the engine)
    must keep its terminal state — the resolver only fires when the engine
    left things ambiguous."""
    state = _colony(end_state=EndState.OUSTED)
    assert resolve_unfinished_end_state(state) is EndState.OUSTED


def test_resolve_unfinished_end_state_endured_when_colony_viable() -> None:
    """SPEC §10: max-turns reached + ≥2 alive advisors + healthy resources +
    legitimacy ≥ 20 → ENDURED."""
    state = _colony(
        end_state=None,
        turn_number=11,
        max_turns=11,
        legitimacy=60,
        n_alive_agents=4,
        resource_value=70,
        resource_crit=20,
    )
    assert resolve_unfinished_end_state(state) is EndState.ENDURED


def test_resolve_unfinished_end_state_collapsed_when_legitimacy_below_floor() -> None:
    """Below the 20 legitimacy floor → COLLAPSED, not ENDURED."""
    state = _colony(
        end_state=None,
        turn_number=10,
        max_turns=10,
        legitimacy=15,
        n_alive_agents=4,
        resource_value=70,
        resource_crit=20,
    )
    assert resolve_unfinished_end_state(state) is EndState.COLLAPSED


def test_resolve_unfinished_end_state_collapsed_when_resource_below_critical() -> None:
    """A resource at-or-below `critical_threshold` → COLLAPSED."""
    state = _colony(
        end_state=None,
        turn_number=10,
        max_turns=10,
        legitimacy=60,
        n_alive_agents=4,
        resource_value=10,
        resource_crit=20,
    )
    assert resolve_unfinished_end_state(state) is EndState.COLLAPSED


def test_resolve_unfinished_end_state_collapsed_when_only_one_alive_agent() -> None:
    """SPEC §10 requires ≥2 alive advisors for ENDURED."""
    state = _colony(
        end_state=None,
        turn_number=10,
        max_turns=10,
        legitimacy=60,
        n_alive_agents=1,
        resource_value=70,
        resource_crit=20,
    )
    assert resolve_unfinished_end_state(state) is EndState.COLLAPSED


def test_resolve_unfinished_end_state_killed_when_player_dead() -> None:
    """player_health ≤ 0 → KILLED."""
    state = _colony(
        end_state=None,
        turn_number=10,
        max_turns=10,
        legitimacy=60,
        player_health=0,
        n_alive_agents=4,
        resource_value=70,
    )
    assert resolve_unfinished_end_state(state) is EndState.KILLED


def test_resolve_unfinished_end_state_collapsed_when_all_agents_dead() -> None:
    """No alive agents → COLLAPSED."""
    state = _colony(
        end_state=None,
        turn_number=10,
        max_turns=10,
        legitimacy=60,
        n_alive_agents=0,
        resource_value=70,
    )
    assert resolve_unfinished_end_state(state) is EndState.COLLAPSED


def test_resolve_unfinished_end_state_collapsed_for_short_run_with_low_resources() -> None:
    """Edge case: harness loop exited at turn 7 (below the engine's 8-12
    randomized max_turns), the run never reached the natural max-turn
    branch. The resolver still derives a sensible terminal state by
    inspecting resource floors and agent health — short runs with
    failing fundamentals → COLLAPSED."""
    state = _colony(
        end_state=None,
        turn_number=7,
        max_turns=10,
        legitimacy=60,
        n_alive_agents=4,
        resource_value=10,  # below crit
        resource_crit=20,
    )
    assert resolve_unfinished_end_state(state) is EndState.COLLAPSED


def test_resolve_unfinished_end_state_endured_for_short_run_with_healthy_state() -> None:
    """Edge case: harness exited mid-run but everything is healthy — default
    to ENDURED per SPEC §10's "every run resolves to one of four end
    states". The colony is viable; the harness loop quitting early is a
    harness bug, not a colony failure, so award the colony its survival."""
    state = _colony(
        end_state=None,
        turn_number=7,
        max_turns=10,
        legitimacy=60,
        n_alive_agents=4,
        resource_value=70,
        resource_crit=20,
    )
    assert resolve_unfinished_end_state(state) is EndState.ENDURED


@pytest.mark.parametrize(
    "tn,mt",
    [(8, 8), (9, 9), (11, 11), (12, 12)],
)
def test_resolve_unfinished_end_state_handles_all_max_turns(tn: int, mt: int) -> None:
    """The 4 failing runs from the 2026-04-30 playtest landed at turns 7,
    8, 10, 11. Verify every turn count in the engine's [8, 12] range
    resolves cleanly when the colony is viable."""
    state = _colony(
        end_state=None,
        turn_number=tn,
        max_turns=mt,
        legitimacy=60,
        n_alive_agents=4,
        resource_value=70,
        resource_crit=20,
    )
    assert resolve_unfinished_end_state(state) is EndState.ENDURED


def test_playtest_defaults_pin_persona_model_and_disable_tracing() -> None:
    from scripts.play_test import PlaytestConfig, build_parser

    config = PlaytestConfig()
    assert config.persona_model == "sonnet"
    assert config.trace is False
    args = build_parser().parse_args([])
    assert args.persona_model == "sonnet"
    assert args.trace is False
    assert args.allow_remote_db is False
    args = build_parser().parse_args(
        ["--personas", "optimizer,pessimist", "--max-turns", "3", "--trace"]
    )
    assert args.personas == "optimizer,pessimist"
    assert args.max_turns == 3
    assert args.trace is True


# ---------------------------------------------------------------------------
# Aborted runs (zero-proposal turns): reported as ABORTED, never resolved
# ---------------------------------------------------------------------------


def _aborted_run_record(game_id: UUID) -> Any:
    from app.eval.types import RunRecord

    return RunRecord(
        setting_id="arctic_station_v1",
        policy="optimizer",
        seed=7,
        end_state=None,
        turns_played=2,
        final_player_health=100,
        agents_alive_at_end=4,
        total_agents=4,
        cost_cents=4,
        game_id=game_id,
        abort_reason="no_proposals at turn 2",
    )


async def test_drive_one_game_returns_aborted_archive_without_resolving(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scripts import play_test
    from scripts.play_test import PlaytestConfig
    from tests.fixtures import run_simulator

    async def fake_simulate(**kwargs: Any) -> Any:
        return _aborted_run_record(GAME_ID)

    async def noop(*args: Any, **kwargs: Any) -> None:
        return None

    class _FakePlayer:
        def __init__(self, **kwargs: Any) -> None:
            self.per_turn_outputs: list[Any] = []
            self.per_turn_inputs: list[Any] = []

    def no_db() -> Any:
        raise AssertionError("aborted runs must not touch the DB / post-mortem path")

    monkeypatch.setattr(run_simulator, "simulate_game_with_player", fake_simulate)
    monkeypatch.setattr(play_test, "_provision_synthetic_user", noop)
    monkeypatch.setattr(play_test, "LLMPlayer", _FakePlayer)
    monkeypatch.setattr(play_test, "async_session_factory", no_db)

    arc = await play_test._drive_one_game(
        persona_id="optimizer",
        setting_id="arctic_station_v1",
        seed=7,
        pool=None,  # type: ignore[arg-type]
        config=PlaytestConfig(shell_dir=tmp_path),
    )
    assert arc.end_state == "ABORTED"
    assert arc.abort_reason == "no_proposals at turn 2"
    assert arc.run_id == str(GAME_ID)
    assert arc.verdict is None
    assert arc.turns_played == 2


async def test_run_harness_lists_aborted_runs_outside_corpus(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from contextlib import asynccontextmanager

    from app.eval.playtest.output import PerRunArchive
    from scripts import play_test
    from scripts.play_test import PlaytestConfig

    @asynccontextmanager
    async def fake_app_state():
        yield None

    async def fake_drive(*, persona_id: str, setting_id: str, seed: int, pool, config):
        aborted = setting_id == "orbital_colony_v1"
        return PerRunArchive(
            run_id=str(uuid4()),
            persona_id=persona_id,
            setting_id=setting_id,
            seed=seed,
            end_state="ABORTED" if aborted else "ENDURED",
            abort_reason="no_proposals at turn 1" if aborted else None,
            turns_played=1 if aborted else 10,
            per_turn_outputs=[],
            verdict=None,
            run_record_summary={"tool_calls": [], "narration_per_turn": []},
            per_run_oracles={},
        )

    monkeypatch.setattr(play_test, "harness_app_state", fake_app_state)
    monkeypatch.setattr(play_test, "_drive_one_game", fake_drive)

    config = PlaytestConfig(
        personas=["optimizer"],
        runs_per_persona=2,
        settings=["arctic_station_v1", "orbital_colony_v1"],
        out_dir=tmp_path,
        shell_dir=tmp_path / "shell",
    )
    await play_test.run_harness(config=config)

    runs = json.loads((tmp_path / "runs.json").read_text())
    assert [r["end_state"] for r in runs["per_run_summaries"]] == ["ENDURED"]
    [aborted] = runs["harness_findings"]["aborted_runs"]
    assert aborted["setting"] == "orbital_colony_v1"
    assert aborted["reason"] == "no_proposals at turn 1"
    assert aborted["end_state"] == "ABORTED"
    # The aborted run's raw archive is still on disk for forensics.
    raw = json.loads((tmp_path / aborted["raw_json_path"]).read_text())
    assert raw["end_state"] == "ABORTED"
    assert raw["abort_reason"] == "no_proposals at turn 1"
