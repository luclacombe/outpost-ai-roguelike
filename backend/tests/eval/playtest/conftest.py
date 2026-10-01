"""Shared fixtures for playtest harness tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# A fixture turn input for the vertical-slice smoke test. Mirrors the shape
# `app/eval/playtest/turn_view.py::TurnViewTracker.build` assembles from a
# real /game/{id}/turn SSE stream + game_sessions.state (UI parity).
_ADVISORS = [
    {"id": "a1", "name": "Marko", "role": "Commissar", "is_alive": True, "status_label": "STEADY"},
    {
        "id": "a2",
        "name": "Nadia",
        "role": "Quartermaster",
        "is_alive": True,
        "status_label": "STEWING",
    },
    {"id": "a3", "name": "Irena", "role": "Medic", "is_alive": True, "status_label": "STEADY"},
    {
        "id": "a4",
        "name": "Yuri",
        "role": "Radio Operator",
        "is_alive": True,
        "status_label": "TIRED",
    },
]


def _resource(rid: str, name: str, value: int, max_: int = 100, crit: int = 20) -> dict[str, Any]:
    return {"id": rid, "name": name, "value": value, "max": max_, "critical_threshold": crit}


FIXTURE_TURN_INPUT: dict[str, Any] = {
    "turn": 1,
    "day": 0,
    "max_turns": 9,
    "setting_name": "Vorgrad Station",
    "setting_lore": "Vorgrad Station — arctic research outpost reactivated after a "
    "geopolitical crisis. The ice was never supposed to be this political.",
    "dashboard": {
        "legitimacy": 50,
        "resources": [
            _resource("fuel", "Fuel", 80),
            _resource("food", "Food", 18),
            _resource("warmth", "Warmth", 65),
            _resource("medicine", "Medicine", 8),
            _resource("morale", "Morale", 60),
        ],
        "advisors": _ADVISORS,
    },
    "event": {
        "id": "ev_coil",
        "title": "Heating coil shows hairline cracks",
        "body": "The east-wing heating coil is on its last week.",
        "category": "infrastructure",
        "severity": 3,
        "origin": "NATURAL",
    },
    "event_log": [{"turn": 0, "text": "heating coil shows hairline cracks"}],
    "last_turn": None,
    "proposals": [
        {
            "id": "p_dom",
            "name": "Cut perimeter heating",
            "flavor": "DOMINANT",
            "description": "Saves 12 fuel; -5 morale.",
            "proposed_by_agent_id": "a2",
            "effects_preview": ["+12 fuel", "-5 morale"],
        },
        {
            "id": "p_mor",
            "name": "Replace coil with reserve",
            "flavor": "MORAL",
            "description": "-15 fuel; +2 morale; advisor backing.",
            "proposed_by_agent_id": "a3",
            "effects_preview": ["-15 fuel", "+2 morale"],
        },
        {
            "id": "p_wld",
            "name": "Salvage Soviet coupler",
            "flavor": "WILDCARD",
            "description": "High variance; could fail.",
            "proposed_by_agent_id": "a1",
            "effects_preview": [],
        },
    ],
    "advisor_reactions": [
        {"agent_id": "a1", "text": "The reserve coil was meant for the second winter."},
        {"agent_id": "a2", "text": "Twelve fuel buys us four turns."},
        {"agent_id": "a3", "text": "Cold kills slower than hunger."},
        {"agent_id": "a4", "text": "There's a coupler in the southern shed. I think."},
    ],
}

# Turn 2 of the same game: one advisor died, the persona sees last turn's outcome.
FIXTURE_TURN_INPUT_T2: dict[str, Any] = {
    **FIXTURE_TURN_INPUT,
    "turn": 2,
    "day": 1,
    "dashboard": {
        **FIXTURE_TURN_INPUT["dashboard"],
        "legitimacy": 46,
        "advisors": [
            *_ADVISORS[:3],
            {**_ADVISORS[3], "is_alive": False, "status_label": "KIA"},
        ],
    },
    "event": {
        "id": "ev_storm",
        "title": "Whiteout on the ridge",
        "body": "The antenna mast is icing over.",
        "category": "weather",
        "severity": 4,
        "origin": "SEED_FIRED",
    },
    "event_log": [
        {"turn": 0, "text": "heating coil shows hairline cracks"},
        {"turn": 0, "text": "Yuri died."},
        {"turn": 1, "text": "whiteout on the ridge"},
    ],
    "last_turn": {
        "chosen": {
            "name": "Cut perimeter heating",
            "flavor": "DOMINANT",
            "effects_preview": ["+12 fuel", "-5 morale"],
        },
        "narration": "The perimeter went dark. Yuri did not come back from the shed.",
        "deaths": ["Yuri"],
        "legitimacy_delta": -4,
        "resource_deltas": {"Fuel": 12, "Morale": -5},
        "status_changes": [{"name": "Yuri", "from": "TIRED", "to": "KIA"}],
    },
    "advisor_reactions": FIXTURE_TURN_INPUT["advisor_reactions"][:3],
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
def fixture_turn_input_t2() -> dict[str, Any]:
    return FIXTURE_TURN_INPUT_T2


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
