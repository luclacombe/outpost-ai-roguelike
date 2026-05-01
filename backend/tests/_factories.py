"""Pure schema fabricators shared across unit tests.

Distinct from `tests/fixtures/_factories.py` (which builds respx HTTP body
factories for eval-pipeline tests). This module is for synchronous,
in-memory `ColonyState`/`Agent`/`Resource` builders used by engine,
validator, prompts, and node tests.

Originally lived inside `tests/test_engine.py`; extracted because a dozen
other test modules imported them across files, making renames silently
fragile.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from app.game.schemas import (
    Agent,
    ColonyState,
    Quadrant,
    Resource,
)

GAME_ID = UUID("aaaaaaaa-0000-0000-0000-000000000001")
USER_ID = UUID("bbbbbbbb-0000-0000-0000-000000000002")


def _agent(
    name: str = "A",
    grievance: int = 0,
    temperament: float = 0.0,
    health: int = 100,
    alive: bool = True,
    **over,
) -> Agent:
    return Agent(
        id=over.pop("id", uuid4()),
        game_id=GAME_ID,
        name=name,
        role="Advisor",
        archetype_quadrant=Quadrant.ACCOUNTANT,
        ethics=0.0,
        temperament=temperament,
        loyalty=0.0,
        portrait_seed="seed",
        health=health,
        morale=50,
        grievance=grievance,
        is_alive=alive,
        **over,
    )


def _resource(rid: str = "fuel", value: int = 50, max_: int = 100, crit: int = 20) -> Resource:
    return Resource(
        id=rid,
        name=rid.capitalize(),
        icon="X",
        value=value,
        max=max_,
        critical_threshold=crit,
    )


def _colony(
    agents: list[Agent] | None = None,
    resources: dict[str, Resource] | None = None,
    turn_number: int = 1,
    max_turns: int = 10,
    legitimacy: int = 60,
    player_health: int = 100,
    **over,
) -> ColonyState:
    if agents is None:
        agents = [_agent()]
    if resources is None:
        resources = {"fuel": _resource()}
    defaults: dict = {
        "consequence_seeds": [],
        "event_log": [],
        "current_event": None,
        "turn_proposals": [],
        "turn_tool_calls": [],
    }
    defaults.update(over)
    return ColonyState(
        game_id=GAME_ID,
        user_id=USER_ID,
        setting_id="arctic_station_v1",
        seed=42,
        turn_number=turn_number,
        max_turns=max_turns,
        resources=resources,
        agents=agents,
        legitimacy=legitimacy,
        player_health=player_health,
        **defaults,
    )
