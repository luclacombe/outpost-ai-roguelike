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
import re
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
    ResolvedEvent,
    Resource,
    SeedPayload,
    ToolCall,
)
from app.game.tools import AGENT_TOOL_SCHEMAS
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
    """Invariant #6: one batched Haiku call regardless of alive count (when
    it yields 3 valid proposals; fewer triggers the single retry)."""
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
        return_value=Response(
            200, json=fake_haiku_response(text_xml=xml, tool_uses=_valid_tool_uses(colony))
        )
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
                tool_uses=_valid_tool_uses(colony),
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
async def test_agent_debate_cache_control_layout():
    """Invariant #5: cache_control placement must make the cached prefix
    cross-turn stable. Concretely: the LAST system block must have
    cache_control, and run-stable content (preamble + personas) must live
    in system while per-turn colony_state lives in the user message.

    Background: Anthropic only persists a cache entry at the LAST
    cache_control breakpoint in the request. Earlier cache_control markers
    don't create their own durable entries. If the last breakpoint sits
    on a per-turn block, every turn invalidates the cache. We observed
    this empirically in Phase 4 — see PLAN.md "Errors & Learnings" Phase 4.
    """
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
            json=fake_haiku_response(
                text_xml='<agent name="Nadia">ok.</agent>',
                tool_uses=_valid_tool_uses(colony),
            ),
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
    # System has exactly 2 blocks: preamble + personas, both cached.
    assert len(system_blocks) == 2, (
        f"expected 2 system blocks (preamble, personas), got {len(system_blocks)}"
    )
    # Both system blocks have ephemeral cache_control — crucially, the LAST one
    # (personas) must, because that's the only block that actually persists
    # a cache entry Anthropic-side.
    for i, block in enumerate(system_blocks):
        assert block.get("cache_control") == {"type": "ephemeral"}, (
            f"system block {i} missing ephemeral cache_control"
        )
    # Colony state content must flow through the user message, not system.
    user_content = sent_body["messages"][0]["content"]
    assert "<colony_state>" in user_content, (
        "colony_state rendering must live in the user message so the last "
        "system cache_control (on personas) points at run-stable content"
    )


# ---------------------------------------------------------------------------
# agent_debate retry on < 3 valid proposals (call_fast mocked)
# ---------------------------------------------------------------------------

_FLAVORS = ("DOMINANT", "MORAL", "WILDCARD")


def _proposal_tool_use(i: int, caller: Agent, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": f"Plan {i}",
        "description": f"Option {i}.",
        "flavor": _FLAVORS[i % 3],
        "effects_preview": ["-5 fuel"],
        "tool_calls_on_pick": [
            {
                "name": "modify_resource",
                "args": {"resource_id": "fuel", "delta": -5, "reason": "burn"},
            }
        ],
        "caller_agent_id": str(caller.id),
    }
    payload.update(overrides)
    return {"name": "propose_action", "input": payload}


def _debate_message(
    colony: ColonyState, tool_uses: list[dict[str, Any]], reaction: str = "ok."
) -> Any:
    from anthropic.types import Message

    xml = f'<agent name="{colony.agents[0].name}">{reaction}</agent>'
    return Message.model_validate(fake_haiku_response(text_xml=xml, tool_uses=tool_uses))


def _valid_tool_uses(colony: ColonyState, n: int = 3) -> list[dict[str, Any]]:
    return [_proposal_tool_use(i, colony.agents[i % len(colony.agents)]) for i in range(n)]


class _FakeCallFast:
    """Stands in for `nodes.call_fast`; returns queued responses in order."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self, system_blocks: list, user_message: str, tools: Any = None, max_tokens: int = 2000
    ) -> Any:
        self.calls.append(
            {"system": system_blocks, "user": user_message, "tools": tools, "max": max_tokens}
        )
        return self._responses.pop(0)


def _debate_state(colony: ColonyState, cost: float = 0.0) -> dict[str, Any]:
    event = Event(
        id="evt_storm",
        category="weather",
        title="Ice storm",
        body="Wind.",
        severity=2,
        origin=EventOrigin.NATURAL,
    )
    return {"colony_state": colony, "current_event": event, "cost_cents_this_turn": cost}


def _install_fake(monkeypatch: pytest.MonkeyPatch, responses: list[Any]) -> _FakeCallFast:
    from app.agent import nodes

    fake = _FakeCallFast(responses)
    monkeypatch.setattr(nodes, "call_fast", fake)
    return fake


async def test_agent_debate_no_retry_when_three_valid(monkeypatch):
    colony = make_colony_state()
    first = _debate_message(colony, _valid_tool_uses(colony))
    fake = _install_fake(monkeypatch, [first])

    result = await agent_debate(_debate_state(colony))

    assert len(fake.calls) == 1
    assert [tc.args["name"] for tc in result["debate_tool_calls"]] == ["Plan 0", "Plan 1", "Plan 2"]
    assert result["cost_cents_this_turn"] == pytest.approx(cost_cents_of(first, MODEL_HAIKU))
    assert result["turn_meta"]["debate_retried"] is False


async def test_agent_debate_retries_once_and_uses_better_retry(monkeypatch):
    colony = make_colony_state()
    first = _debate_message(colony, [], reaction="first")
    second = _debate_message(colony, _valid_tool_uses(colony), reaction="second")
    fake = _install_fake(monkeypatch, [first, second])

    result = await agent_debate(_debate_state(colony, cost=1.0))

    assert len(fake.calls) == 2
    assert len(result["debate_tool_calls"]) == 3
    assert result["debate_per_agent_text"][colony.agents[0].id] == "second"
    assert result["turn_meta"]["debate_retried"] is True
    expected = 1.0 + cost_cents_of(first, MODEL_HAIKU) + cost_cents_of(second, MODEL_HAIKU)
    assert result["cost_cents_this_turn"] == pytest.approx(expected)


async def test_agent_debate_keeps_first_when_retry_is_not_better(monkeypatch):
    colony = make_colony_state()
    first = _debate_message(colony, _valid_tool_uses(colony, n=1), reaction="first")
    second = _debate_message(colony, [], reaction="second")
    fake = _install_fake(monkeypatch, [first, second])

    result = await agent_debate(_debate_state(colony))

    assert len(fake.calls) == 2
    assert [tc.args["name"] for tc in result["debate_tool_calls"]] == ["Plan 0"]
    assert result["debate_per_agent_text"][colony.agents[0].id] == "first"
    expected = cost_cents_of(first, MODEL_HAIKU) + cost_cents_of(second, MODEL_HAIKU)
    assert result["cost_cents_this_turn"] == pytest.approx(expected)


async def test_agent_debate_no_retry_when_turn_budget_spent(monkeypatch):
    colony = make_colony_state()
    first = _debate_message(colony, [])
    fake = _install_fake(monkeypatch, [first])

    result = await agent_debate(_debate_state(colony, cost=float(MAX_COST_CENTS_PER_TURN)))

    assert len(fake.calls) == 1
    assert result["debate_tool_calls"] == []


async def test_agent_debate_counts_only_validator_accepted_proposals(monkeypatch):
    """Empty name, empty input and a disallowed nested tool are all rejected
    by the validator, so 3 propose_action blocks with 1 valid still retry."""
    colony = make_colony_state()
    nadia = colony.agents[0]
    bad_nested = [{"name": "initiate_coup", "args": {}}]
    first = _debate_message(
        colony,
        [
            _proposal_tool_use(0, nadia),
            _proposal_tool_use(1, nadia, name=""),
            {"name": "propose_action", "input": {}},
            _proposal_tool_use(2, nadia, tool_calls_on_pick=bad_nested),
        ],
    )
    second = _debate_message(colony, _valid_tool_uses(colony))
    fake = _install_fake(monkeypatch, [first, second])

    result = await agent_debate(_debate_state(colony))

    assert len(fake.calls) == 2
    assert len(result["debate_tool_calls"]) == 3


async def test_agent_debate_retry_keeps_cached_prefix(monkeypatch):
    """The retry may only change the user message: system blocks and tools
    must be identical so the cached prefix still hits."""
    colony = make_colony_state()
    fake = _install_fake(
        monkeypatch,
        [_debate_message(colony, []), _debate_message(colony, _valid_tool_uses(colony))],
    )

    await agent_debate(_debate_state(colony))

    first, retry = fake.calls
    assert retry["system"] == first["system"]
    assert retry["tools"] == first["tools"]
    assert retry["user"].startswith(first["user"])
    assert "propose_action" in retry["user"][len(first["user"]) :]


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


@respx.mock
async def test_propose_pick_resolve_pipeline_mutates_resource():
    """End-to-end (mocked Haiku): debate emits a propose_action whose
    tool_calls_on_pick carries a modify_resource; player picks it; resource
    moves. Guards the headline 'frozen dashboard' regression from the
    2026-04-30 playtest.
    """
    nadia = make_alive_agent(name="Nadia")
    colony = make_colony_state(agents=[nadia])
    nadia = colony.agents[0]
    fuel_before = colony.resources["fuel"].value

    event = Event(
        id="evt_storm",
        category="weather",
        title="Ice storm",
        body="The wind is up.",
        severity=2,
        origin=EventOrigin.NATURAL,
    )

    # Mock Haiku to emit a single propose_action with nested tool_calls_on_pick.
    respx.post(_ANTHROPIC_URL).mock(
        return_value=Response(
            200,
            json=fake_haiku_response(
                text_xml=f'<agent name="{nadia.name}">Burn it now.</agent>',
                tool_uses=[
                    {
                        "name": "propose_action",
                        "input": {
                            "name": "Burn fuel for warmth",
                            "description": "Run the heater hard.",
                            "flavor": "DOMINANT",
                            "effects_preview": ["-10 fuel"],
                            "tool_calls_on_pick": [
                                {
                                    "name": "modify_resource",
                                    "args": {
                                        "resource_id": "fuel",
                                        "delta": -10,
                                        "reason": "heater run",
                                    },
                                }
                            ],
                            "caller_agent_id": str(nadia.id),
                        },
                    }
                ],
            ),
        )
    )

    # 1. agent_debate parses the response into debate_tool_calls.
    debate_state: dict[str, Any] = {
        "colony_state": colony,
        "current_event": event,
        "cost_cents_this_turn": 0.0,
    }
    debated = await agent_debate(debate_state)
    assert len(debated["debate_tool_calls"]) == 1

    # 2. apply_tool_calls folds them through the engine; proposal carries
    #    its nested tool_calls_on_pick.
    apply_state: dict[str, Any] = {
        "colony_state": colony,
        "debate_tool_calls": debated["debate_tool_calls"],
    }
    applied = await apply_tool_calls(apply_state)
    assert len(applied["proposals"]) == 1
    proposal = applied["proposals"][0]
    assert len(proposal.tool_calls_on_pick) == 1
    assert proposal.tool_calls_on_pick[0].name == "modify_resource"
    # Fuel is unchanged at proposal stage — the nested call has not fired yet.
    assert applied["colony_state"].resources["fuel"].value == fuel_before

    # 3. resolve picks this proposal and applies its nested calls.
    resolve_state: dict[str, Any] = {
        "colony_state": applied["colony_state"],
        "proposals": applied["proposals"],
        "player_choice": proposal.id,
        "current_event": event,
    }
    resolved = await resolve(resolve_state)
    assert resolved["colony_state"].resources["fuel"].value == fuel_before - 10


async def test_apply_tool_calls_separates_applied_from_rejected():
    colony = make_colony_state()
    good = ToolCall(
        name="propose_action",
        caller_id=colony.agents[0].id,
        args={
            "name": "Patch",
            "description": "Patch it.",
            "flavor": "DOMINANT",
            "tool_calls_on_pick": [
                {
                    "name": "modify_resource",
                    "args": {"resource_id": "fuel", "delta": -5, "reason": "patching"},
                }
            ],
        },
    )
    bad = ToolCall(
        name="propose_action",
        caller_id=colony.agents[1].id,
        args={"name": "Nope", "flavor": "MORAL"},
    )
    result = await apply_tool_calls({"colony_state": colony, "debate_tool_calls": [good, bad]})
    assert [tc.args["name"] for tc in result["applied_tool_calls"]] == ["Patch"]
    assert [tc.rejection_reason for tc in result["rejected_tool_calls"]] == ["missing_description"]


async def test_apply_node_rejects_ambient_debate_calls():
    colony = make_colony_state()
    ambient = ToolCall(
        name="modify_resource",
        caller_id=colony.agents[0].id,
        args={"resource_id": "fuel", "delta": -5, "reason": "storm"},
    )
    result = await apply_tool_calls({"colony_state": colony, "debate_tool_calls": [ambient]})
    assert result["colony_state"].resources["fuel"].value == 70
    rejected = result["rejected_tool_calls"]
    assert [(tc.phase, tc.rejection_reason) for tc in rejected] == [
        ("debate", "not_allowed_in_debate")
    ]


def _patch_call(colony: ColonyState, **extra: Any) -> ToolCall:
    return ToolCall(
        name="propose_action",
        caller_id=colony.agents[0].id,
        args={
            "name": "Patch",
            "description": "Patch it.",
            "flavor": "DOMINANT",
            "tool_calls_on_pick": [
                {
                    "name": "modify_resource",
                    "args": {"resource_id": "fuel", "delta": -5, "reason": "p"},
                }
            ],
            **extra,
        },
    )


async def test_apply_node_renders_preview_and_ignores_llm_preview():
    colony = make_colony_state()
    call = _patch_call(colony, effects_preview=["+99 legitimacy"])
    result = await apply_tool_calls({"colony_state": colony, "debate_tool_calls": [call]})
    assert result["proposals"][0].effects_preview == ["fuel \u22125"]
    assert result["colony_state"].turn_proposals[0].effects_preview == ["fuel \u22125"]


async def test_apply_node_writes_turn_meta():
    colony = make_colony_state()
    result = await apply_tool_calls(
        {
            "colony_state": colony,
            "debate_tool_calls": [_patch_call(colony)],
            "turn_meta": {"debate_retried": False},
        }
    )
    meta = result["turn_meta"]
    assert meta["debate_retried"] is False
    assert meta["proposal_classes"] == ["valid"]
    assert meta["risk_notes_dropped"] == 0
    assert meta["pre_pick"]["legitimacy"] == colony.legitimacy


async def test_resolve_stamps_resolve_phase():
    colony = make_colony_state()
    applied = await apply_tool_calls(
        {"colony_state": colony, "debate_tool_calls": [_patch_call(colony)]}
    )
    before = applied["colony_state"]
    out = await resolve(
        {
            "colony_state": before,
            "proposals": applied["proposals"],
            "player_choice": applied["proposals"][0].id,
            "current_event": _make_event(severity=1),
        }
    )
    new_calls = out["colony_state"].turn_tool_calls[len(before.turn_tool_calls) :]
    assert new_calls and all(tc.phase == "resolve" for tc in new_calls)


async def test_autonomous_stamps_autonomy_phase(monkeypatch):
    colony = make_colony_state()
    remember = {
        "name": "remember",
        "input": {"fact": "The pump held.", "caller_agent_id": str(colony.agents[0].id)},
    }
    _install_fake(monkeypatch, [_debate_message(colony, [remember])])
    out = await autonomous_actions(
        {"colony_state": colony, "narration": "x", "cost_cents_this_turn": 0.0}
    )
    assert out["colony_state"].turn_tool_calls[-1].phase == "autonomy"


async def test_retry_ignores_discarded_first_attempt_for_duplicate_names(monkeypatch):
    """The retry is judged on the pre-debate state, so a discarded first
    attempt's proposals never trip the duplicate-name check."""
    colony = make_colony_state()
    first = _debate_message(colony, _valid_tool_uses(colony, n=1), reaction="first")
    second = _debate_message(colony, _valid_tool_uses(colony), reaction="second")
    fake = _install_fake(monkeypatch, [first, second])

    result = await agent_debate(_debate_state(colony))

    assert len(fake.calls) == 2
    assert [tc.args["name"] for tc in result["debate_tool_calls"]] == ["Plan 0", "Plan 1", "Plan 2"]
    assert result["turn_meta"]["debate_retried"] is True


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


async def test_resolve_uses_payload_driven_consequences_not_flavor_label():
    """A WILDCARD with no real effects costs 0 legitimacy; a WILDCARD with
    severe-health payload craters legitimacy ≥10. Confirms Pb fix —
    consequence is derived from picked.tool_calls_on_pick, not the label."""
    nadia = make_alive_agent(name="Nadia", quadrant=Quadrant.ACCOUNTANT)
    marko = make_alive_agent(name="Marko", quadrant=Quadrant.OPERATOR)
    irina = make_alive_agent(name="Irina", quadrant=Quadrant.CONSCIENCE)
    viktor = make_alive_agent(name="Viktor", quadrant=Quadrant.CRUSADER)
    colony = make_colony_state(turn=2, agents=[nadia, marko, irina, viktor])
    base_legit = colony.legitimacy
    event = _make_event(severity=2)  # no consequence seed

    # Benign WILDCARD: empty payload, single-proposal triple = no overrule.
    benign = _make_proposal(
        pid="benign",
        flavor=ProposalFlavor.WILDCARD,
        proposer_id=nadia.id,
        tool_calls_on_pick=[],
    )
    benign_state: dict[str, Any] = {
        "colony_state": colony,
        "proposals": [benign],
        "player_choice": "benign",
        "current_event": event,
    }
    r = await resolve(benign_state)
    assert r["colony_state"].legitimacy == base_legit, (
        f"benign WILDCARD should not move legitimacy, got delta {base_legit - r['colony_state'].legitimacy}"
    )

    # Severe WILDCARD: kills an advisor. Should crater legitimacy ≥10.
    severe = _make_proposal(
        pid="severe",
        flavor=ProposalFlavor.WILDCARD,
        proposer_id=nadia.id,
        tool_calls_on_pick=[
            ToolCall(
                name="modify_health",
                caller_id=None,
                args={"target_id": str(marko.id), "delta": -100, "reason": "ice"},
            )
        ],
    )
    severe_state: dict[str, Any] = {
        "colony_state": colony,
        "proposals": [severe],
        "player_choice": "severe",
        "current_event": event,
    }
    r = await resolve(severe_state)
    drop = base_legit - r["colony_state"].legitimacy
    assert drop >= 10, f"severe-health WILDCARD should crater legitimacy ≥10, got drop {drop}"


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
async def test_autonomous_actions_sends_full_tool_list_for_caching():
    """Autonomous now ships the FULL tool list to keep the cached prefix
    above Haiku 4.5's 4096-token minimum (a smaller subset dropped below
    threshold and silently disabled caching). Off-list tools the model
    might emit are dropped runtime-side; see the next test.
    """
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
    # Full list — prefix-size matches debate's, so caching shares an entry.
    assert sent_tool_names == {t["name"] for t in AGENT_TOOL_SCHEMAS}
    # Sanity: AUTONOMOUS_TOOL_NAMES is still a strict subset (it's the
    # runtime filter, not the schema payload).
    assert sent_tool_names >= AUTONOMOUS_TOOL_NAMES


@respx.mock
async def test_autonomous_actions_drops_debate_only_tool_calls():
    """Runtime safety net: even if the model emits a propose_action or
    modify_resource off-turn (now possible because we send the full tool
    list), the node must drop it before reaching the engine.

    Build a fake response with mixed tool calls and assert the resulting
    colony_state shows only the autonomous-whitelist effects.
    """
    nadia = make_alive_agent(name="Nadia", quadrant=Quadrant.ACCOUNTANT)
    colony = make_colony_state(agents=[nadia])
    nadia_id = str(nadia.id)

    # Two debate-only tool calls (must be dropped) + one introspective
    # call (must apply).
    debate_only_modify = {
        "id": "tu_drop_1",
        "name": "modify_resource",
        "input": {
            "resource_id": "fuel",
            "delta": -10,
            "reason": "off-turn raid",
            "caller_agent_id": nadia_id,
        },
    }
    debate_only_propose = {
        "id": "tu_drop_2",
        "name": "propose_action",
        "input": {
            "name": "Burn the records",
            "description": "Off-turn proposal that should never reach the engine.",
            "flavor": "WILDCARD",
            "caller_agent_id": nadia_id,
        },
    }
    introspective_remember = {
        "id": "tu_keep",
        "name": "remember",
        "input": {
            "fact": "Player overruled me on the samples.",
            "caller_agent_id": nadia_id,
        },
    }

    respx.post(_ANTHROPIC_URL).mock(
        return_value=Response(
            200,
            json=fake_haiku_response(
                text_xml="",
                tool_uses=[debate_only_modify, debate_only_propose, introspective_remember],
            ),
        )
    )

    state: dict[str, Any] = {
        "colony_state": colony,
        "cost_cents_this_turn": 0.0,
    }
    result = await autonomous_actions(state)

    # The dropped calls never reach the engine — they don't appear in
    # turn_tool_calls (rejected or applied) on the new colony state.
    new_colony: ColonyState = result["colony_state"]
    logged_names = {tc.name for tc in new_colony.turn_tool_calls}
    assert "modify_resource" not in logged_names
    assert "propose_action" not in logged_names
    # And the introspective call DID apply: Nadia's memory grew by one.
    nadia_post = next(a for a in new_colony.agents if a.id == nadia.id)
    assert any("Player overruled me" in m for m in nadia_post.memory)


@respx.mock
async def test_autonomous_actions_summary_strips_turn_counter_prose():
    """Regression for §1.7 4th-wall leak: the synthesized summary that
    `autonomous_actions` feeds to advisors must NOT include "Turn N"
    prose. Advisors otherwise echo the index back into their `remember`
    calls, which then re-surface in the next turn's persona context.

    The structured `turn_number: N / M` line inside `<colony_state>` is
    expected and required for engine-level pacing; this test isolates
    the synthesized prose summary by stripping that one keyed line
    before scanning.
    """
    # No prior narration → autonomous_actions synthesizes a summary from
    # the most recent ResolvedEvent. Build one to force that path.
    nadia = make_alive_agent(name="Nadia", quadrant=Quadrant.ACCOUNTANT)
    colony = make_colony_state(agents=[nadia], turn=5)
    last_event = Event(
        id="evt_storm",
        category="weather",
        title="Ice storm at first light",
        body="Visibility drops to three meters; the heating core hums.",
        severity=3,
        origin=EventOrigin.NATURAL,
    )
    resolved = ResolvedEvent(
        turn_number=5,
        event=last_event,
        chosen_proposal_id="p_a",
        summary="Patch the leak and accept the temperature drop.",
    )
    colony = colony.model_copy(update={"event_log": [resolved]})

    route = respx.post(_ANTHROPIC_URL).mock(
        return_value=Response(200, json=fake_haiku_response(text_xml=""))
    )

    state: dict[str, Any] = {
        "colony_state": colony,
        "cost_cents_this_turn": 0.0,
    }
    await autonomous_actions(state)

    sent_body = json.loads(route.calls.last.request.content.decode("utf-8"))
    user_content = sent_body["messages"][0]["content"]

    # Strip the structured `turn_number: N / M` line (expected and
    # legitimate inside <colony_state>) before scanning.
    stripped = re.sub(r"turn_number:\s*\d+\s*/\s*\d+", "", user_content)

    matches = re.findall(r"\bturn\s+\d+\b", stripped, re.IGNORECASE)
    assert not matches, (
        f"autonomous_actions user message must not narrate 'turn N'; "
        f"matched: {matches}.\nFull user message:\n{user_content}"
    )


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
