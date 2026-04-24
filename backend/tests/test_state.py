"""Tests for GameTurnState TypedDict shape.

This is a type definition, not business logic. The three tests guard against:
- Partial construction breaking (total=False invariant)
- ColonyState round-trips through the dict key
- Silent removal of any of the 14 declared keys
"""

from __future__ import annotations

import typing
from uuid import uuid4

from app.agent.state import GameTurnState
from app.game.schemas import (
    Agent,
    ColonyState,
    Quadrant,
    Resource,
)

# ---------------------------------------------------------------------------
# Minimal fixture
# ---------------------------------------------------------------------------


def _make_colony_state() -> ColonyState:
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_game_turn_state_accepts_partial_dict() -> None:
    """total=False means a dict with only some keys is a valid GameTurnState."""
    state: GameTurnState = {"game_id": "abc"}
    assert state["game_id"] == "abc"


def test_game_turn_state_supports_colony_state() -> None:
    """ColonyState can be stored and retrieved from the colony_state key."""
    colony = _make_colony_state()
    state: GameTurnState = {"colony_state": colony}
    assert state["colony_state"] is colony


def test_game_turn_state_all_expected_keys_are_declared() -> None:
    """All 14 keys declared in the spec must appear in the TypedDict annotations."""
    hints = typing.get_type_hints(GameTurnState)
    expected_keys = {
        "game_id",
        "user_id",
        "colony_state",
        "current_event",
        "debate_per_agent_text",
        "debate_tool_calls",
        "applied_tool_calls",
        "rejected_tool_calls",
        "proposals",
        "player_choice",
        "autonomous_tool_calls",
        "narration",
        "cost_cents_this_turn",
        "is_run_over",
    }
    missing = expected_keys - hints.keys()
    assert not missing, f"Keys missing from GameTurnState: {missing}"
