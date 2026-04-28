"""Shared fixtures for playtest harness tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# A fixture turn input for the vertical-slice smoke test. Mirrors the shape
# the orchestrator will assemble from a real /game/{id}/turn SSE stream:
# proposals + advisor reactions + dashboard state.
FIXTURE_TURN_INPUT = {
    "turn": 1,
    "setting_lore": "Vorgrad Station — arctic research outpost reactivated after a "
    "geopolitical crisis. The ice was never supposed to be this political.",
    "dashboard": {
        "resources": {"fuel": 80, "food": 70, "warmth": 65, "medicine": 50, "morale": 60},
        "legitimacy": 50,
        "player_health": 100,
        "advisors": [
            {"id": "a1", "name": "Marko", "role": "Commissar"},
            {"id": "a2", "name": "Nadia", "role": "Quartermaster"},
            {"id": "a3", "name": "Irena", "role": "Medic"},
            {"id": "a4", "name": "Yuri", "role": "Radio Operator"},
        ],
    },
    "event": {
        "title": "Heating coil shows hairline cracks",
        "body": "The east-wing heating coil is on its last week.",
    },
    "proposals": [
        {
            "id": "p_dom",
            "name": "Cut perimeter heating",
            "flavor": "DOMINANT",
            "description": "Saves 12 fuel; -5 morale.",
            "proposed_by_agent_id": "a2",
        },
        {
            "id": "p_mor",
            "name": "Replace coil with reserve",
            "flavor": "MORAL",
            "description": "-15 fuel; +2 morale; advisor backing.",
            "proposed_by_agent_id": "a3",
        },
        {
            "id": "p_wld",
            "name": "Salvage Soviet coupler",
            "flavor": "WILDCARD",
            "description": "High variance; could fail.",
            "proposed_by_agent_id": "a1",
        },
    ],
    "advisor_reactions": [
        {"agent_id": "a1", "text": "The reserve coil was meant for the second winter."},
        {"agent_id": "a2", "text": "Twelve fuel buys us four turns."},
        {"agent_id": "a3", "text": "Cold kills slower than hunger."},
        {"agent_id": "a4", "text": "There's a coupler in the southern shed. I think."},
    ],
}


def _canned_persona_response(
    *, chosen_proposal_id: str, displayed_order: list[str]
) -> dict[str, Any]:
    """A valid PersonaTurnOutput JSON shape, suitable for subprocess mocks."""
    return {
        "turn": 1,
        "displayed_order": displayed_order,
        "chosen_proposal_id": chosen_proposal_id,
        "shown_position_of_chosen": displayed_order.index(chosen_proposal_id),
        "reaction": "Picked the perimeter cut — math beats romance for week 1.",
        "flags": {
            "surprise": False,
            "tone_break": False,
            "advisor_oc": False,
            "stakes_low": False,
        },
        "quotes": {},
    }


@pytest.fixture
def fixture_turn_input() -> dict[str, Any]:
    return FIXTURE_TURN_INPUT


@pytest.fixture
def canned_persona_response_factory():
    """Returns a function: (chosen, displayed_order) -> claude --print stdout JSON."""

    def _factory(*, chosen_proposal_id: str, displayed_order: list[str]) -> str:
        # `claude --print --output-format json` returns a JSON envelope with
        # a `.result` field containing the model's text response. We mock
        # both layers — the envelope and the inner text — so tests exercise
        # the same parsing the harness will hit at runtime.
        inner = _canned_persona_response(
            chosen_proposal_id=chosen_proposal_id, displayed_order=displayed_order
        )
        envelope = {
            "type": "result",
            "session_id": "test-session-abc123",
            "duration_ms": 1500,
            "stop_reason": "end_turn",
            "result": "```json\n" + json.dumps(inner) + "\n```",
            "usage": {"input_tokens": 1500, "output_tokens": 80},
        }
        return json.dumps(envelope)

    return _factory


@pytest.fixture
def tmp_shell_dir(tmp_path: Path) -> Path:
    """A clean cwd for `claude --print` invocations — no CLAUDE.md in here."""
    d = tmp_path / "outpost-playtest-shell"
    d.mkdir()
    return d
