"""Unit tests for the eight LangGraph nodes (Tasks 4.4 + 4.5).

Pre-pause (Task 4.4): generate_event, agent_debate, apply_tool_calls.
Post-pause (Task 4.5): resolve, autonomous_actions, check_deaths,
narrate_turn, advance_turn.

Covers invariants #5 (three `cache_control: ephemeral` breakpoints on the
system blocks) and #6 (one Haiku call per debate regardless of alive
count). All Anthropic traffic is mocked via `respx` at the HTTPX layer.
DB-touching tests (advance_turn) call `_require_db()` and skip when
Postgres is unreachable, matching the pattern in test_game_router.py.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
import respx
from httpx import Response
from sqlalchemy import text

from app.agent.anthropic_client import cost_cents_of
from app.agent.constants import MAX_COST_CENTS_PER_TURN, MODEL_HAIKU
from app.agent.nodes import (
    AUTONOMOUS_TOOL_NAMES,
    advance_turn,
    agent_debate,
    apply_tool_calls,
    autonomous_actions,
    check_deaths,
    generate_event,
    narrate_turn,
    resolve,
)
from app.database import async_session_factory
from app.game.schemas import (
    Agent,
    ColonyState,
    ConsequenceSeed,
    DecisionRef,
    Event,
    EventOrigin,
    Proposal,
    ProposalFlavor,
    Quadrant,
    Resource,
    SeedPayload,
    ToolCall,
)
from tests.conftest import fake_haiku_response, fake_sonnet_response

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


# ---------------------------------------------------------------------------
# resolve (Task 4.5)
# ---------------------------------------------------------------------------


def _make_proposal(
    *,
    pid: str = "p_dom",
    flavor: ProposalFlavor = ProposalFlavor.DOMINANT,
    proposer_id: UUID | None = None,
    tool_calls_on_pick: list[ToolCall] | None = None,
) -> Proposal:
    return Proposal(
        id=pid,
        proposed_by_agent_id=proposer_id,
        flavor=flavor,
        name=f"Proposal {pid}",
        description=f"Description for {pid}.",
        effects_preview=[],
        tool_calls_on_pick=tool_calls_on_pick or [],
    )


def _make_event(*, severity: int = 3, event_id: str = "evt_x") -> Event:
    return Event(
        id=event_id,
        category="political",
        title="Test event",
        body="A thing happened.",
        severity=severity,
        origin=EventOrigin.NATURAL,
    )


async def test_resolve_finds_picked_proposal_and_appends_event_log():
    colony = make_colony_state(turn=2)
    proposals = [
        _make_proposal(pid="p_a", flavor=ProposalFlavor.DOMINANT),
        _make_proposal(pid="p_b", flavor=ProposalFlavor.MORAL),
        _make_proposal(pid="p_c", flavor=ProposalFlavor.WILDCARD),
    ]
    event = _make_event(severity=2, event_id="evt_mild")  # no seed scheduling

    state: dict[str, Any] = {
        "colony_state": colony,
        "proposals": proposals,
        "player_choice": "p_b",
        "current_event": event,
    }
    result = await resolve(state)

    new_colony: ColonyState = result["colony_state"]
    assert len(new_colony.event_log) == len(colony.event_log) + 1
    last = new_colony.event_log[-1]
    assert last.chosen_proposal_id == "p_b"
    assert last.event.id == "evt_mild"
    assert last.turn_number == colony.turn_number


async def test_resolve_raises_on_unknown_player_choice():
    colony = make_colony_state(turn=2)
    proposals = [_make_proposal(pid="p_a")]
    event = _make_event(severity=2)

    state: dict[str, Any] = {
        "colony_state": colony,
        "proposals": proposals,
        "player_choice": "does_not_exist",
        "current_event": event,
    }
    with pytest.raises(ValueError):
        await resolve(state)


async def test_resolve_applies_flavor_legitimacy_shift():
    colony = make_colony_state(turn=2)
    base_legit = colony.legitimacy
    event = _make_event(severity=2)  # no seeding

    # MORAL: +2
    moral_state: dict[str, Any] = {
        "colony_state": colony,
        "proposals": [_make_proposal(pid="p_m", flavor=ProposalFlavor.MORAL)],
        "player_choice": "p_m",
        "current_event": event,
    }
    r = await resolve(moral_state)
    assert r["colony_state"].legitimacy == base_legit + 2

    # WILDCARD: -3
    wild_state: dict[str, Any] = {
        "colony_state": colony,
        "proposals": [_make_proposal(pid="p_w", flavor=ProposalFlavor.WILDCARD)],
        "player_choice": "p_w",
        "current_event": event,
    }
    r = await resolve(wild_state)
    assert r["colony_state"].legitimacy == base_legit - 3

    # DOMINANT: unchanged
    dom_state: dict[str, Any] = {
        "colony_state": colony,
        "proposals": [_make_proposal(pid="p_d", flavor=ProposalFlavor.DOMINANT)],
        "player_choice": "p_d",
        "current_event": event,
    }
    r = await resolve(dom_state)
    assert r["colony_state"].legitimacy == base_legit


async def test_resolve_schedules_consequence_seed_on_severe_event():
    colony = make_colony_state(turn=2)
    pre_seed_count = len(colony.consequence_seeds)
    event = _make_event(severity=4, event_id="evt_severe")

    state: dict[str, Any] = {
        "colony_state": colony,
        "proposals": [_make_proposal(pid="p_a", flavor=ProposalFlavor.DOMINANT)],
        "player_choice": "p_a",
        "current_event": event,
    }
    result = await resolve(state)

    new_seeds = result["colony_state"].consequence_seeds
    assert len(new_seeds) == pre_seed_count + 1
    scheduled = new_seeds[-1]
    # fires_at should be clamped to max_turns but still > current turn.
    assert scheduled.fires_at_turn > colony.turn_number
    assert scheduled.fires_at_turn <= colony.max_turns
    assert scheduled.payload.event_id == "aftermath_evt_severe"
    # severity clamped at max(1, severity-1)
    assert scheduled.payload.severity == 3


async def test_resolve_does_not_schedule_seed_on_low_severity():
    colony = make_colony_state(turn=2)
    pre_seed_count = len(colony.consequence_seeds)
    event = _make_event(severity=2, event_id="evt_low")

    state: dict[str, Any] = {
        "colony_state": colony,
        "proposals": [_make_proposal(pid="p_a", flavor=ProposalFlavor.DOMINANT)],
        "player_choice": "p_a",
        "current_event": event,
    }
    result = await resolve(state)

    assert len(result["colony_state"].consequence_seeds) == pre_seed_count


# ---------------------------------------------------------------------------
# autonomous_actions (Task 4.5)
# ---------------------------------------------------------------------------


@respx.mock
async def test_autonomous_actions_makes_one_anthropic_call():
    colony = make_colony_state()  # 4 alive
    route = respx.post(_ANTHROPIC_URL).mock(
        return_value=Response(200, json=fake_haiku_response(text_xml=""))
    )

    state: dict[str, Any] = {
        "colony_state": colony,
        "cost_cents_this_turn": 0.0,
    }
    result = await autonomous_actions(state)

    assert route.call_count == 1
    assert "autonomous_tool_calls" in result
    # Cost accumulates.
    assert result["cost_cents_this_turn"] > 0.0


async def test_autonomous_actions_skips_llm_when_no_alive_agents():
    # Build a colony where every agent is dead.
    dead_agents = [
        make_alive_agent(name="A", quadrant=Quadrant.ACCOUNTANT, is_alive=False),
        make_alive_agent(name="B", quadrant=Quadrant.OPERATOR, is_alive=False),
        make_alive_agent(name="C", quadrant=Quadrant.CONSCIENCE, is_alive=False),
        make_alive_agent(name="D", quadrant=Quadrant.CRUSADER, is_alive=False),
    ]
    colony = make_colony_state(agents=dead_agents)

    # respx.mock is NOT used — if the node makes any HTTP call it will fail
    # with an ImportError-free network error, surfacing the mistake. We also
    # assert the shape of the returned dict.
    with respx.mock():
        route = respx.post(_ANTHROPIC_URL)
        state: dict[str, Any] = {
            "colony_state": colony,
            "cost_cents_this_turn": 0.0,
        }
        result = await autonomous_actions(state)
        assert route.call_count == 0

    assert result == {"autonomous_tool_calls": []}


@respx.mock
async def test_autonomous_actions_filters_to_introspective_tools():
    colony = make_colony_state()
    route = respx.post(_ANTHROPIC_URL).mock(
        return_value=Response(200, json=fake_haiku_response(text_xml=""))
    )

    state: dict[str, Any] = {
        "colony_state": colony,
        "cost_cents_this_turn": 0.0,
    }
    await autonomous_actions(state)

    sent_body = json.loads(route.calls.last.request.content.decode("utf-8"))
    sent_tool_names = {t["name"] for t in sent_body["tools"]}
    assert sent_tool_names == set(AUTONOMOUS_TOOL_NAMES)
    # Explicitly check that the player-visible tools are excluded.
    assert "propose_action" not in sent_tool_names
    assert "modify_resource" not in sent_tool_names
    assert "modify_health" not in sent_tool_names


# ---------------------------------------------------------------------------
# check_deaths (Task 4.5)
# ---------------------------------------------------------------------------


async def test_check_deaths_flips_agent_is_alive():
    # Agent at 0 health — check_deaths should mark is_alive=False.
    agents = [
        make_alive_agent(name="Nadia", quadrant=Quadrant.ACCOUNTANT),
        make_alive_agent(name="Marko", quadrant=Quadrant.OPERATOR),
        make_alive_agent(name="Irina", quadrant=Quadrant.CONSCIENCE),
        make_alive_agent(name="Viktor", quadrant=Quadrant.CRUSADER),
    ]
    # Drop Marko to 0.
    agents[1] = agents[1].model_copy(update={"health": 0})
    colony = make_colony_state(agents=agents)

    state: dict[str, Any] = {"colony_state": colony}
    result = await check_deaths(state)

    new_colony: ColonyState = result["colony_state"]
    marko_new = next(a for a in new_colony.agents if a.name == "Marko")
    assert marko_new.is_alive is False
    # Others still alive.
    others_alive = [a for a in new_colony.agents if a.name != "Marko"]
    assert all(a.is_alive for a in others_alive)


# ---------------------------------------------------------------------------
# narrate_turn (Task 4.5)
# ---------------------------------------------------------------------------


@respx.mock
async def test_narrate_turn_skips_sonnet_when_over_budget():
    colony = make_colony_state()
    route = respx.post(_ANTHROPIC_URL)

    state: dict[str, Any] = {
        "colony_state": colony,
        "cost_cents_this_turn": float(MAX_COST_CENTS_PER_TURN) + 1.0,
        "current_event": _make_event(),
    }
    result = await narrate_turn(state)

    assert route.call_count == 0
    assert "narration" in result
    assert "Colony stands" in result["narration"]


@respx.mock
async def test_narrate_turn_calls_sonnet_when_under_budget():
    colony = make_colony_state()
    narration_text = "The wall held. Nadia counted the rations twice."
    route = respx.post(_ANTHROPIC_URL).mock(
        return_value=Response(200, json=fake_sonnet_response(narration_text))
    )

    state: dict[str, Any] = {
        "colony_state": colony,
        "cost_cents_this_turn": 1.0,
        "current_event": _make_event(),
    }
    result = await narrate_turn(state)

    assert route.call_count == 1
    assert result["narration"] == narration_text
    # Cost accumulates.
    assert result["cost_cents_this_turn"] > 1.0


@respx.mock
async def test_narrate_turn_falls_back_on_api_error():
    colony = make_colony_state()
    respx.post(_ANTHROPIC_URL).mock(return_value=Response(500, json={"error": "boom"}))

    state: dict[str, Any] = {
        "colony_state": colony,
        "cost_cents_this_turn": 1.0,
        "current_event": _make_event(),
    }
    # Should not raise; templated fallback instead.
    result = await narrate_turn(state)

    assert "narration" in result
    assert "Colony stands" in result["narration"]
    # No cost accumulation on error.
    assert "cost_cents_this_turn" not in result


# ---------------------------------------------------------------------------
# advance_turn (Task 4.5) — DB-touching; SKIP when Postgres unreachable
# ---------------------------------------------------------------------------


async def _require_db() -> None:
    """Match the pattern in tests/test_game_router.py — SKIP, not FAIL."""
    try:
        async with async_session_factory() as s:
            await s.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover — env-dependent
        pytest.skip(f"Postgres unreachable: {exc}")


async def _ensure_auth_user(user_id: UUID, email: str) -> None:
    async with async_session_factory() as s:
        await s.execute(
            text(
                """
                INSERT INTO auth.users (
                    id, instance_id, aud, role, email, encrypted_password,
                    email_confirmed_at, created_at, updated_at
                )
                VALUES (
                    :id,
                    '00000000-0000-0000-0000-000000000000'::uuid,
                    'authenticated', 'authenticated', :email,
                    '', now(), now(), now()
                )
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {"id": user_id, "email": email},
        )
        await s.execute(
            text(
                """
                INSERT INTO profiles (id, display_name, total_runs, created_at)
                VALUES (:id, :name, 0, now())
                ON CONFLICT (id) DO UPDATE SET last_run_started_at = NULL, total_runs = 0
                """
            ),
            {"id": user_id, "name": "Advance Turn Tester"},
        )
        await s.commit()


async def _delete_auth_user(user_id: UUID) -> None:
    async with async_session_factory() as s:
        await s.execute(text("DELETE FROM auth.users WHERE id = :id"), {"id": user_id})
        await s.commit()


@pytest_asyncio.fixture
async def persisted_colony():
    """Create an auth user + profile + a game_sessions row via the service,
    yield the ColonyState, and clean up via CASCADE on teardown."""
    await _require_db()
    from app.game.service import create_game_session

    user_id = uuid4()
    await _ensure_auth_user(user_id, f"advance_{user_id.hex[:8]}@outpost.local")
    try:
        async with async_session_factory() as db:
            colony = await create_game_session(user_id=user_id, db=db)
            await db.commit()
        yield colony
    finally:
        await _delete_auth_user(user_id)


async def test_advance_turn_persists_to_db_and_writes_turn_log(persisted_colony):
    """Run advance_turn against a real game_sessions row and verify both
    the scalar mirror on game_sessions and a turn_logs row are written."""
    colony = persisted_colony
    # Fabricate a current event + a resolved event on the log so the node
    # has something plausible to write to turn_logs.
    event = _make_event(severity=2, event_id="evt_adv")
    proposal = _make_proposal(pid="p_adv", flavor=ProposalFlavor.DOMINANT)
    colony = colony.model_copy(update={"current_event": event})

    state: dict[str, Any] = {
        "game_id": str(colony.game_id),
        "user_id": str(colony.user_id),
        "colony_state": colony,
        "current_event": event,
        "proposals": [proposal],
        "player_choice": "p_adv",
        "debate_per_agent_text": {},
        "autonomous_tool_calls": [],
        "narration": "The snow held.",
        "cost_cents_this_turn": 2.7,
    }

    result = await advance_turn(state)

    # Engine advanced the turn.
    new_colony: ColonyState = result["colony_state"]
    assert new_colony.turn_number == colony.turn_number + 1

    async with async_session_factory() as s:
        row = (
            await s.execute(
                text(
                    "SELECT current_turn, legitimacy, player_health, cost_cents"
                    " FROM game_sessions WHERE id = :gid"
                ),
                {"gid": colony.game_id},
            )
        ).one()
        assert row.current_turn == new_colony.turn_number
        assert row.legitimacy == new_colony.legitimacy
        assert row.player_health == new_colony.player_health
        assert row.cost_cents == new_colony.turn_cost_cents

        log_row = (
            await s.execute(
                text(
                    "SELECT turn_number, narration, turn_cost_cents"
                    " FROM turn_logs WHERE game_id = :gid"
                ),
                {"gid": colony.game_id},
            )
        ).one()
        # Pre-advance turn number recorded.
        assert log_row.turn_number == colony.turn_number
        assert log_row.narration == "The snow held."
        # Cost is rounded at write time.
        assert log_row.turn_cost_cents == round(2.7)


async def test_advance_turn_sets_is_run_over_when_end_state_set(persisted_colony):
    """Force COLLAPSED by pinning turn_number at max_turns with low legitimacy."""
    colony = persisted_colony
    # Push the run to the turn limit with resources below thresholds.
    drained_resources = {
        rid: r.model_copy(update={"value": 0}) for rid, r in colony.resources.items()
    }
    event = _make_event(severity=1, event_id="evt_endgame")
    colony = colony.model_copy(
        update={
            "turn_number": colony.max_turns,
            "legitimacy": 5,
            "resources": drained_resources,
            "current_event": event,
        }
    )
    # Persist this altered state back so advance_turn's DB read sees it.
    async with async_session_factory() as s:
        await s.execute(
            text(
                "UPDATE game_sessions SET current_turn = :t, legitimacy = :l, state = :st"
                " WHERE id = :gid"
            ),
            {
                "t": colony.turn_number,
                "l": colony.legitimacy,
                "st": json.dumps(colony.model_dump(mode="json")),
                "gid": colony.game_id,
            },
        )
        await s.commit()

    state: dict[str, Any] = {
        "game_id": str(colony.game_id),
        "user_id": str(colony.user_id),
        "colony_state": colony,
        "current_event": event,
        "proposals": [],
        "player_choice": None,
        "debate_per_agent_text": {},
        "autonomous_tool_calls": [],
        "narration": None,
        "cost_cents_this_turn": 0.0,
    }

    result = await advance_turn(state)

    assert result["is_run_over"] is True
    assert result["colony_state"].end_state is not None
