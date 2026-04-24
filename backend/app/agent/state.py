"""
GameTurnState — the mutable state carried through the Phase 4 LangGraph graph.

Each node accepts this dict and returns a partial update; LangGraph merges them.
The graph's Postgres checkpointer owns mid-turn state across HTTP boundaries
(see app.agent.graph — interrupt_before=["resolve"]).
"""

from typing import TypedDict
from uuid import UUID

from app.game.schemas import ColonyState, Event, Proposal, ToolCall


class GameTurnState(TypedDict, total=False):
    # Stable for a run — set at graph entry, never mutated.
    # str not UUID: LangGraph serializes these; UUID round-trips fine but str is simpler.
    game_id: str
    user_id: str

    # The full Pydantic ColonyState, carried live across nodes and re-persisted
    # to game_sessions.state at advance_turn. Mid-turn mutations stay in-memory
    # (+ the LangGraph checkpointer).
    colony_state: ColonyState

    # Set by generate_event, cleared/refreshed each turn.
    current_event: Event | None

    # Set by agent_debate.
    debate_per_agent_text: dict[UUID, str]  # agent_id -> short reaction paragraph
    debate_tool_calls: list[ToolCall]  # LLM-emitted, pre-validation.

    # Set by apply_tool_calls.
    applied_tool_calls: list[ToolCall]  # those that validated.
    rejected_tool_calls: list[ToolCall]  # those with a rejection_reason.
    proposals: list[Proposal]  # up to 3; surfaced to player at pause.

    # Set by /decide handler before resuming the graph.
    player_choice: str | None  # chosen_proposal_id

    # Set by autonomous_actions.
    autonomous_tool_calls: list[ToolCall]  # validated off-turn actions.

    # Set by narrate_turn (optional — may be None if budget skipped).
    narration: str | None

    # Running cost for this turn's LLM calls. Reset at generate_event, accumulated
    # by any node that makes an LLM call, written to TurnLog.turn_cost_cents at advance.
    cost_cents_this_turn: float

    # Set by advance_turn's conditional edge evaluator.
    is_run_over: bool
