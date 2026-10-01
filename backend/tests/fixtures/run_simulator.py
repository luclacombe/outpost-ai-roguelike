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

Tool-call provenance: the canonical record of all per-turn tool calls comes
from the `turn_logs` rows written at `nodes.advance_turn`, NOT from SSE
frames. SSE drops the chosen proposal's `tool_calls_on_pick` and the
applied/rejected calls from `resolve`; reading the DB at end of run is the
only way to reconstruct the full picture for the validity-rate metric.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any, Literal
from uuid import UUID

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database import async_session_factory
from app.dependencies import get_current_user, get_run_seed_override
from app.eval.types import RunRecord
from app.game.models import GameSession, TurnLog
from app.game.schemas import ColonyState, ToolCall
from app.game.setting_loader import load_setting
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


async def _read_turn_logs(game_id: UUID) -> list[TurnLog]:
    """Return every `turn_logs` row for this game, ordered by turn_number ASC.

    Used by the run aggregator to reconstruct tool-call history and
    per-turn resource snapshots that SSE doesn't expose.
    """
    async with async_session_factory() as s:
        rows = (
            (
                await s.execute(
                    select(TurnLog)
                    .where(TurnLog.game_id == game_id)
                    .order_by(TurnLog.turn_number.asc())
                )
            )
            .scalars()
            .all()
        )
    return list(rows)


def _proposals_from_frame(frame: dict[str, Any]) -> list[dict[str, Any]]:
    """Pluck the public proposals payload from a `proposals_ready` SSE frame."""
    return list(frame["data"]["proposals"])


def _auth_override(user_id: UUID) -> dict[str, str]:
    return {
        "sub": str(user_id),
        "email": "eval@outpost.local",
        "aud": "authenticated",
    }


# Per-task auth state. The override registered on `app.dependency_overrides`
# is a single shared callable that reads from this contextvar — each asyncio
# task running a game sets its own user_id token, so concurrent games no longer
# stomp each other's auth (which used to manifest as 429 / 404 / 401 cascades
# under `max_concurrency > 1`).
_eval_user_id_ctx: ContextVar[UUID | None] = ContextVar("eval_user_id", default=None)


def _auth_override_from_ctx() -> dict[str, str]:
    uid = _eval_user_id_ctx.get()
    if uid is None:
        raise RuntimeError(
            "eval auth override invoked without _eval_user_id_ctx set — "
            "use simulate_game / simulate_game_with_player as the entrypoint"
        )
    return _auth_override(uid)


# Per-task run seed, read by the `get_run_seed_override` dependency override.
# Same pattern as `_eval_user_id_ctx`: one shared override callable, one
# contextvar token per game task. This is how the harness `seed` reaches
# the engine, which makes (setting, seed) cells paired across players.
_eval_seed_ctx: ContextVar[int | None] = ContextVar("eval_seed", default=None)


def _seed_override_from_ctx() -> int | None:
    return _eval_seed_ctx.get()


def _install_overrides(user_id: UUID, seed: int) -> tuple[Any, Any]:
    """Register the shared ctx-reading overrides and set this task's values."""
    app.dependency_overrides.setdefault(get_current_user, _auth_override_from_ctx)
    app.dependency_overrides.setdefault(get_run_seed_override, _seed_override_from_ctx)
    return _eval_user_id_ctx.set(user_id), _eval_seed_ctx.set(seed)


def _reset_overrides(tokens: tuple[Any, Any]) -> None:
    _eval_user_id_ctx.reset(tokens[0])
    _eval_seed_ctx.reset(tokens[1])


class _RunAggregator:
    """Bucket SSE frames into the RunRecord-shaped accumulators.

    Kept private — the public API is `simulate_game`. A class lets us add
    new buckets without growing the function signature.

    Note on tool calls: this aggregator does NOT collect tool calls from
    SSE. The canonical source is the `turn_logs` table, read once after
    the run via `_read_turn_logs`. SSE only emits applied autonomous
    actions and a public-shape proposal payload that drops
    `tool_calls_on_pick`; relying on it would silently under-count the
    validity-rate denominator.
    """

    def __init__(self) -> None:
        self.agent_responses_per_agent: dict[UUID, list[str]] = {}
        self.narration_per_turn: list[str] = []

    def absorb_frames(self, frames: list[dict[str, Any]]) -> None:
        for f in frames:
            event = f["event"]
            data = f["data"]
            if not isinstance(data, dict):
                continue
            if event == "agent_spoke":
                aid = UUID(data["agent_id"])
                self.agent_responses_per_agent.setdefault(aid, []).append(data["text"])
            elif event == "narration":
                text = data.get("text")
                if text:
                    self.narration_per_turn.append(text)


def _tool_calls_from_turn_log(row: TurnLog) -> list[ToolCall]:
    """Recover every ToolCall recorded against this turn.

    Two sources, both round-tripped through `ToolCall.model_validate` so
    `rejection_reason` is a real `str | None` field on the returned models.

    1. The chosen proposal's `tool_calls_on_pick` — from the JSONB
       `proposals` column, filtered by `chosen.chosen_proposal_id`. These
       are the calls that ran through the engine at `resolve`.
    2. `autonomous_actions` — applied autonomous-phase calls.

    Calls rejected at `resolve` or `autonomous` aren't persisted in the
    `turn_logs` schema today (engine clears `turn_tool_calls` at
    `advance_turn`), so this still under-counts validity denominators on
    runs where the engine rejects calls. Documented limitation; fixing it
    requires snapshotting `turn_tool_calls` BEFORE the clear, which is an
    `app/agent/nodes.py` change out of scope for Task 5.5.
    """
    out: list[ToolCall] = []

    chosen_id = (row.chosen or {}).get("chosen_proposal_id")
    if chosen_id is not None:
        for prop in row.proposals or []:
            if prop.get("id") == chosen_id:
                for tc_dict in prop.get("tool_calls_on_pick") or []:
                    out.append(ToolCall.model_validate(tc_dict))
                break

    for tc_dict in row.autonomous_actions or []:
        out.append(ToolCall.model_validate(tc_dict))

    return out


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
      3. POST /decide — collect frames until proposals_ready (next turn) or
         run_over.

    `setting_id` is currently informational — `POST /game/session` picks the
    setting server-side per (user, UTC day). The simulator records what the
    server actually picked into the returned `RunRecord.setting_id` so the
    metrics still see the real id.

    `real_llm` is a no-op flag here; the network is either intercepted by a
    `respx.mock` block in the caller (mocked mode) or not (real mode).

    `seed` seeds both the policy RNG and the engine run (via the
    `get_run_seed_override` dependency override), so the same seed gives
    the same starting colony and event RNG for every policy.

    After the SSE loop terminates, the driver reads `turn_logs` for the
    game and walks every row to populate:

    * `tool_calls` — chosen proposal `tool_calls_on_pick` + applied
      autonomous actions, round-tripped through `ToolCall.model_validate`
      so `rejection_reason` is preserved.
    * `resource_history` — per-turn `value` for each resource id.
    * `forced_choice_turns` — turns where `len(proposals) <= 1`.

    Per-debate `agent_responses_per_agent` and per-turn `narration_per_turn`
    stay SSE-derived (one debate / one narration per turn — no aggregation
    gap there).

    Returns a `RunRecord` populated from the final DB state + per-turn
    `turn_logs` rows + the SSE-derived narration / responses streams.
    """
    player = SimulatedPlayer(policy, seed=seed)
    aggregator = _RunAggregator()

    tokens = _install_overrides(user_id, seed)
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

                chosen_id = player.pick(proposals)  # SimulatedPlayer is non-blocking

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
        _reset_overrides(tokens)

    # `ended` is informational — `final_state.end_state` is authoritative.
    _ = ended

    return await _build_run_record(game_id, aggregator, policy=policy, seed=seed)


async def simulate_game_with_player(
    *,
    setting_id: str,
    seed: int = 0,
    real_llm: bool = False,
    max_turns: int = 12,
    user_id: UUID,
    player: Any,  # ducktyped: .pick(proposals: list[dict]) -> str
    on_turn_input: Callable[[dict[str, Any]], None] | None = None,
) -> RunRecord:
    """Variant of simulate_game that accepts a pre-built player and emits
    per-turn input to a callback. Used by the LLM playtest orchestrator.

    Differences vs. simulate_game:
      - No `policy` argument; caller passes an already-constructed `player`.
      - `on_turn_input(turn_input_dict)` fires before each pick so the
        LLMPlayer can wire its turn_input_provider closure.

    The `setting_id` argument is informational (server picks the setting
    from (user_id, UTC day)); the returned RunRecord reflects what the
    server actually used.

    `seed` becomes the engine run seed (see `simulate_game`).

    `real_llm` is a no-op flag here; intercept network externally via respx.
    """
    aggregator = _RunAggregator()

    tokens = _install_overrides(user_id, seed)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            create_resp = await client.post("/game/session")
            assert create_resp.status_code == 201, create_resp.text
            game_id = UUID(create_resp.json()["game_id"])

            for _turn_idx in range(max_turns):
                # ---- /turn ----
                async with client.stream("POST", f"/game/{game_id}/turn") as resp:
                    assert resp.status_code == 200, await resp.aread()
                    turn_frames = await _collect_stream(resp)
                aggregator.absorb_frames(turn_frames)

                if any(f["event"] == "run_over" for f in turn_frames):
                    break

                pr = next((f for f in turn_frames if f["event"] == "proposals_ready"), None)
                if pr is None:
                    break
                proposals = _proposals_from_frame(pr)
                if not proposals:
                    break

                # Assemble per-turn input dict for the LLM persona and notify
                # the caller before pick() so turn_input_provider is wired.
                if on_turn_input is not None:
                    colony = await _load_final_state(game_id)
                    on_turn_input(
                        _assemble_turn_input(turn_frames, proposals, _turn_idx + 1, colony)
                    )

                # `player.pick` is sync (LLMPlayer subprocess-blocks on
                # `claude --print`); calling it inline here would block the
                # asyncio event loop, serializing all concurrent games' picks.
                # Hand it to a thread so other games' /turn HTTP calls can
                # progress while this one's persona thinks.
                chosen_id = await asyncio.to_thread(player.pick, proposals)

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
                    break
    finally:
        _reset_overrides(tokens)

    policy = str(getattr(player, "persona", None) and player.persona.id or "llm")
    return await _build_run_record(game_id, aggregator, policy=policy, seed=seed)


def _assemble_turn_input(
    turn_frames: list[dict[str, Any]],
    proposals: list[dict[str, Any]],
    turn_number: int,
    colony: ColonyState,
) -> dict[str, Any]:
    """Project SSE frames + current ColonyState into the dict shape that
    PersonaSession._format_turn_user_message expects.

    Frame names emitted by app/agent/service.py:
      - `agent_spoke`     — {agent_id, text}
      - `proposals_ready` — {turn_number, current_event, proposals}
      - `narration`       — {text}
      - `turn_advanced`   — {turn_number, legitimacy, player_health, cost_cents}
      - `decision_applied`, `autonomous_action`, `death`, `run_over`, `done`

    There is no `dashboard` or `event_intro` frame. The dashboard is assembled
    from the ColonyState (loaded after /turn completes, before pick). The event
    comes from proposals_ready.current_event.
    """
    # Event: from proposals_ready frame.
    pr = next((f for f in turn_frames if f["event"] == "proposals_ready"), None)
    raw_event = (pr["data"].get("current_event") or {}) if pr else {}

    # Advisor reactions: one agent_spoke frame per advisor.
    advisor_reactions = [
        {"agent_id": f["data"]["agent_id"], "text": f["data"]["text"]}
        for f in turn_frames
        if f["event"] == "agent_spoke" and isinstance(f.get("data"), dict)
    ]

    # Setting lore from setting file.
    try:
        setting = load_setting(colony.setting_id)
        setting_lore = setting.lore
    except Exception:  # noqa: BLE001 — file missing in test env is acceptable
        setting_lore = ""

    # Dashboard resources: {resource_id: value}.
    resources = {rid: r.value for rid, r in colony.resources.items()}

    # Advisor list from colony agents (alive only, so persona doesn't see dead advisors).
    advisors = [
        {"id": str(a.id), "name": a.name, "role": a.role} for a in colony.agents if a.is_alive
    ]

    return {
        "turn": turn_number,
        "setting_lore": setting_lore,
        "dashboard": {
            "resources": resources,
            "legitimacy": colony.legitimacy,
            "player_health": colony.player_health,
            "advisors": advisors,
        },
        "event": raw_event,
        "proposals": proposals,
        "advisor_reactions": advisor_reactions,
    }


async def _build_run_record(
    game_id: UUID,
    aggregator: _RunAggregator,
    *,
    policy: str,
    seed: int,
) -> RunRecord:
    """Shared tail logic: build a RunRecord from final DB state + turn_logs.

    Factored out so simulate_game and simulate_game_with_player both produce
    a RunRecord using the same authoritative source.
    """
    final_state = await _load_final_state(game_id)
    turn_logs = await _read_turn_logs(game_id)

    tool_calls: list[ToolCall] = []
    forced_choice_turns = 0
    resource_history: dict[str, list[int]] = {}
    for row in turn_logs:
        tool_calls.extend(_tool_calls_from_turn_log(row))
        if len(row.proposals or []) <= 1:
            forced_choice_turns += 1
        snap_resources = (row.state_snapshot or {}).get("resources") or {}
        for rid, rdict in snap_resources.items():
            value = int(rdict.get("value", 0))
            resource_history.setdefault(rid, []).append(value)

    return RunRecord(
        setting_id=final_state.setting_id,
        policy=policy,
        seed=seed,
        end_state=final_state.end_state,
        turns_played=final_state.turn_number,
        final_player_health=final_state.player_health,
        agents_alive_at_end=sum(1 for a in final_state.agents if a.is_alive),
        total_agents=len(final_state.agents),
        cost_cents=final_state.turn_cost_cents,
        tool_calls=tool_calls,
        agent_responses_per_agent=aggregator.agent_responses_per_agent,
        resource_history=resource_history,
        forced_choice_turns=forced_choice_turns,
        narration_per_turn=aggregator.narration_per_turn,
    )


__all__ = ["simulate_game", "simulate_game_with_player", "_read_turn_logs"]
