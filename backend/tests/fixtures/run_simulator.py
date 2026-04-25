"""Drive a full Outpost run through the live HTTP surface and emit a RunRecord.

The eval pipeline's whole value is exercising the same path the player uses,
so this driver speaks ONLY through `/game/session`, `/game/{id}/turn`, and
`/game/{id}/decide`. State snapshots come from a final DB read so the
RunRecord reflects the engine's authoritative view.

Two modes:

  * Mocked-LLM (default): the caller wraps `simulate_game` in a `respx.mock`
    block and configures Anthropic routes. `simulate_game` itself is
    transport-agnostic.
  * Real-LLM: pass `real_llm=True` (or just don't intercept the network).
    Used by the nightly job; requires `ANTHROPIC_API_KEY`.

The driver requires Postgres because:
  * `POST /game/session` writes `game_sessions` + `agents` rows.
  * `nodes.advance_turn` is the sole DB-write site and runs every turn.
  * `graph_postgres` is the only checkpointer that survives the /turn → /decide
    pause/resume hop.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.agent.graph import thread_config
from app.database import async_session_factory
from app.dependencies import get_current_user
from app.eval.types import RunRecord
from app.game.models import GameSession
from app.game.schemas import ColonyState, EndState, ToolCall
from app.main import app
from tests.fixtures.simulated_player import SimulatedPlayer
from tests.test_agent_router import _collect_stream

Policy = Literal["moral", "pragmatic", "random"]


async def _load_final_state(game_id: UUID) -> ColonyState:
    """Read the authoritative ColonyState from the game_sessions row.

    `nodes.advance_turn` writes the full state JSONB on every turn; the row
    is the source of truth for end_state, resources, agents, and cumulative
    cost.
    """
    async with async_session_factory() as s:
        row = (await s.execute(select(GameSession).where(GameSession.id == game_id))).scalar_one()
    return ColonyState.model_validate(row.state)


def _proposals_from_frame(frame: dict[str, Any]) -> list[dict[str, Any]]:
    """Pluck the public proposals payload from a `proposals_ready` SSE frame."""
    return list(frame["data"]["proposals"])


async def _resolve_chosen_tool_calls(
    *,
    graph: Any,
    game_id: UUID,
    chosen_proposal_id: str,
) -> list[ToolCall]:
    """Read the paused snapshot to recover the chosen proposal's tool_calls_on_pick.

    `ProposalPublic` (the SSE payload shape) drops `tool_calls_on_pick`, so
    the only way to capture them for the RunRecord is via
    `graph.aget_state(...)` while the graph is paused at `resolve`.
    """
    cfg = thread_config(str(game_id))
    snapshot = await graph.aget_state(cfg)
    full_proposals = snapshot.values.get("proposals") or []
    for p in full_proposals:
        if p.id == chosen_proposal_id:
            return list(p.tool_calls_on_pick)
    return []


def _auth_override(user_id: UUID) -> dict[str, str]:
    return {
        "sub": str(user_id),
        "email": "eval@outpost.local",
        "aud": "authenticated",
    }


class _RunAggregator:
    """Bucket SSE frames into the RunRecord-shaped accumulators.

    Kept private — the public API is `simulate_game`. A class lets us add
    new buckets without growing the function signature.
    """

    def __init__(self) -> None:
        self.tool_calls: list[ToolCall] = []
        self.agent_responses_per_agent: dict[UUID, list[str]] = {}
        self.narration_per_turn: list[str] = []
        self.forced_choice_turns: int = 0

    def absorb_frames(self, frames: list[dict[str, Any]]) -> None:
        for f in frames:
            event = f["event"]
            data = f["data"]
            if not isinstance(data, dict):
                continue
            if event == "agent_spoke":
                aid = UUID(data["agent_id"])
                self.agent_responses_per_agent.setdefault(aid, []).append(data["text"])
            elif event == "autonomous_action":
                # Reconstruct a ToolCall from the SSE payload. `agent_id` and
                # `args` are the only fields exposed; the engine has already
                # validated/applied so `rejection_reason` stays None here.
                # Off-list autonomous calls or rejected ones never reach
                # the SSE stream — they are filtered upstream — so this
                # under-counts validity-rate denominators but keeps the
                # numerator stable. Documented limitation.
                aid_str = data.get("agent_id")
                self.tool_calls.append(
                    ToolCall(
                        name=data["tool_name"],
                        caller_id=UUID(aid_str) if aid_str else None,
                        args=dict(data.get("args") or {}),
                    )
                )
            elif event == "narration":
                text = data.get("text")
                if text:
                    self.narration_per_turn.append(text)


async def simulate_game(
    *,
    setting_id: str,
    policy: Policy,
    seed: int = 0,
    real_llm: bool = False,
    max_turns: int = 12,
    user_id: UUID,
) -> RunRecord:
    """Drive a full game through the in-process HTTP surface.

    Sequence per turn:
      1. POST /turn — collect SSE frames until proposals_ready or run_over.
      2. SimulatedPlayer.pick(proposals).
      3. Read the paused snapshot to capture `tool_calls_on_pick` for the
         chosen proposal (the SSE payload drops them).
      4. POST /decide — collect frames until proposals_ready (next turn) or
         run_over.

    `setting_id` is currently informational — `POST /game/session` picks the
    setting server-side per (user, UTC day). The simulator records what the
    server actually picked into the returned `RunRecord.setting_id` so the
    metrics still see the real id.

    `real_llm` is a no-op flag here; the network is either intercepted by a
    `respx.mock` block in the caller (mocked mode) or not (real mode).

    Returns a `RunRecord` populated from SSE frames + the final DB state.
    """
    player = SimulatedPlayer(policy, seed=seed)
    aggregator = _RunAggregator()
    graph = app.state.graph

    app.dependency_overrides[get_current_user] = lambda: _auth_override(user_id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            create_resp = await client.post("/game/session")
            assert create_resp.status_code == 201, create_resp.text
            game_id = UUID(create_resp.json()["game_id"])

            ended = False
            for _turn_idx in range(max_turns):
                # ---- /turn ----
                async with client.stream("POST", f"/game/{game_id}/turn") as resp:
                    assert resp.status_code == 200, await resp.aread()
                    turn_frames = await _collect_stream(resp)
                aggregator.absorb_frames(turn_frames)

                if any(f["event"] == "run_over" for f in turn_frames):
                    ended = True
                    break

                pr = next((f for f in turn_frames if f["event"] == "proposals_ready"), None)
                if pr is None:
                    # Defensive — a healthy /turn always pauses with proposals.
                    break

                proposals = _proposals_from_frame(pr)
                if not proposals:
                    break
                if len(proposals) == 1:
                    aggregator.forced_choice_turns += 1

                chosen_id = player.pick(proposals)

                # Capture the chosen proposal's tool_calls_on_pick from the
                # paused snapshot before /decide consumes the pause.
                chosen_tool_calls = await _resolve_chosen_tool_calls(
                    graph=graph, game_id=game_id, chosen_proposal_id=chosen_id
                )
                aggregator.tool_calls.extend(chosen_tool_calls)

                # ---- /decide ----
                async with client.stream(
                    "POST",
                    f"/game/{game_id}/decide",
                    json={"chosen_proposal_id": chosen_id},
                ) as resp:
                    assert resp.status_code == 200, await resp.aread()
                    decide_frames = await _collect_stream(resp)
                aggregator.absorb_frames(decide_frames)

                if any(f["event"] == "run_over" for f in decide_frames):
                    ended = True
                    break
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    final_state = await _load_final_state(game_id)

    # Build resource_history from the final state. Per-turn snapshots aren't
    # streamed; we expose the final values as a single-element history per
    # resource. The metrics treat single-element histories as zero volatility,
    # which is the correct conservative default for one-run inputs.
    resource_history: dict[str, list[int]] = {
        rid: [r.value] for rid, r in final_state.resources.items()
    }

    end_state: EndState | None = final_state.end_state
    turns_played = final_state.turn_number
    final_player_health = final_state.player_health
    agents_alive_at_end = sum(1 for a in final_state.agents if a.is_alive)
    total_agents = len(final_state.agents)
    cost_cents = final_state.turn_cost_cents

    # `ended` is informational — `final_state.end_state` is authoritative.
    _ = ended

    return RunRecord(
        setting_id=final_state.setting_id,
        policy=policy,
        seed=seed,
        end_state=end_state,
        turns_played=turns_played,
        final_player_health=final_player_health,
        agents_alive_at_end=agents_alive_at_end,
        total_agents=total_agents,
        cost_cents=cost_cents,
        tool_calls=aggregator.tool_calls,
        agent_responses_per_agent=aggregator.agent_responses_per_agent,
        resource_history=resource_history,
        forced_choice_turns=aggregator.forced_choice_turns,
        narration_per_turn=aggregator.narration_per_turn,
    )


__all__ = ["simulate_game"]
