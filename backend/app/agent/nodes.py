"""LangGraph nodes for the Outpost turn graph — pre-pause half.

Each node is an `async def` over `GameTurnState` and returns a partial
update that LangGraph merges into the carried state. The graph pauses
via `interrupt_before=["resolve"]` (wired in Task 4.6); everything
below runs before that pause.

Invariants enforced here:
  * #5 — prompt caching is mandatory: system blocks come from `prompts`
    builders which stamp `cache_control: ephemeral` on each block.
  * #6 — one batched LLM call per debate. `agent_debate` makes exactly
    one `call_haiku` regardless of how many advisors are alive.
  * #11 — state mutations go through `engine.apply_tool_calls`. Nodes
    never poke fields on `ColonyState` directly for gameplay effects.
"""

from __future__ import annotations

import random

from app.agent.anthropic_client import call_haiku, cost_cents_of
from app.agent.constants import MODEL_HAIKU
from app.agent.prompts import (
    build_colony_state_block,
    build_debate_user_message,
    build_personas_block,
    build_preamble_block,
    parse_debate_response,
)
from app.agent.state import GameTurnState
from app.game import consequence, engine, events, setting_loader
from app.game.tools import AGENT_TOOL_SCHEMAS


async def generate_event(state: GameTurnState) -> GameTurnState:
    """Turn-start node: fire due seeds, pick the event, reset per-turn keys.

    Pure Python — no LLM call. Cost for this turn is reset to 0.0 here;
    downstream nodes accumulate into `cost_cents_this_turn`.
    """
    colony_state = state["colony_state"]

    # 1. Fire any consequence seeds that match this turn's number.
    new_state, fired_events = consequence.check_and_fire_seeds(colony_state)

    # 2. Load the setting YAML (validated at load time).
    setting = setting_loader.load_setting(new_state.setting_id)

    # 3. Deterministic per-(run, turn) RNG. XOR-mixing run seed, turn, and a
    #    module-local salt keeps replays identical without coupling to any
    #    library-specific seeding scheme.
    rng = random.Random(new_state.seed ^ (new_state.turn_number + 1) ^ 0x4C3D5E6F)

    # 4. Select event — seed-fired events take priority when present.
    event = events.select_event(
        new_state,
        setting,
        rng,
        seeded_events=(fired_events or None),
    )

    # 5. Surface the event both on ColonyState (persistent) and on the
    #    node-level key (convenience for downstream nodes and the API
    #    layer). Reset every per-turn field so a replay/resume starts
    #    from a clean slate.
    return {
        "colony_state": new_state.model_copy(update={"current_event": event}),
        "current_event": event,
        "cost_cents_this_turn": 0.0,
        "debate_per_agent_text": {},
        "debate_tool_calls": [],
        "applied_tool_calls": [],
        "rejected_tool_calls": [],
        "proposals": [],
        "player_choice": None,
        "autonomous_tool_calls": [],
        "narration": None,
    }


async def agent_debate(state: GameTurnState) -> GameTurnState:
    """Batched Haiku debate: one call, XML-delimited per-agent reactions.

    Invariant #6 — one LLM call per debate regardless of alive count.
    """
    colony_state = state["colony_state"]
    event = state["current_event"]
    alive_agents = [a for a in colony_state.agents if a.is_alive]

    system_blocks = [
        build_preamble_block(),
        build_personas_block(alive_agents),
        build_colony_state_block(colony_state),
    ]
    user_message = build_debate_user_message(event)

    response = await call_haiku(
        system_blocks,
        user_message,
        tools=AGENT_TOOL_SCHEMAS,
        max_tokens=2000,
    )
    cost = cost_cents_of(response, MODEL_HAIKU)
    parsed = parse_debate_response(response, alive_agents)

    return {
        "debate_per_agent_text": parsed.per_agent_text,
        "debate_tool_calls": parsed.tool_calls,
        "cost_cents_this_turn": state.get("cost_cents_this_turn", 0.0) + cost,
    }


async def apply_tool_calls(state: GameTurnState) -> GameTurnState:
    """Fold validated tool calls through the engine; split applied vs rejected.

    Pure Python. Uses `engine.apply_tool_calls`, which is invariant #11's
    single mutation pathway: it runs each tool call through the validator
    and stamps `rejection_reason` on rejects. We diff the pre/post
    `turn_tool_calls` list to recover the node-level split.
    """
    colony_state = state["colony_state"]
    pre_tc_len = len(colony_state.turn_tool_calls)

    new_colony_state = engine.apply_tool_calls(colony_state, state["debate_tool_calls"])

    newly_logged = new_colony_state.turn_tool_calls[pre_tc_len:]
    applied = [tc for tc in newly_logged if tc.rejection_reason is None]
    rejected = [tc for tc in newly_logged if tc.rejection_reason is not None]

    # Safety cap: UI only surfaces three choices. The engine does not
    # enforce the cap in Phase 3, so truncate here.
    proposals = new_colony_state.turn_proposals[:3]

    return {
        "colony_state": new_colony_state,
        "applied_tool_calls": applied,
        "rejected_tool_calls": rejected,
        "proposals": proposals,
    }
