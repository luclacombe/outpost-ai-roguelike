"""Async-iterator service layer for the SSE /turn and /decide routes.

The two streaming functions translate LangGraph's `astream(stream_mode="updates")`
output into the SSE event envelopes documented in `schemas_io.py`. The
router stays thin (parse → service → StreamingResponse); this module owns:

  - ownership check (delegated to `app.game.service.get_game_session`),
  - thread_id discipline (invariant #12 guard via `_require_thread_id`),
  - per-node update → SSE frame translation,
  - terminal disposition (proposals_ready re-pause vs run_over vs double-POST
    /turn short-circuit).

Every `ainvoke` / `astream` / `aget_state` / `aupdate_state` call site
passes `config=thread_config(str(game_id))`. No bare calls.

### Double-POST /turn

If `/turn` is invoked while the graph is already paused at `resolve`, we
re-emit the paused `proposals_ready` + `[DONE]` without running the graph
again. That makes the frontend's reconnect/replay flow idempotent: a lost
SSE connection can re-POST /turn and receive the same proposals.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.graph import thread_config
from app.agent.observability import with_observability
from app.agent.state import GameTurnState
from app.game.exceptions import GameNotFound
from app.game.preview import render_close
from app.game.schemas import Agent, ColonyState, Proposal
from app.game.schemas_io import ProposalPublic
from app.game.service import get_game_session

_DONE_SENTINEL = "[DONE]"


def _sse_frame(event: str, data: dict | str) -> str:
    """Render a single SSE frame.

    Contract: the blank line at the end (the `\\n\\n`) is the frame
    separator — every frame ends with it. If `data` is the literal string
    `"[DONE]"`, skip JSON encoding and write it verbatim; otherwise
    `json.dumps` the payload.
    """
    if isinstance(data, str) and data == _DONE_SENTINEL:
        return f"event: {event}\ndata: {_DONE_SENTINEL}\n\n"
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _require_thread_id(cfg: dict) -> None:
    """Invariant #12 guard — fail fast on missing/empty thread_id.

    Without a thread_id the Postgres checkpointer cannot locate the paused
    state between /turn and /decide; the graph would silently start a
    fresh run on resume. Catching the caller error here is cheap.
    """
    configurable = cfg.get("configurable") or {}
    tid = configurable.get("thread_id")
    if not tid:
        raise RuntimeError("thread_config produced an empty thread_id; invariant #12 violated")


def _proposal_public(p: Proposal) -> dict[str, Any]:
    """Project a Proposal to the player-facing ProposalPublic JSON shape."""
    return ProposalPublic(
        id=p.id,
        proposed_by_agent_id=p.proposed_by_agent_id,
        flavor=p.flavor,
        name=p.name,
        description=p.description,
        effects_preview=p.effects_preview,
        risk_note=p.risk_note,
    ).model_dump(mode="json")


def _proposals_ready_payload(state_values: dict[str, Any]) -> dict[str, Any]:
    """Build the `proposals_ready` payload from a paused snapshot's values."""
    colony: ColonyState | None = state_values.get("colony_state")
    proposals: list[Proposal] = state_values.get("proposals") or []
    current_event = state_values.get("current_event")
    # `current_event` may come back as either the Event Pydantic model (mid-
    # stream) or a dict (round-tripped through the checkpointer). Handle both.
    if current_event is not None and not isinstance(current_event, dict):
        current_event = current_event.model_dump(mode="json")
    return {
        "turn_number": colony.turn_number if colony is not None else None,
        "current_event": current_event,
        "proposals": [_proposal_public(p) for p in proposals],
        "close_preview": render_close(),
    }


def _run_over_payload(state_values: dict[str, Any]) -> dict[str, Any]:
    """Build the `run_over` payload from a terminal snapshot's values."""
    colony: ColonyState = state_values["colony_state"]
    end_state = colony.end_state
    return {
        "end_state": end_state.value if end_state is not None else None,
        "turn_number": colony.turn_number,
        "cost_cents": colony.turn_cost_cents,
    }


def _alive_map(agents: list[Agent]) -> dict[UUID, bool]:
    return {a.id: a.is_alive for a in agents}


async def _raise_if_not_owned(*, game_id: UUID, user_id: UUID, db: AsyncSession) -> ColonyState:
    """Reuse `get_game_session` as the ownership gate.

    `GameNotFound` is mapped to 404 with the error envelope documented in
    root CLAUDE.md. The loaded ColonyState is returned so callers don't
    have to re-query when they need it (e.g. /turn's initial state).
    """
    try:
        return await get_game_session(user_id=user_id, game_id=game_id, db=db)
    except GameNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": exc.to_dict()},
        ) from exc


def _translate_update(
    node_name: str,
    update: dict[str, Any],
    *,
    pre_alive: dict[UUID, bool],
    agents_by_id: dict[UUID, Agent],
) -> list[str]:
    """Turn one `astream(stream_mode="updates")` payload into SSE frames.

    Returns a list because a single node update (e.g. debate → multiple
    agent_spoke events) can expand into several frames.
    """
    frames: list[str] = []

    if node_name == "agent_debate":
        per_agent = update.get("debate_per_agent_text") or {}
        for agent_id, text in per_agent.items():
            frames.append(
                _sse_frame(
                    "agent_spoke",
                    {"agent_id": str(agent_id), "text": text},
                )
            )
        return frames

    if node_name == "resolve":
        colony: ColonyState = update["colony_state"]
        # `player_choice` lives on the carried state — not on this node's
        # update payload — so we pull it from the most-recent resolved
        # event log entry, which `resolve` just appended.
        chosen_id = colony.event_log[-1].chosen_proposal_id if colony.event_log else None
        frames.append(
            _sse_frame(
                "decision_applied",
                {
                    "chosen_proposal_id": chosen_id,
                    "turn_number": colony.turn_number,
                },
            )
        )
        return frames

    if node_name == "autonomous_actions":
        tool_calls = update.get("autonomous_tool_calls") or []
        for tc in tool_calls:
            # `caller_id` is set by `debate_format.parse_submit_offturn`, so
            # `tc.args` should carry no `caller_agent_id`. The
            # defensive pop below guards against an alternate path ever
            # leaking it; cost is negligible for the small args dict.
            clean_args = {k: v for k, v in tc.args.items() if k != "caller_agent_id"}
            frames.append(
                _sse_frame(
                    "autonomous_action",
                    {
                        "agent_id": str(tc.caller_id) if tc.caller_id else None,
                        "tool_name": tc.name,
                        "args": clean_args,
                    },
                )
            )
        return frames

    if node_name == "check_deaths":
        new_colony: ColonyState = update["colony_state"]
        for agent in new_colony.agents:
            was_alive = pre_alive.get(agent.id, True)
            if was_alive and not agent.is_alive:
                frames.append(
                    _sse_frame(
                        "death",
                        {
                            "agent_id": str(agent.id),
                            "agent_name": agent.name,
                            "turn": new_colony.turn_number,
                        },
                    )
                )
        return frames

    if node_name == "narrate_turn":
        narration = update.get("narration")
        if narration:
            frames.append(_sse_frame("narration", {"text": narration}))
        return frames

    if node_name == "advance_turn":
        colony: ColonyState = update["colony_state"]
        frames.append(
            _sse_frame(
                "turn_advanced",
                {
                    "turn_number": colony.turn_number,
                    "legitimacy": colony.legitimacy,
                    "player_health": colony.player_health,
                    "cost_cents": colony.turn_cost_cents,
                },
            )
        )
        return frames

    # generate_event, apply_tool_calls → no per-node SSE (proposals surface
    # at pause via `proposals_ready`). Unknown nodes are silently ignored.
    _ = agents_by_id  # referenced to keep the signature stable for callers
    return frames


async def _stream_graph_updates(
    graph: Any,
    input_value: Any,
    cfg: dict,
    *,
    pre_alive: dict[UUID, bool],
    agents_by_id: dict[UUID, Agent],
) -> AsyncIterator[str]:
    """Common body: iterate `astream` and yield translated SSE frames.

    `input_value` is either `GameTurnState` (fresh /turn) or `None` (resume
    from pause in /decide).
    """
    async for chunk in graph.astream(input_value, config=cfg, stream_mode="updates"):
        # stream_mode="updates" yields {node_name: partial_state_update}.
        for node_name, update in chunk.items():
            if not isinstance(update, dict):
                # LangGraph sometimes emits non-dict payloads for
                # special keys (e.g. `__interrupt__`); skip gracefully.
                continue
            for frame in _translate_update(
                node_name, update, pre_alive=pre_alive, agents_by_id=agents_by_id
            ):
                yield frame


async def prepare_turn(
    *,
    game_id: UUID,
    user_id: UUID,
    db: AsyncSession,
    request: Request,
) -> dict[str, Any]:
    """Run /turn's pre-stream ownership check and thread-id guard.

    Returns a handoff dict the router passes into `stream_turn`. Raises
    404 `game_not_found` for a missing/foreign game.

    Split from the streaming generator because `StreamingResponse` only
    starts iterating the body after status + headers are sent —
    HTTPExceptions raised inside the generator can't surface as normal
    JSON responses anymore.
    """
    colony = await _raise_if_not_owned(game_id=game_id, user_id=user_id, db=db)

    graph = request.app.state.graph
    cfg = thread_config(str(game_id))
    _require_thread_id(cfg)
    # Observability cfg carries Langfuse callbacks + metadata. State-read
    # ops (`aget_state` / `aupdate_state`) intentionally use the bare cfg
    # so they don't duplicate spans; streaming uses the enriched cfg.
    stream_cfg = with_observability(
        cfg,
        game_id=str(game_id),
        user_id=str(user_id),
        setting_id=colony.setting_id,
        turn_number=colony.turn_number,
    )

    return {
        "graph": graph,
        "cfg": cfg,
        "stream_cfg": stream_cfg,
        "colony": colony,
        "game_id": str(game_id),
        "user_id": str(user_id),
    }


async def stream_turn(
    *,
    prepared: dict[str, Any],
) -> AsyncIterator[str]:
    """Drive the pre-pause half of the graph and stream events to the client.

    Assumes `prepare_turn` already ran (ownership + thread-id checks). If
    the graph is already paused at `resolve` when /turn is called, we
    re-emit the paused `proposals_ready` + `[DONE]` without running the
    graph (see module docstring on double-POST /turn).
    """
    graph = prepared["graph"]
    cfg = prepared["cfg"]
    stream_cfg = prepared.get("stream_cfg", cfg)
    colony: ColonyState = prepared["colony"]

    # If the graph is already paused for this thread_id, short-circuit
    # and re-emit the paused turn's frames. The frontend's reconnect/replay
    # flow becomes idempotent this way.
    #
    # The graph topology fires the next turn's pre-pause half (generate_event
    # → agent_debate → apply_tool_calls) at the END of the previous /decide,
    # so when /turn arrives next, agent_debate has already run. We must
    # re-emit the cached `agent_spoke` frames here — otherwise the harness
    # / frontend assembling the dashboard sees zero advisor reactions and
    # renders every advisor as `(silent)` (smoke 2026-04-29 regression).
    snapshot = await graph.aget_state(cfg)
    if snapshot.next == ("resolve",):
        cached_per_agent = snapshot.values.get("debate_per_agent_text") or {}
        for agent_id, text in cached_per_agent.items():
            yield _sse_frame(
                "agent_spoke",
                {"agent_id": str(agent_id), "text": text},
            )
        yield _sse_frame("proposals_ready", _proposals_ready_payload(snapshot.values))
        yield _sse_frame("done", _DONE_SENTINEL)
        return

    initial_state: GameTurnState = {
        "game_id": prepared["game_id"],
        "user_id": prepared["user_id"],
        "colony_state": colony,
        "cost_cents_this_turn": 0.0,
    }

    pre_alive = _alive_map(colony.agents)
    agents_by_id = {a.id: a for a in colony.agents}

    async for frame in _stream_graph_updates(
        graph, initial_state, stream_cfg, pre_alive=pre_alive, agents_by_id=agents_by_id
    ):
        yield frame

    # Terminal disposition.
    snapshot = await graph.aget_state(cfg)
    if snapshot.next == ("resolve",):
        yield _sse_frame("proposals_ready", _proposals_ready_payload(snapshot.values))
    else:
        # Defensive: a healthy run's turn 1 should always pause; a graph
        # that reached END on the initial partial invocation is an edge
        # case but we surface it rather than hang.
        yield _sse_frame("run_over", _run_over_payload(snapshot.values))

    yield _sse_frame("done", _DONE_SENTINEL)


async def prepare_decide(
    *,
    game_id: UUID,
    user_id: UUID,
    chosen_proposal_id: str,
    db: AsyncSession,
    request: Request,
) -> dict[str, Any]:
    """Run /decide's pre-stream validations and return a handoff dict.

    StreamingResponse starts iterating the generator AFTER it has sent
    200 OK + headers, so an HTTPException raised inside `stream_decide`
    never reaches FastAPI's exception handler as a clean JSON response.
    The fix is to run the validations here — *before* the route returns
    a StreamingResponse — and pass the validated artifacts (snapshot
    alive-map, agents) into the generator so it can do its work without
    re-checking.

    Raises:
      * 404 `game_not_found` — via `_raise_if_not_owned`.
      * 409 `not_paused`     — graph is not waiting at `resolve`.
      * 400 `unknown_proposal` — id not among the offered proposals.
    """
    await _raise_if_not_owned(game_id=game_id, user_id=user_id, db=db)

    graph = request.app.state.graph
    cfg = thread_config(str(game_id))
    _require_thread_id(cfg)

    snapshot = await graph.aget_state(cfg)
    if snapshot.next != ("resolve",):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "not_paused",
                    "message": ("Call /turn first; the run is not waiting on a decision."),
                }
            },
        )

    paused_proposals: list[Proposal] = snapshot.values.get("proposals") or []
    if chosen_proposal_id not in [p.id for p in paused_proposals]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "unknown_proposal",
                    "message": (
                        f"chosen_proposal_id {chosen_proposal_id!r} did not match "
                        "any offered proposal."
                    ),
                }
            },
        )

    paused_colony: ColonyState = snapshot.values["colony_state"]
    # Reuse the paused snapshot we already loaded — no second `aget_state`
    # round-trip — to derive the metadata for Langfuse traces.
    stream_cfg = with_observability(
        cfg,
        game_id=str(game_id),
        user_id=str(user_id),
        setting_id=paused_colony.setting_id,
        turn_number=paused_colony.turn_number,
    )
    return {
        "graph": graph,
        "cfg": cfg,
        "stream_cfg": stream_cfg,
        "pre_alive": _alive_map(paused_colony.agents),
        "agents_by_id": {a.id: a for a in paused_colony.agents},
    }


async def stream_decide(
    *,
    chosen_proposal_id: str,
    prepared: dict[str, Any],
) -> AsyncIterator[str]:
    """Apply the player's choice and stream the post-pause half of the turn.

    Assumes `prepare_decide` already ran (ownership, pause, and proposal-id
    checks passed). `prepared` carries the graph handle, the thread config,
    and the pre-pause alive-map so `check_deaths` can diff correctly.
    """
    graph = prepared["graph"]
    cfg = prepared["cfg"]
    stream_cfg = prepared.get("stream_cfg", cfg)
    pre_alive = prepared["pre_alive"]
    agents_by_id = prepared["agents_by_id"]

    # Write the player choice into the carried state, then resume by
    # streaming with `input=None` — LangGraph picks up where it paused.
    # `aupdate_state` uses the bare cfg (state mutation, no LLM span).
    await graph.aupdate_state(cfg, {"player_choice": chosen_proposal_id})

    async for frame in _stream_graph_updates(
        graph, None, stream_cfg, pre_alive=pre_alive, agents_by_id=agents_by_id
    ):
        yield frame

    # Terminal disposition: either the graph paused again at the next
    # turn's `resolve`, or it reached END (run over).
    snapshot = await graph.aget_state(cfg)
    if snapshot.next == ("resolve",):
        yield _sse_frame("proposals_ready", _proposals_ready_payload(snapshot.values))
    else:
        yield _sse_frame("run_over", _run_over_payload(snapshot.values))

    yield _sse_frame("done", _DONE_SENTINEL)
