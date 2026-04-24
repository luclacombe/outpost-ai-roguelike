"""Unit tests for the three pre-pause LangGraph nodes.

Covers invariants #5 (three `cache_control: ephemeral` breakpoints on the
system blocks) and #6 (one Haiku call per debate regardless of alive
count). All Anthropic traffic is mocked via `respx` at the HTTPX layer.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import pytest
import respx
from httpx import Response

from app.agent.anthropic_client import cost_cents_of
from app.agent.constants import MODEL_HAIKU
from app.agent.nodes import agent_debate, apply_tool_calls, generate_event
from app.game.schemas import (
    Agent,
    ColonyState,
    ConsequenceSeed,
    DecisionRef,
    Event,
    EventOrigin,
    Quadrant,
    Resource,
    SeedPayload,
    ToolCall,
)
from tests.conftest import fake_haiku_response

# The AsyncAnthropic SDK hits this URL; verified via the probe recommended
# in the task brief (AsyncAnthropic(api_key=...).base_url is
# "https://api.anthropic.com" and messages.create posts to /v1/messages).
_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

# Real setting shipped in app/game/settings/. Pinned so the event pool is
# stable across test runs.
_SETTING_ID = "arctic_station_v1"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_alive_agent(
    *,
    name: str = "Nadia",
    role: str = "Quartermaster",
    quadrant: Quadrant = Quadrant.ACCOUNTANT,
    agent_id: UUID | None = None,
    game_id: UUID | None = None,
    is_alive: bool = True,
) -> Agent:
    return Agent(
        id=agent_id or uuid4(),
        game_id=game_id or uuid4(),
        name=name,
        role=role,
        archetype_quadrant=quadrant,
        ethics=-0.2,
        temperament=0.1,
        loyalty=0.4,
        portrait_seed=name.lower(),
        health=100,
        morale=60,
        grievance=2,
        is_alive=is_alive,
        memory=[],
        relationships={},
        verbal_tic="cold numbers",
    )


def make_colony_state(
    agents: list[Agent] | None = None,
    *,
    turn: int = 1,
    seed: int = 42,
    game_id: UUID | None = None,
    consequence_seeds: list[ConsequenceSeed] | None = None,
) -> ColonyState:
    gid = game_id or uuid4()
    if agents is None:
        agents = [
            make_alive_agent(name="Nadia", quadrant=Quadrant.ACCOUNTANT, game_id=gid),
            make_alive_agent(name="Marko", quadrant=Quadrant.OPERATOR, game_id=gid),
            make_alive_agent(name="Irina", quadrant=Quadrant.CONSCIENCE, game_id=gid),
            make_alive_agent(name="Viktor", quadrant=Quadrant.CRUSADER, game_id=gid),
        ]
    else:
        # Align agent game_ids with the colony game_id for consistency.
        agents = [a.model_copy(update={"game_id": gid}) for a in agents]
    return ColonyState(
        game_id=gid,
        user_id=uuid4(),
        setting_id=_SETTING_ID,
        seed=seed,
        turn_number=turn,
        max_turns=10,
        resources={
            "fuel": Resource(
                id="fuel", name="Fuel", icon="flame", value=70, max=100, critical_threshold=15
            ),
            "food": Resource(
                id="food", name="Food", icon="can", value=80, max=100, critical_threshold=20
            ),
        },
        agents=agents,
        legitimacy=70,
        player_health=90,
        consequence_seeds=consequence_seeds or [],
        event_log=[],
        current_event=None,
        turn_proposals=[],
        turn_tool_calls=[],
    )


def _fake_response_for_cost(usage_fields: dict[str, int]) -> Any:
    """Shape-compatible stand-in for an Anthropic response used by cost_cents_of.

    `usage_fields` uses the exact SDK attribute names (input_tokens,
    output_tokens, cache_read_input_tokens, cache_creation_input_tokens).
    Used to compute expected cost for the accumulate-cost test without
    re-sending traffic through respx.
    """

    class _U:
        pass

    class _R:
        pass

    u = _U()
    for k, v in usage_fields.items():
        setattr(u, k, v)
    r = _R()
    r.usage = u
    return r


# ---------------------------------------------------------------------------
# generate_event
# ---------------------------------------------------------------------------


async def test_generate_event_sets_current_event_from_setting_pool():
    state: dict[str, Any] = {"colony_state": make_colony_state(turn=1)}
    result = await generate_event(state)

    assert result["current_event"] is not None
    assert isinstance(result["current_event"], Event)
    # Current event should also land on the ColonyState itself.
    assert result["colony_state"].current_event == result["current_event"]
    # All per-turn node keys reset.
    assert result["cost_cents_this_turn"] == 0.0
    assert result["debate_per_agent_text"] == {}
    assert result["debate_tool_calls"] == []
    assert result["applied_tool_calls"] == []
    assert result["rejected_tool_calls"] == []
    assert result["proposals"] == []
    assert result["player_choice"] is None
    assert result["autonomous_tool_calls"] == []
    assert result["narration"] is None


async def test_generate_event_prefers_fired_seed_over_pool_event():
    game_id = uuid4()
    colony = make_colony_state(turn=3, game_id=game_id)
    seed = ConsequenceSeed(
        game_id=game_id,
        written_at_turn=1,
        fires_at_turn=3,
        foreshadowed=False,
        foreshadow_turn=None,
        payload=SeedPayload(
            event_id="seeded_backlash",
            severity=4,
            category="political",
            description="The miners remember last week's concession.",
        ),
        origin_decision=DecisionRef(
            turn_number=1,
            event_id="evt_initial",
            chosen_proposal_id="p_a",
        ),
    )
    colony = colony.model_copy(update={"consequence_seeds": [seed]})
    state: dict[str, Any] = {"colony_state": colony}

    result = await generate_event(state)

    event: Event = result["current_event"]
    assert event.origin == EventOrigin.SEED_FIRED
    assert event.origin_seed_id == seed.id
    # The seed should also be marked as fired on the returned colony state.
    seeds = result["colony_state"].consequence_seeds
    assert any(s.id == seed.id and s.fired for s in seeds)


async def test_generate_event_is_deterministic_for_same_seed_and_turn():
    colony_a = make_colony_state(turn=2, seed=1337)
    colony_b = make_colony_state(turn=2, seed=1337)
    # Different game_ids/user_ids don't feed the RNG, so identical event ids
    # are expected.
    r1 = await generate_event({"colony_state": colony_a})
    r2 = await generate_event({"colony_state": colony_b})
    assert r1["current_event"].id == r2["current_event"].id
    assert r1["current_event"].category == r2["current_event"].category


# ---------------------------------------------------------------------------
# agent_debate
# ---------------------------------------------------------------------------


@respx.mock
async def test_agent_debate_makes_exactly_one_anthropic_call():
    """Invariant #6: one batched Haiku call regardless of alive count."""
    colony = make_colony_state()  # 4 alive by default
    event = Event(
        id="evt_storm",
        category="weather",
        title="Ice storm",
        body="Winds peel the north wall.",
        severity=3,
        origin=EventOrigin.NATURAL,
    )

    alive = [a for a in colony.agents if a.is_alive]
    xml = "".join(f'<agent name="{a.name}">She nods.</agent>' for a in alive)
    route = respx.post(_ANTHROPIC_URL).mock(
        return_value=Response(200, json=fake_haiku_response(text_xml=xml))
    )

    state: dict[str, Any] = {
        "colony_state": colony,
        "current_event": event,
        "cost_cents_this_turn": 0.0,
    }
    await agent_debate(state)

    assert route.call_count == 1


@respx.mock
async def test_agent_debate_accumulates_cost_cents():
    colony = make_colony_state()
    event = Event(
        id="evt_x",
        category="weather",
        title="Ice storm",
        body="Wind.",
        severity=2,
        origin=EventOrigin.NATURAL,
    )

    usage_fields = {
        "input_tokens": 2_000,
        "output_tokens": 500,
        "cache_read_input_tokens": 1_500,
        "cache_creation_input_tokens": 0,
    }
    respx.post(_ANTHROPIC_URL).mock(
        return_value=Response(
            200,
            json=fake_haiku_response(
                text_xml='<agent name="Nadia">ok.</agent>',
                usage_overrides=usage_fields,
            ),
        )
    )

    expected_delta = cost_cents_of(_fake_response_for_cost(usage_fields), MODEL_HAIKU)

    state: dict[str, Any] = {
        "colony_state": colony,
        "current_event": event,
        "cost_cents_this_turn": 5.0,
    }
    result = await agent_debate(state)

    assert result["cost_cents_this_turn"] == pytest.approx(5.0 + expected_delta)


@respx.mock
async def test_agent_debate_sends_3_cache_control_breakpoints():
    """Invariant #5: three ephemeral cache_control breakpoints on system."""
    colony = make_colony_state()
    event = Event(
        id="evt_y",
        category="weather",
        title="Ice storm",
        body="Wind.",
        severity=2,
        origin=EventOrigin.NATURAL,
    )

    route = respx.post(_ANTHROPIC_URL).mock(
        return_value=Response(
            200,
            json=fake_haiku_response(text_xml='<agent name="Nadia">ok.</agent>'),
        )
    )

    state: dict[str, Any] = {
        "colony_state": colony,
        "current_event": event,
        "cost_cents_this_turn": 0.0,
    }
    await agent_debate(state)

    assert route.call_count == 1
    sent_body = json.loads(route.calls.last.request.content.decode("utf-8"))
    system_blocks = sent_body["system"]
    ephemeral_count = sum(
        1
        for block in system_blocks
        if isinstance(block, dict) and block.get("cache_control") == {"type": "ephemeral"}
    )
    assert ephemeral_count == 3, (
        f"expected 3 ephemeral cache_control breakpoints, got {ephemeral_count}"
    )


# ---------------------------------------------------------------------------
# apply_tool_calls
# ---------------------------------------------------------------------------


async def test_apply_tool_calls_populates_proposals_from_propose_action():
    nadia = make_alive_agent(name="Nadia")
    colony = make_colony_state(agents=[nadia])
    # Re-read Nadia post-rebinding (colony rebinds game_ids).
    nadia = colony.agents[0]

    tc = ToolCall(
        name="propose_action",
        caller_id=nadia.id,
        args={
            "name": "Patch the wall",
            "description": "Burn fuel to patch it fast.",
            "flavor": "DOMINANT",
            "effects_preview": ["-10 fuel"],
        },
    )

    state: dict[str, Any] = {
        "colony_state": colony,
        "debate_tool_calls": [tc],
    }
    result = await apply_tool_calls(state)

    assert len(result["proposals"]) == 1
    proposal = result["proposals"][0]
    assert proposal.name == "Patch the wall"
    assert proposal.flavor.value == "DOMINANT"
    assert proposal.proposed_by_agent_id == nadia.id
    assert len(result["applied_tool_calls"]) == 1
    assert len(result["rejected_tool_calls"]) == 0


async def test_apply_tool_calls_separates_applied_from_rejected():
    nadia = make_alive_agent(name="Nadia")
    colony = make_colony_state(agents=[nadia])
    nadia = colony.agents[0]

    valid = ToolCall(
        name="modify_resource",
        caller_id=nadia.id,
        args={"resource_id": "fuel", "delta": -5, "reason": "patching"},
    )
    invalid = ToolCall(
        name="modify_resource",
        caller_id=nadia.id,
        args={
            "resource_id": "nonexistent",
            "delta": -5,
            "reason": "bogus",
        },
    )

    state: dict[str, Any] = {
        "colony_state": colony,
        "debate_tool_calls": [valid, invalid],
    }
    result = await apply_tool_calls(state)

    assert len(result["applied_tool_calls"]) == 1
    assert result["applied_tool_calls"][0].args["resource_id"] == "fuel"
    assert result["applied_tool_calls"][0].rejection_reason is None

    assert len(result["rejected_tool_calls"]) == 1
    assert result["rejected_tool_calls"][0].rejection_reason == "unknown_resource"


async def test_apply_tool_calls_caps_proposals_at_3():
    nadia = make_alive_agent(name="Nadia")
    colony = make_colony_state(agents=[nadia])
    nadia = colony.agents[0]

    tcs = [
        ToolCall(
            name="propose_action",
            caller_id=nadia.id,
            args={
                "name": f"Proposal {i}",
                "description": f"Option {i}.",
                "flavor": "DOMINANT" if i == 0 else "MORAL" if i == 1 else "WILDCARD",
            },
        )
        for i in range(4)
    ]

    state: dict[str, Any] = {
        "colony_state": colony,
        "debate_tool_calls": tcs,
    }
    result = await apply_tool_calls(state)

    assert len(result["proposals"]) == 3
    # All four should still show up as validated tool calls on the engine log.
    assert len(result["applied_tool_calls"]) == 4
