"""Balance sweep orchestration — DB-free (game driver and app state stubbed)."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.agent import observability
from app.config import settings
from app.eval.playtest.seeds import paired_cells
from app.eval.types import RunRecord
from app.game.generator import generate_run
from app.game.schemas import EndState, ToolCall
from scripts import sweep
from scripts.sweep import SweepConfig, allocate_sweep, build_parser, game_record, run_sweep


def test_default_allocation_is_90_paired_games() -> None:
    config = SweepConfig()
    allocations = allocate_sweep(config)
    assert len(allocations) == 90
    cells_by_policy: dict[str, list[tuple[str, int]]] = {}
    for policy, setting, seed in allocations:
        cells_by_policy.setdefault(policy, []).append((setting, seed))
    assert set(cells_by_policy) == {"moral", "pragmatic", "random"}
    expected = paired_cells(config.settings, 30)
    for cells in cells_by_policy.values():
        assert cells == expected


def test_parser_defaults() -> None:
    args = build_parser().parse_args([])
    assert args.seeds == 10
    assert args.concurrency == 5
    assert args.max_turns == 12
    assert args.trace is False
    assert args.out is None
    args = build_parser().parse_args(["--seeds", "2", "--policies", "moral", "--trace"])
    assert args.seeds == 2 and args.policies == "moral" and args.trace is True


def test_game_record_from_run_record() -> None:
    colony = generate_run(
        user_id=UUID("bbbbbbbb-0000-0000-0000-000000000002"),
        setting_id="arctic_station_v1",
        seed=9,
    ).model_copy(update={"legitimacy": 41, "turn_number": 9})
    record = RunRecord(
        setting_id="arctic_station_v1",
        policy="moral",
        seed=9,
        end_state=None,
        turns_played=9,
        final_player_health=100,
        agents_alive_at_end=4,
        total_agents=4,
        cost_cents=33,
        tool_calls=[
            ToolCall(name="modify_resource", caller_id=None, args={}),
            ToolCall(name="modify_resource", caller_id=None, args={}, rejection_reason="x"),
        ],
        game_id=colony.game_id,
        final_legitimacy=41,
    )
    row = game_record(
        record,
        colony=colony,
        requested_setting="arctic_station_v1",
        cost_cents=35,
        end_state=EndState.ENDURED,
        resolved_by_harness=True,
    )
    assert row["end_state"] == "ENDURED"
    assert row["end_state_resolved_by_harness"] is True
    assert row["final_legitimacy"] == 41
    assert row["cost_cents"] == 35
    assert row["tool_calls_total"] == 2 and row["tool_calls_rejected"] == 1
    assert row["choice_spread_summary"]["n_turns"] == 0
    assert set(row["final_resources"]) == set(colony.resources)
    json.dumps(row)  # must be JSON-serializable as-is


def _fake_row(policy: str, setting: str, seed: int) -> dict[str, Any]:
    return {
        "policy": policy,
        "setting": setting,
        "seed": seed,
        "game_id": str(uuid4()),
        "end_state": "ENDURED",
        "turns_played": 9,
        "final_legitimacy": 50,
        "final_player_health": 100,
        "final_resource_frac": 0.5,
        "cost_cents": 12,
        "tool_calls_total": 3,
        "tool_calls_rejected": 0,
        "end_state_resolved_by_harness": False,
        "choice_spread_summary": {"n_turns": 0, "oracle_errors": 0},
    }


async def test_run_sweep_writes_outputs_and_records_aborts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", "sk")
    tracing_seen: list[bool] = []

    @asynccontextmanager
    async def fake_app_state():
        yield None

    async def fake_drive(*, policy: str, setting: str, seed: int, config: SweepConfig):
        tracing_seen.append(observability.langfuse_enabled())
        if policy == "random":
            raise RuntimeError("engine exploded")
        return _fake_row(policy, setting, seed)

    monkeypatch.setattr(sweep, "harness_app_state", fake_app_state)
    monkeypatch.setattr(sweep, "_drive_one_sweep_game", fake_drive)

    config = SweepConfig(seeds=1, settings=["arctic_station_v1"], out_dir=tmp_path, concurrency=2)
    summary = await run_sweep(config)

    assert tracing_seen and not any(tracing_seen)
    assert observability.langfuse_enabled() is True  # restored after the run
    assert summary["n_games"] == 2
    assert summary["n_aborted"] == 1
    assert summary["aborted"][0]["error"] == "RuntimeError: engine exploded"
    raw = sorted(p.name for p in (tmp_path / "raw").glob("*.json"))
    seed = paired_cells(["arctic_station_v1"], 1)[0][1]
    assert raw == [
        f"moral-arctic_station_v1-seed{seed}.json",
        f"pragmatic-arctic_station_v1-seed{seed}.json",
    ]
    on_disk = json.loads((tmp_path / "summary.json").read_text())
    assert on_disk["n_games"] == 2
    assert on_disk["meta"]["seeds"] == 1
    assert "## By policy" in (tmp_path / "summary.md").read_text()


def _aborted_record(colony: Any) -> RunRecord:
    return RunRecord(
        setting_id="arctic_station_v1",
        policy="moral",
        seed=9,
        end_state=None,
        turns_played=1,
        final_player_health=100,
        agents_alive_at_end=4,
        total_agents=4,
        cost_cents=3,
        game_id=colony.game_id,
        abort_reason="no_proposals at turn 1",
    )


def test_game_record_marks_aborted_runs() -> None:
    colony = generate_run(
        user_id=UUID("bbbbbbbb-0000-0000-0000-000000000002"),
        setting_id="arctic_station_v1",
        seed=9,
    )
    row = game_record(
        _aborted_record(colony),
        colony=colony,
        requested_setting="arctic_station_v1",
        cost_cents=3,
        end_state=None,
        resolved_by_harness=False,
    )
    assert row["end_state"] == "ABORTED"
    assert row["abort_reason"] == "no_proposals at turn 1"
    assert row["end_state_resolved_by_harness"] is False
    json.dumps(row)


def test_game_record_finished_run_has_no_abort_reason() -> None:
    colony = generate_run(
        user_id=UUID("bbbbbbbb-0000-0000-0000-000000000002"),
        setting_id="arctic_station_v1",
        seed=9,
    )
    record = _aborted_record(colony).model_copy(update={"abort_reason": None})
    row = game_record(
        record,
        colony=colony,
        requested_setting="arctic_station_v1",
        cost_cents=3,
        end_state=EndState.COLLAPSED,
        resolved_by_harness=False,
    )
    assert row["end_state"] == "COLLAPSED"
    assert row["abort_reason"] is None


async def test_drive_one_sweep_game_does_not_resolve_aborted_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.fixtures import run_simulator

    colony = generate_run(
        user_id=UUID("bbbbbbbb-0000-0000-0000-000000000002"),
        setting_id="arctic_station_v1",
        seed=9,
    )

    async def fake_simulate(**kwargs: Any) -> RunRecord:
        return _aborted_record(colony)

    async def noop(*args: Any, **kwargs: Any) -> None:
        return None

    def must_not_resolve(state: Any) -> EndState:
        raise AssertionError("aborted runs must not be resolved to an end state")

    class _Row:
        id = colony.game_id
        state = colony.model_dump(mode="json")
        cost_cents = 3

    class _Result:
        def scalar_one(self) -> _Row:
            return _Row()

    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *exc: Any) -> bool:
            return False

        async def execute(self, stmt: Any) -> _Result:
            return _Result()

    monkeypatch.setattr(run_simulator, "simulate_game", fake_simulate)
    monkeypatch.setattr(sweep, "_provision_synthetic_user", noop)
    monkeypatch.setattr(sweep, "async_session_factory", _Session)
    monkeypatch.setattr(sweep, "resolve_unfinished_end_state", must_not_resolve)

    row = await sweep._drive_one_sweep_game(
        policy="moral", setting="arctic_station_v1", seed=9, config=SweepConfig()
    )
    assert row["end_state"] == "ABORTED"
    assert row["abort_reason"] == "no_proposals at turn 1"


async def test_run_sweep_lists_aborted_games_outside_outcome_stats(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    @asynccontextmanager
    async def fake_app_state():
        yield None

    async def fake_drive(*, policy: str, setting: str, seed: int, config: SweepConfig):
        row = _fake_row(policy, setting, seed)
        if policy == "random":
            row.update(end_state="ABORTED", abort_reason="no_proposals at turn 2")
        return row

    monkeypatch.setattr(sweep, "harness_app_state", fake_app_state)
    monkeypatch.setattr(sweep, "_drive_one_sweep_game", fake_drive)

    config = SweepConfig(seeds=1, settings=["arctic_station_v1"], out_dir=tmp_path, concurrency=2)
    summary = await run_sweep(config)

    assert summary["n_games"] == 2
    assert summary["overall"]["end_states"]["counts"]["ENDURED"] == 2
    assert summary["n_aborted"] == 1
    [aborted] = summary["aborted"]
    assert aborted["policy"] == "random"
    assert aborted["error"] == "no_proposals at turn 2"
    assert aborted["abort_reason"] == "no_proposals at turn 2"
    # The aborted game's raw row is still written for forensics.
    seed = paired_cells(["arctic_station_v1"], 1)[0][1]
    raw = json.loads((tmp_path / "raw" / f"random-arctic_station_v1-seed{seed}.json").read_text())
    assert raw["end_state"] == "ABORTED"
    assert "no_proposals at turn 2" in (tmp_path / "summary.md").read_text()
