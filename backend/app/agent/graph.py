"""Phase 4 LangGraph StateGraph assembly.

Linear topology with `interrupt_before=["resolve"]`:

    START -> generate_event -> agent_debate -> apply_tool_calls -> [pause]
      resolve -> autonomous_actions -> check_deaths -> narrate_turn
      -> advance_turn -> {generate_event if not is_run_over else END}

Invariant #12: every call site MUST pass
`config={"configurable": {"thread_id": str(game_id)}}`. Without it the
Postgres checkpointer can't find the paused state from /turn when
/decide fires. Use `thread_config` below.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agent import nodes
from app.agent.state import GameTurnState


def _route_after_advance(state: GameTurnState) -> str:
    return "__end__" if state.get("is_run_over") else "generate_event"


def build_graph(checkpointer: Any):
    """Build and compile the Outpost Phase 4 graph.

    `checkpointer` is passed in explicitly so tests can use MemorySaver
    and production can use AsyncPostgresSaver without this module knowing
    about either.
    """
    builder = StateGraph(GameTurnState)

    builder.add_node("generate_event", nodes.generate_event)
    builder.add_node("agent_debate", nodes.agent_debate)
    builder.add_node("apply_tool_calls", nodes.apply_tool_calls)
    builder.add_node("resolve", nodes.resolve)
    builder.add_node("autonomous_actions", nodes.autonomous_actions)
    builder.add_node("check_deaths", nodes.check_deaths)
    builder.add_node("narrate_turn", nodes.narrate_turn)
    builder.add_node("advance_turn", nodes.advance_turn)

    builder.add_edge(START, "generate_event")
    builder.add_edge("generate_event", "agent_debate")
    builder.add_edge("agent_debate", "apply_tool_calls")
    # Graph pauses before resolve; the /decide handler resumes by calling
    # graph.ainvoke(None, config) after updating state["player_choice"].
    builder.add_edge("apply_tool_calls", "resolve")
    builder.add_edge("resolve", "autonomous_actions")
    builder.add_edge("autonomous_actions", "check_deaths")
    builder.add_edge("check_deaths", "narrate_turn")
    builder.add_edge("narrate_turn", "advance_turn")
    builder.add_conditional_edges(
        "advance_turn",
        _route_after_advance,
        {"generate_event": "generate_event", "__end__": END},
    )

    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["resolve"],
    )


def thread_config(game_id: str) -> dict:
    """Canonical config for any ainvoke/astream call site — invariant #12."""
    return {"configurable": {"thread_id": str(game_id)}}
