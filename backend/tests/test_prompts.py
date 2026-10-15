"""Unit tests for `app.agent.prompts`.

Covers:
- Cache breakpoints on the three system blocks.
- Personas block excludes per-turn volatile state (memory, grievance, etc.).
- Colony state block includes memories and dead agents with is_alive="false".
- Debate response parser: per-agent text extraction, tool_use → ToolCall
  round-trip (caller_agent_id stripped from args), tolerance of missing
  caller_agent_id, unknown agent names, and malformed XML.
- Persona blocks use suggestive, not imperative, framing for verbal tics
  (regression for §1.8 advisor tic ossification).
- Prose user messages never narrate "turn N" — turn indices stay structured
  in `<colony_state>` only (regression for §1.7 4th-wall leaks).
"""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from app.agent import prompts
from app.agent.prompts import (
    DebateParse,
    build_autonomous_user_message,
    build_colony_state_block,
    build_debate_user_message,
    build_personas_block,
    build_preamble_block,
    parse_debate_response,
)
from app.game.schemas import (
    Agent,
    ColonyState,
    Event,
    EventOrigin,
    Quadrant,
    Resource,
    ToolCall,
)
from app.game.setting_loader import list_available_settings, load_setting

# Matches "turn N" / "Turn N" / "TURN N" in prose (case-insensitive) — the
# 4th-wall leak signature. Structured state lines like
# `turn_number: 5 / 12` are allowed elsewhere; this regex deliberately uses
# a literal space to skip those.
_TURN_COUNTER_PROSE_RE = re.compile(r"\bturn\s+\d+\b", re.IGNORECASE)

_ALL_SETTING_IDS = ("arctic_station_v1", "orbital_colony_v1", "desert_outpost_v1")

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def make_agent(
    *,
    name: str = "Nadia",
    role: str = "Quartermaster",
    quadrant: Quadrant = Quadrant.ACCOUNTANT,
    memory: list[str] | None = None,
    relationships: dict[UUID, int] | None = None,
    is_alive: bool = True,
    agent_id: UUID | None = None,
    game_id: UUID | None = None,
    verbal_tic: str = "cold numbers",
) -> Agent:
    return Agent(
        id=agent_id or uuid4(),
        game_id=game_id or uuid4(),
        name=name,
        role=role,
        archetype_quadrant=quadrant,
        ethics=-0.3,
        temperament=0.2,
        loyalty=0.5,
        portrait_seed=name.lower(),
        health=100,
        morale=60,
        grievance=3,
        is_alive=is_alive,
        memory=memory or [],
        relationships=relationships or {},
        verbal_tic=verbal_tic,
    )


def make_colony_state(agents: list[Agent], game_id: UUID | None = None) -> ColonyState:
    gid = game_id or (agents[0].game_id if agents else uuid4())
    return ColonyState(
        game_id=gid,
        user_id=uuid4(),
        setting_id="arctic_station_v1",
        seed=42,
        turn_number=3,
        max_turns=10,
        resources={
            "fuel": Resource(
                id="fuel", name="Fuel", icon="⛽", value=42, max=100, critical_threshold=15
            ),
            "food": Resource(
                id="food", name="Food", icon="🥫", value=80, max=100, critical_threshold=20
            ),
        },
        agents=agents,
        legitimacy=70,
        player_health=90,
        consequence_seeds=[],
        event_log=[],
        current_event=None,
        turn_proposals=[],
        turn_tool_calls=[],
    )


def _response(content_blocks: list[Any]) -> MagicMock:
    r = MagicMock()
    r.content = content_blocks
    return r


def _text_block(text: str) -> MagicMock:
    b = MagicMock()
    b.type = "text"
    b.text = text
    return b


def _tool_use_block(
    *,
    name: str,
    input_: dict[str, Any],
    block_id: str = "toolu_abc",
) -> MagicMock:
    b = MagicMock()
    b.type = "tool_use"
    b.name = name
    b.id = block_id
    b.input = input_
    return b


# ---------------------------------------------------------------------------
# Cache breakpoint assertions
# ---------------------------------------------------------------------------


def test_preamble_block_is_cached():
    block = build_preamble_block()
    assert block["type"] == "text"
    assert block["cache_control"] == {"type": "ephemeral"}
    assert block["text"]  # non-empty


def test_personas_block_is_cached():
    agents = [make_agent(name="Nadia"), make_agent(name="Marko", quadrant=Quadrant.OPERATOR)]
    block = build_personas_block(agents)
    assert block["type"] == "text"
    assert block["cache_control"] == {"type": "ephemeral"}
    assert "Nadia" in block["text"] and "Marko" in block["text"]


def test_personas_block_excludes_memories():
    agent = make_agent(name="Nadia", memory=["alpha", "beta"])
    block = build_personas_block([agent])
    assert "alpha" not in block["text"]
    assert "beta" not in block["text"]
    # Also exclude volatile per-turn state.
    assert "grievance" not in block["text"]
    assert "morale" not in block["text"]


def test_colony_state_block_is_cached():
    agent = make_agent()
    state = make_colony_state([agent])
    block = build_colony_state_block(state)
    assert block["type"] == "text"
    assert block["cache_control"] == {"type": "ephemeral"}


def test_colony_state_block_includes_memories():
    agent = make_agent(name="Nadia", memory=["alpha", "beta"])
    state = make_colony_state([agent])
    block = build_colony_state_block(state)
    assert "alpha" in block["text"]
    assert "beta" in block["text"]


def test_colony_state_block_includes_dead_agents():
    alive = make_agent(name="Nadia", is_alive=True)
    dead = make_agent(name="Marko", is_alive=False, quadrant=Quadrant.OPERATOR)
    state = make_colony_state([alive, dead])
    block = build_colony_state_block(state)
    text = block["text"]
    assert "Marko" in text
    assert str(dead.id) in text
    assert 'is_alive="false"' in text
    assert 'is_alive="true"' in text


# ---------------------------------------------------------------------------
# User message shape (smoke tests, not in the required 11 but cheap)
# ---------------------------------------------------------------------------


def test_debate_user_message_contains_event_body():
    event = Event(
        id="evt_storm",
        category="weather",
        title="Ice storm",
        body="Winds are peeling the north wall.",
        severity=3,
        origin=EventOrigin.NATURAL,
    )
    msg = build_debate_user_message(event)
    assert "Ice storm" in msg
    assert "Winds are peeling" in msg
    assert "caller_agent_id" in msg


def test_autonomous_user_message_mentions_introspection():
    msg = build_autonomous_user_message("The player conceded to the miners.")
    assert "conceded to the miners" in msg
    assert "caller_agent_id" in msg


# ---------------------------------------------------------------------------
# Debate response parser
# ---------------------------------------------------------------------------


def test_parse_debate_extracts_per_agent_text():
    nadia = make_agent(name="Nadia")
    marko = make_agent(name="Marko", quadrant=Quadrant.OPERATOR)
    resp = _response(
        [_text_block('<agent name="Nadia">She nods.</agent><agent name="Marko">He scoffs.</agent>')]
    )
    parsed = parse_debate_response(resp, [nadia, marko])
    assert isinstance(parsed, DebateParse)
    assert parsed.per_agent_text[nadia.id] == "She nods."
    assert parsed.per_agent_text[marko.id] == "He scoffs."


def test_parse_debate_extracts_tool_calls():
    nadia = make_agent(name="Nadia")
    resp = _response(
        [
            _tool_use_block(
                name="modify_resource",
                input_={
                    "resource_id": "fuel",
                    "delta": -10,
                    "reason": "Patching the north wall burns reserve.",
                    "caller_agent_id": str(nadia.id),
                },
            )
        ]
    )
    parsed = parse_debate_response(resp, [nadia])
    assert len(parsed.tool_calls) == 1
    tc = parsed.tool_calls[0]
    assert isinstance(tc, ToolCall)
    assert tc.name == "modify_resource"
    assert tc.caller_id == nadia.id
    assert tc.args == {
        "resource_id": "fuel",
        "delta": -10,
        "reason": "Patching the north wall burns reserve.",
    }
    assert "caller_agent_id" not in tc.args


def test_parse_debate_tool_call_without_caller_agent_id():
    nadia = make_agent(name="Nadia")
    resp = _response(
        [
            _tool_use_block(
                name="modify_resource",
                input_={"resource_id": "fuel", "delta": -5, "reason": "leak"},
            )
        ]
    )
    parsed = parse_debate_response(resp, [nadia])
    assert len(parsed.tool_calls) == 1
    assert parsed.tool_calls[0].caller_id is None


def test_parse_debate_unknown_agent_name_skipped():
    nadia = make_agent(name="Nadia")
    resp = _response(
        [_text_block('<agent name="Nadia">She nods.</agent><agent name="Unknown">foo</agent>')]
    )
    parsed = parse_debate_response(resp, [nadia])
    assert parsed.per_agent_text == {nadia.id: "She nods."}


def test_parse_debate_malformed_xml_returns_partial():
    nadia = make_agent(name="Nadia")
    marko = make_agent(name="Marko", quadrant=Quadrant.OPERATOR)
    # Marko's tag is missing its closing </agent> — the regex should skip it
    # without raising and still return Nadia's reaction.
    resp = _response(
        [_text_block('<agent name="Nadia">She nods.</agent><agent name="Marko">He scoffs.')]
    )
    parsed = parse_debate_response(resp, [nadia, marko])
    assert parsed.per_agent_text == {nadia.id: "She nods."}


# ---------------------------------------------------------------------------
# 4th-wall leak regression — turn-counter language must not appear in prose
# user messages or in agent-state rendering. Structured `turn_number: N / M`
# inside <colony_state> is allowed (and required) for engine-level pacing,
# but the prose builders that feed the LLM should reference past events by
# what happened, not by index. See bug report §1.7.
# ---------------------------------------------------------------------------


def test_autonomous_user_message_has_no_turn_counter_prose():
    """The autonomous prose summary must not narrate "turn N"."""
    summary = "The colony chose 'Patch the leak' for Fuel leak."
    msg = build_autonomous_user_message(summary)
    matches = _TURN_COUNTER_PROSE_RE.findall(msg)
    assert not matches, (
        f"build_autonomous_user_message must not include 'turn N' prose; "
        f"matched: {matches} in:\n{msg}"
    )


def test_debate_user_message_has_no_turn_counter_prose():
    """Debate user message wraps the event but must not narrate 'turn N'."""
    event = Event(
        id="evt_storm",
        category="weather",
        title="Ice storm at first light",
        body="Visibility drops to three meters; the heating core hums.",
        severity=3,
        origin=EventOrigin.NATURAL,
    )
    msg = build_debate_user_message(event)
    matches = _TURN_COUNTER_PROSE_RE.findall(msg)
    assert not matches, (
        f"build_debate_user_message must not include 'turn N' prose; matched: {matches} in:\n{msg}"
    )


def test_colony_state_block_keeps_structured_turn_number_only():
    """`<colony_state>` carries `turn_number: N / M` (structured, allowed)
    but no free-form 'turn N' phrase outside that one keyed line."""
    agent = make_agent()
    state = make_colony_state([agent])
    text = build_colony_state_block(state)["text"]

    # The structured field must be present.
    assert f"turn_number: {state.turn_number} / {state.max_turns}" in text

    # Strip the structured field, then assert no 'turn N' prose remains.
    stripped = re.sub(r"turn_number:\s*\d+\s*/\s*\d+", "", text)
    matches = _TURN_COUNTER_PROSE_RE.findall(stripped)
    assert not matches, (
        f"colony_state block must keep 'turn N' content inside the "
        f"structured turn_number line only; leaked: {matches}"
    )


# ---------------------------------------------------------------------------
# Verbal-tic de-ossification regression — the persona block rendered from
# every shipping setting YAML must use suggestive, not imperative, phrasing
# for advisor verbal tics. The LLM reads imperative phrasings ("Prefaces",
# "Hedges", "Frames every situation") as per-turn rules; suggestive
# phrasings ("Sometimes …") read as flavor cues. See bug report §1.8.
# ---------------------------------------------------------------------------


# Imperatives the report calls out as worst offenders, plus other
# always-do-X verbs that read as ossifying. Match case-insensitively
# at the start of the verbal_tic value.
_IMPERATIVE_TIC_PATTERNS = (
    re.compile(r"^prefaces\b", re.IGNORECASE),
    re.compile(r"^hedges\b", re.IGNORECASE),
    re.compile(r"^frames every\b", re.IGNORECASE),
    re.compile(r"^refers to everyone\b", re.IGNORECASE),
    re.compile(r"^ends most sentences\b", re.IGNORECASE),
    re.compile(r"^quotes (?:scripture|prices) (?:from|fragments)\b", re.IGNORECASE),
    re.compile(r"^uses garden-bay metaphors\b", re.IGNORECASE),
    re.compile(r"^references the station logbook\b", re.IGNORECASE),
    re.compile(r"^speaks in orders\b", re.IGNORECASE),
)


@pytest.mark.parametrize("setting_id", _ALL_SETTING_IDS)
def test_setting_yaml_loads_cleanly(setting_id: str):
    """Every shipping setting YAML must load + validate after the tic edits.

    Catches accidental schema breakage (e.g. a multi-sentence verbal_tic
    or a >200-char value) before the persona-block test runs.
    """
    setting = load_setting(setting_id)
    assert len(setting.advisor_archetypes) == 4


@pytest.mark.parametrize("setting_id", _ALL_SETTING_IDS)
def test_setting_verbal_tics_use_suggestive_framing(setting_id: str):
    """Each archetype's verbal_tic in the YAML must avoid imperative
    phrasing. Reads as a flavor cue ("Sometimes X"), not a per-turn rule.
    """
    setting = load_setting(setting_id)
    offenders: list[tuple[str, str]] = []
    for arch in setting.advisor_archetypes:
        tic = arch.verbal_tic
        for pat in _IMPERATIVE_TIC_PATTERNS:
            if pat.match(tic):
                offenders.append((arch.role_name, tic))
                break
    assert not offenders, (
        f"setting {setting_id!r} still contains imperative verbal_tic phrasings: {offenders}"
    )


@pytest.mark.parametrize("setting_id", _ALL_SETTING_IDS)
def test_personas_block_does_not_leak_imperative_tic_words(setting_id: str):
    """Render the personas block from real YAML-derived agents and assert
    no imperative tic phrasing reaches the prompt."""
    setting = load_setting(setting_id)
    agents = [
        make_agent(
            name=arch.name_pool[0],
            role=arch.role_name,
            quadrant=arch.quadrant,
            verbal_tic=arch.verbal_tic,
        )
        for arch in setting.advisor_archetypes
    ]
    block = build_personas_block(agents)
    text = block["text"].lower()

    # The bug-report headline phrases must be gone.
    forbidden = (
        "prefaces claims",
        "hedges conclusions with",
        "frames every situation",
        "refers to everyone by last name",
        "ends most sentences with",
    )
    leaks = [phrase for phrase in forbidden if phrase in text]
    assert not leaks, (
        f"persona block for {setting_id!r} still contains imperative phrasings: {leaks}"
    )


def test_preamble_blocks_radio_rumor_when_event_attributes_to_self():
    """The preamble must include the radio-rumor guard so an advisor whose
    own action is named in the event body does not frame their reaction
    as second-hand. See bug report §1.8 (Ilya finding the message)."""
    block = build_preamble_block()
    text = block["text"].lower()
    # Match the load-bearing intent without pinning to exact wording.
    assert "second-hand" in text or "direct knowledge" in text, (
        "preamble must instruct advisors not to frame self-attributed "
        "events as overheard via the radio"
    )


def test_preamble_forbids_turn_number_narration():
    """The preamble must instruct advisors never to narrate turn numbers
    or other scaffolding metadata in prose. See bug report §1.7."""
    block = build_preamble_block()
    text = block["text"].lower()
    assert "turn number" in text, "preamble must explicitly forbid narrating turn numbers in prose"


# ---------------------------------------------------------------------------
# AdvisorStatus rendering and cast filter (P1 plan, Task 6)
# ---------------------------------------------------------------------------


def test_colony_state_block_renders_status_attribute():
    from app.agent.prompts import build_colony_state_block
    from app.game.schemas import AdvisorStatus
    from tests._factories import _agent, _colony

    detained = _agent(name="A", status=AdvisorStatus.DETAINED)
    state = _colony(agents=[detained, _agent(name="B"), _agent(name="C"), _agent(name="D")])

    text = build_colony_state_block(state)["text"]
    assert 'status="DETAINED"' in text
    assert 'status="ACTIVE"' in text  # the other three


def test_cast_filter_includes_only_active_alive_advisors():
    """Helper used by agent_debate / autonomous_actions: alive AND ACTIVE only.

    Detained advisors do not author next turn (acceptance gate P1). Dismissed
    are gone permanently. Dead are gone permanently. Only ACTIVE+alive author.
    """
    from app.agent.nodes import filter_cast
    from app.game.schemas import AdvisorStatus
    from tests._factories import _agent, _colony

    a = _agent(name="A")  # ACTIVE alive — authors
    b = _agent(name="B", status=AdvisorStatus.DETAINED)  # detained — silent
    c = _agent(name="C", status=AdvisorStatus.DISMISSED)  # dismissed — gone
    d = _agent(name="D", alive=False, health=0)  # dead — gone
    state = _colony(agents=[a, b, c, d])

    cast = filter_cast(state.agents)
    names = {ag.name for ag in cast}
    assert names == {"A"}, f"expected ACTIVE alive only, got {names}"


def test_preamble_has_no_llm_preview_contract() -> None:
    text = prompts.build_preamble_block()["text"]
    assert "effects_preview" not in text
    assert '"fuel"' not in text
    assert "legitimacy-positive" not in text
    assert "legitimacy-negative" not in text
    assert "clamps values at 0 and max" not in text
    assert "Standalone modify_resource" not in text
    assert "risk_note" in text


def test_no_prompt_text_contains_a_name_pool_name() -> None:
    names = {
        n
        for sid in list_available_settings()
        for a in load_setting(sid).advisor_archetypes
        for n in a.name_pool
    }
    texts = [v for k, v in vars(prompts).items() if k.isupper() and isinstance(v, str)]
    texts.append(prompts.build_preamble_block()["text"])
    for text in texts:
        for name in names:
            assert not re.search(rf"\b{re.escape(name)}\b", text), name


def test_debate_user_message_drops_preview_instruction() -> None:
    event = Event(id="e", category="c", title="T", body="B", severity=1, origin=EventOrigin.NATURAL)
    message = prompts.build_debate_user_message(event)
    assert "effects_preview" not in message
    assert "no numbers in name or description" in message
