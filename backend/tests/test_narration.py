"""Narration prompt builders and fallback (spec §4.2)."""

from __future__ import annotations

import re
from uuid import UUID

from app.agent import narration
from app.agent.narration import (
    build_narration_system_block,
    build_narration_user_message,
    fallback_line,
)
from app.game.generator import generate_run
from app.game.schemas import Event, EventOrigin, Proposal, ProposalFlavor
from app.game.setting_loader import list_available_settings, load_setting

DESERT = load_setting("desert_outpost_v1")
COLONY = generate_run(
    user_id=UUID("bbbbbbbb-0000-0000-0000-000000000002"), setting_id="desert_outpost_v1", seed=3
)
PICKED = Proposal(
    id="p",
    proposed_by_agent_id=COLONY.agents[0].id,
    flavor=ProposalFlavor.MORAL,
    name="Share the water",
    description="Open the tank to the caravan.",
    effects_preview=["water −10", "legitimacy −2 (overrules Cal)"],
    tool_calls_on_pick=[],
)
EVENT = Event(
    id="e",
    category="c",
    title="Caravan wants water",
    body="They wait by the pump.",
    severity=2,
    origin=EventOrigin.NATURAL,
)


def test_system_block_is_setting_specific_and_cached() -> None:
    block = build_narration_system_block(DESERT, COLONY.agents)
    assert block["cache_control"] == {"type": "ephemeral"}
    text = block["text"]
    assert DESERT.name in text and "Warden" in text
    for agent in COLONY.agents:
        assert f"{agent.name}, {agent.role}, alive" in text
    assert "<agent" not in text


def test_system_block_prose_names_no_name_pool_name() -> None:
    names = {
        n
        for sid in list_available_settings()
        for a in load_setting(sid).advisor_archetypes
        for n in a.name_pool
    }
    for sid in list_available_settings():
        setting = load_setting(sid)
        # Empty roster: only the static prose and setting text remain.
        text = build_narration_system_block(setting, [])["text"]
        for name in names:
            assert not re.search(rf"\b{re.escape(name)}\b", text), (sid, name)
    for const in (narration.RETRY_NOTE,):
        for name in names:
            assert not re.search(rf"\b{re.escape(name)}\b", const), name


def test_user_message_carries_full_event_choice_and_effects() -> None:
    msg = build_narration_user_message(EVENT, PICKED, COLONY.agents[0].name, ["Cal"])
    assert "<title>Caravan wants water</title>" in msg
    assert "<body>They wait by the pump.</body>" in msg
    assert (
        f'<choice proposer="{COLONY.agents[0].name}" flavor="MORAL">'
        "Share the water: Open the tank to the caravan.</choice>"
    ) in msg
    assert "<effects>water −10; legitimacy −2 (overrules Cal)</effects>" in msg
    assert "<dead>Cal</dead>" in msg


def test_fallback_lines() -> None:
    assert fallback_line(PICKED, "Sol") == "You backed Sol: Share the water."
    assert fallback_line(PICKED, None) == "You chose: Share the water."
    assert fallback_line(None, None) == "The day closes."
