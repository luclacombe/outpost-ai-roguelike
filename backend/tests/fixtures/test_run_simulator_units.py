"""DB-free unit tests for run_simulator helpers.

The full driver needs Postgres (see test_run_simulator.py, marked `eval`).
These cover the pure pieces: seed injection and the choice-spread hook.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import pytest

from app.eval.types import RunRecord
from app.game.generator import generate_run
from app.game.schemas import ColonyState, Proposal, ProposalFlavor, ToolCall
from app.main import app
from tests.fixtures import run_simulator
from tests.fixtures.run_simulator import (
    _choice_spread_at_pause,
    _eval_seed_ctx,
    _seed_override_from_ctx,
)


def test_seed_override_defaults_to_none() -> None:
    assert _seed_override_from_ctx() is None


def test_seed_override_reads_task_local_seed() -> None:
    token = _eval_seed_ctx.set(4242)
    try:
        assert _seed_override_from_ctx() == 4242
    finally:
        _eval_seed_ctx.reset(token)
    assert _seed_override_from_ctx() is None


class _FakeSnapshot:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values


class _FakeGraph:
    """Stands in for the compiled graph: read-only `aget_state` only."""

    def __init__(self, values: dict[str, Any]) -> None:
        self._values = values
        self.configs: list[dict[str, Any]] = []

    async def aget_state(self, config: dict[str, Any]) -> _FakeSnapshot:
        self.configs.append(config)
        return _FakeSnapshot(self._values)


def _paused_values() -> tuple[ColonyState, list[Proposal]]:
    colony = generate_run(
        user_id=UUID("bbbbbbbb-0000-0000-0000-000000000002"),
        setting_id="arctic_station_v1",
        seed=5,
    )
    props = [
        Proposal(
            id=f"p{i}",
            proposed_by_agent_id=None,
            flavor=ProposalFlavor.DOMINANT,
            name=f"p{i}",
            description="d",
            effects_preview=[],
            tool_calls_on_pick=[
                ToolCall(
                    name="modify_resource",
                    caller_id=None,
                    args={"resource_id": "fuel", "delta": -i, "reason": "r"},
                )
            ],
        )
        for i in range(3)
    ]
    return colony, props


async def test_choice_spread_at_pause_reads_checkpoint_with_thread_id() -> None:
    colony, props = _paused_values()
    graph = _FakeGraph({"colony_state": colony, "proposals": props})
    out = await _choice_spread_at_pause(graph, colony.game_id)
    assert graph.configs[0]["configurable"]["thread_id"] == str(colony.game_id)
    assert [o["proposal_id"] for o in out["options"]] == ["p0", "p1", "p2"]
    assert out["spread_l1_max"] > 0


async def test_choice_spread_at_pause_accepts_serialized_values() -> None:
    colony, props = _paused_values()
    graph = _FakeGraph(
        {
            "colony_state": colony.model_dump(mode="json"),
            "proposals": [p.model_dump(mode="json") for p in props],
        }
    )
    out = await _choice_spread_at_pause(graph, colony.game_id)
    assert len(out["options"]) == 3


async def test_choice_spread_at_pause_never_raises() -> None:
    out = await _choice_spread_at_pause(_FakeGraph({}), UUID(int=0))
    assert "error" in out


# ---------------------------------------------------------------------------
# _drive_game abort paths (DB-free: HTTP client, SSE and DB reads stubbed)
# ---------------------------------------------------------------------------

_GAME_ID = UUID("cccccccc-0000-0000-0000-000000000003")
_PROPOSALS = [{"id": f"p{i}", "name": f"Plan {i}", "flavor": "DOMINANT"} for i in range(3)]


class _Resp:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = ""

    def json(self) -> dict[str, Any]:
        return self._payload

    async def aread(self) -> bytes:
        return b""


class _FakeClient:
    """Minimal AsyncClient: POST /game/session + streaming /turn and /decide."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.stream_urls: list[str] = []

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def post(self, url: str, **kwargs: Any) -> _Resp:
        return _Resp(201, {"game_id": str(_GAME_ID)})

    @asynccontextmanager
    async def stream(self, method: str, url: str, **kwargs: Any):
        self.stream_urls.append(url)
        yield _Resp(200)


class _FirstPlayer:
    def pick(self, proposals: list[dict[str, Any]]) -> str:
        return proposals[0]["id"]


def _stub_driver(monkeypatch: pytest.MonkeyPatch, frames: list[list[dict[str, Any]]]) -> None:
    """Feed `frames` to successive `_collect_stream` calls; stub the DB tail."""
    queue = list(frames)

    async def fake_collect(resp: Any) -> list[dict[str, Any]]:
        return queue.pop(0) if queue else []

    async def fake_build(game_id: UUID, aggregator: Any, *, policy: str, seed: int) -> RunRecord:
        return RunRecord(
            setting_id="arctic_station_v1",
            policy=policy,
            seed=seed,
            end_state=None,
            turns_played=1,
            final_player_health=100,
            agents_alive_at_end=4,
            total_agents=4,
            cost_cents=0,
            game_id=game_id,
        )

    async def fake_spread(graph: Any, game_id: UUID) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(run_simulator, "AsyncClient", _FakeClient)
    monkeypatch.setattr(run_simulator, "_collect_stream", fake_collect)
    monkeypatch.setattr(run_simulator, "_build_run_record", fake_build)
    monkeypatch.setattr(run_simulator, "_choice_spread_at_pause", fake_spread)
    monkeypatch.setattr(app.state, "graph", None, raising=False)


async def _drive(max_turns: int = 5) -> RunRecord:
    return await run_simulator._drive_game(
        player=_FirstPlayer(),
        policy="moral",
        seed=1,
        max_turns=max_turns,
        user_id=UUID(int=7),
        on_turn_input=None,
    )


def _proposals_frame(proposals: list[dict[str, Any]]) -> dict[str, Any]:
    return {"event": "proposals_ready", "data": {"proposals": proposals}}


async def test_drive_game_aborts_on_empty_proposals(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_driver(monkeypatch, [[_proposals_frame([])]])
    record = await _drive()
    assert record.abort_reason == "no_proposals at turn 1"
    assert record.end_state is None


async def test_drive_game_aborts_on_missing_proposals_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_driver(monkeypatch, [[{"event": "done", "data": "[DONE]"}]])
    record = await _drive()
    assert record.abort_reason == "no_proposals_frame at turn 1"


async def test_drive_game_abort_reports_the_failing_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    # turn 1 /turn, turn 1 /decide (no run_over), turn 2 /turn with no proposals.
    _stub_driver(monkeypatch, [[_proposals_frame(_PROPOSALS)], [], [_proposals_frame([])]])
    record = await _drive()
    assert record.abort_reason == "no_proposals at turn 2"
    assert len(record.choice_spread) == 1


async def test_drive_game_run_over_is_not_an_abort(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_driver(monkeypatch, [[_proposals_frame(_PROPOSALS)], [{"event": "run_over", "data": {}}]])
    record = await _drive()
    assert record.abort_reason is None


async def test_drive_game_turn_budget_exhaustion_is_not_an_abort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_driver(monkeypatch, [[_proposals_frame(_PROPOSALS)], []])
    record = await _drive(max_turns=1)
    assert record.abort_reason is None


def test_tool_calls_from_turn_log_prefers_new_column() -> None:
    from types import SimpleNamespace

    from tests.fixtures.run_simulator import _tool_calls_from_turn_log

    record = [
        {
            "name": "modify_legitimacy",
            "caller_id": None,
            "args": {"delta": -1, "reason": "end of turn"},
            "phase": "close",
            "applied_delta": -1,
        },
        {
            "name": "modify_resource",
            "caller_id": None,
            "args": {"resource_id": "water"},
            "phase": "prepare",
            "rejection_reason": "stripped:unknown_resource",
        },
    ]
    row = SimpleNamespace(tool_calls=record, chosen={}, proposals=[], autonomous_actions=[])
    calls = _tool_calls_from_turn_log(row)
    assert [tc.phase for tc in calls] == ["close", "prepare"]
    assert calls[1].rejection_reason == "stripped:unknown_resource"


def test_tool_calls_from_turn_log_falls_back_when_column_null() -> None:
    from types import SimpleNamespace

    from tests.fixtures.run_simulator import _tool_calls_from_turn_log

    pick_call = {
        "name": "modify_resource",
        "caller_id": None,
        "args": {"resource_id": "food", "delta": 2},
        "phase": "resolve",
    }
    row = SimpleNamespace(
        tool_calls=None,
        chosen={"chosen_proposal_id": "p1"},
        proposals=[
            {"id": "p0", "tool_calls_on_pick": []},
            {"id": "p1", "tool_calls_on_pick": [pick_call]},
        ],
        autonomous_actions=[],
    )
    calls = _tool_calls_from_turn_log(row)
    assert len(calls) == 1
    assert calls[0].name == "modify_resource"
    assert calls[0].args == {"resource_id": "food", "delta": 2}
