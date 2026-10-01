"""DB-free unit tests for run_simulator helpers.

The full driver needs Postgres (see test_run_simulator.py, marked `eval`).
These cover the pure pieces: seed injection and the choice-spread hook.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.game.generator import generate_run
from app.game.schemas import ColonyState, Proposal, ProposalFlavor, ToolCall
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
