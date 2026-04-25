"""Tests for the LangGraph checkpointer wiring.

Covers the msgpack allowlist that suppresses the saver's
"Deserializing unregistered type" warnings (PLAN Task 8.0b). The bug being
guarded is exactly that: warnings spammed per checkpoint read because every
schema type in `app.game.schemas` was an unregistered constructor.
"""

from __future__ import annotations

import logging
from uuid import uuid4

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from app.agent.checkpointer import _ALLOWED_MSGPACK_TYPES
from app.agent.state import GameTurnState
from app.game.schemas import (
    Agent,
    ColonyState,
    EndState,
    EventOrigin,
    ProposalFlavor,
    Quadrant,
    Resource,
    ToolCall,
)


def _colony_state() -> ColonyState:
    agent = Agent(
        id=uuid4(),
        game_id=uuid4(),
        name="Nadia",
        role="Quartermaster",
        archetype_quadrant=Quadrant.ACCOUNTANT,
        ethics=-0.3,
        temperament=0.2,
        loyalty=0.5,
        portrait_seed="nadia",
        health=100,
        morale=60,
        grievance=3,
        is_alive=True,
    )
    return ColonyState(
        game_id=agent.game_id,
        user_id=uuid4(),
        setting_id="arctic_station_v1",
        seed=42,
        turn_number=1,
        max_turns=10,
        resources={
            "fuel": Resource(
                id="fuel", name="Fuel", icon="F", value=50, max=100, critical_threshold=15
            ),
        },
        agents=[agent],
        legitimacy=70,
        player_health=90,
        consequence_seeds=[],
        event_log=[],
        current_event=None,
        turn_proposals=[],
        turn_tool_calls=[],
    )


def test_allowlist_covers_state_types() -> None:
    """Every Pydantic / enum class on GameTurnState must be in the allowlist.

    A new schema type added without the allowlist update would re-introduce
    the warning. This test fails loudly at that point.
    """
    serde = JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED_MSGPACK_TYPES)
    allowed = serde._allowed_msgpack_modules

    state: GameTurnState = {
        "game_id": "abc",
        "colony_state": _colony_state(),
        "debate_tool_calls": [
            ToolCall(
                caller_id=uuid4(),
                name="modify_resource",
                args={"resource_id": "fuel", "delta": -3},
            ),
        ],
        "applied_tool_calls": [],
        "rejected_tool_calls": [],
        "proposals": [],
        "player_choice": None,
        "narration": "the cold held",
        "cost_cents_this_turn": 0.4,
        "is_run_over": False,
    }
    # Round-trip through the serde — any type it touches must be in the allowlist.
    typ, blob = serde.dumps_typed(state)
    serde.loads_typed((typ, blob))

    # Sanity: enums and core schema classes are registered.
    for cls in (
        Quadrant,
        ProposalFlavor,
        EventOrigin,
        EndState,
        ColonyState,
        ToolCall,
        Agent,
        Resource,
    ):
        assert (cls.__module__, cls.__name__) in allowed


def test_round_trip_emits_no_unregistered_warnings(caplog) -> None:
    """The "Deserializing unregistered type ..." warnings must not fire.

    The allowlist's purpose is exactly to silence these — if a future serde
    change reverts to permissive mode, the warning text would reappear in the
    captured log and this test would fail.
    """
    caplog.set_level(logging.WARNING, logger="langgraph.checkpoint.serde.jsonplus")
    serde = JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED_MSGPACK_TYPES)
    state: GameTurnState = {"colony_state": _colony_state()}
    typ, blob = serde.dumps_typed(state)
    serde.loads_typed((typ, blob))
    offending = [r for r in caplog.records if "unregistered" in r.message or "Blocked" in r.message]
    assert not offending, [r.message for r in offending]
