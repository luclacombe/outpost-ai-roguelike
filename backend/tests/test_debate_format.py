"""submit_debate tool schema and parser (flat debate format)."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from app.agent.debate_format import DEBATE_TOOL_NAME, build_debate_tool, parse_submit_debate
from app.agent.prepare import prepare_proposals
from app.agent.prompts import DebateParse
from app.game.generator import generate_run
from app.game.setting_loader import load_setting
from tests._factories import _agent

ARCTIC = load_setting("arctic_station_v1")
USER = UUID("bbbbbbbb-0000-0000-0000-000000000002")

VERA = _agent("Vera")
OMAR = _agent("Omar")
LINA = _agent("Lina")
KAI = _agent("Kai")
ROSTER = [VERA, OMAR, LINA, KAI]
NAMES = [a.name for a in ROSTER]


def _response(payload: dict[str, Any]) -> SimpleNamespace:
    block = SimpleNamespace(type="tool_use", name=DEBATE_TOOL_NAME, input=payload)
    return SimpleNamespace(content=[block])


def _option(advisor: str, title: str, flavor: str, effects: list[dict[str, Any]]) -> dict:
    return {
        "flavor": flavor,
        "advisor": advisor,
        "title": title,
        "description": f"{title} now.",
        "effects": effects,
    }


def _payload() -> dict[str, Any]:
    return {
        "reactions": [{"advisor": n, "line": f"{n} has a view."} for n in NAMES],
        "options": [
            _option(
                "Vera",
                "Burn the reserve",
                "DOMINANT",
                [{"kind": "resource", "resource": "fuel", "delta": -5}],
            ),
            _option(
                "Omar",
                "Share the load",
                "MORAL",
                [{"kind": "health", "target": "Lina", "delta": 3}],
            ),
            _option(
                "Kai",
                "Roll the dice",
                "WILDCARD",
                [{"kind": "grievance", "target": "Vera", "delta": 4}],
            ),
        ],
    }


def test_schema_enums_hold_roster_and_resources() -> None:
    tool = build_debate_tool(ROSTER, ["fuel", "food"])
    assert tool["name"] == "submit_debate"
    props = tool["input_schema"]["properties"]
    options = props["options"]
    assert options["minItems"] == options["maxItems"] == 3
    assert props["reactions"]["items"]["properties"]["advisor"]["enum"] == NAMES
    option_props = options["items"]["properties"]
    assert option_props["advisor"]["enum"] == NAMES
    assert option_props["flavor"]["enum"] == ["DOMINANT", "MORAL", "WILDCARD"]
    effects = option_props["effects"]
    assert (effects["minItems"], effects["maxItems"]) == (1, 5)
    effect_props = effects["items"]["properties"]
    assert effect_props["resource"]["enum"] == ["fuel", "food"]
    assert effect_props["target"]["enum"] == [*NAMES, "player"]
    assert effect_props["kind"]["enum"] == ["resource", "health", "grievance"]
    assert effect_props["delta"] == {"type": "integer"}


def test_parse_maps_reactions_and_options() -> None:
    parsed = parse_submit_debate(_response(_payload()), ROSTER)
    assert set(parsed.per_agent_text) == {a.id for a in ROSTER}
    assert parsed.per_agent_text[VERA.id] == "Vera has a view."
    assert [c.name for c in parsed.tool_calls] == ["propose_action"] * 3
    assert [c.caller_id for c in parsed.tool_calls] == [VERA.id, OMAR.id, KAI.id]
    first = parsed.tool_calls[0].args
    assert first["name"] == "Burn the reserve"
    assert first["flavor"] == "DOMINANT"
    assert first["risk_note"] == ""
    assert first["tool_calls_on_pick"] == [
        {
            "name": "modify_resource",
            "args": {"resource_id": "fuel", "delta": -5, "reason": "Burn the reserve"},
        }
    ]


def test_parse_reason_is_title_truncated_to_60() -> None:
    payload = _payload()
    payload["options"][0]["title"] = "T" * 90
    parsed = parse_submit_debate(_response(payload), ROSTER)
    nested = parsed.tool_calls[0].args["tool_calls_on_pick"][0]
    assert nested["args"]["reason"] == "T" * 60


def test_parse_is_case_insensitive_and_drops_unknown_names() -> None:
    payload = _payload()
    payload["reactions"] = [
        {"advisor": "vera", "line": "Lowercase works."},
        {"advisor": "Nobody", "line": "I am not here."},
    ]
    payload["options"][1]["advisor"] = "Nobody"
    parsed = parse_submit_debate(_response(payload), ROSTER)
    assert parsed.per_agent_text == {VERA.id: "Lowercase works."}
    assert parsed.tool_calls[1].caller_id is None


def test_parse_health_target_player_and_agent() -> None:
    payload = _payload()
    payload["options"][0]["effects"] = [
        {"kind": "health", "target": "player", "delta": -2},
        {"kind": "health", "target": "Vera", "delta": 1},
        {"kind": "grievance", "target": "player", "delta": 3},
    ]
    parsed = parse_submit_debate(_response(payload), ROSTER)
    nested = parsed.tool_calls[0].args["tool_calls_on_pick"]
    assert nested[0] == {
        "name": "modify_health",
        "args": {"target_id": "player", "delta": -2, "reason": "Burn the reserve"},
    }
    assert nested[1]["args"]["target_id"] == str(VERA.id)
    assert nested[2] == {
        "name": "adjust_grievance",
        "args": {"target_id": "player", "delta": 3, "reason": "Burn the reserve"},
    }


def test_parse_passes_unknown_resource_through_for_prepare_to_strip() -> None:
    colony = generate_run(user_id=USER, setting_id="arctic_station_v1", seed=7).model_copy(
        update={"turn_number": 3, "legitimacy": 60}
    )
    roster = colony.agents
    payload = {
        "reactions": [],
        "options": [
            _option(
                roster[i].name,
                f"Option {i}",
                flavor,
                [{"kind": "resource", "resource": "unobtainium", "delta": -5}],
            )
            for i, flavor in enumerate(["DOMINANT", "MORAL", "WILDCARD"])
        ],
    }
    parsed = parse_submit_debate(_response(payload), roster)
    nested = parsed.tool_calls[0].args["tool_calls_on_pick"][0]
    assert nested["args"]["resource_id"] == "unobtainium"
    out = prepare_proposals(colony, parsed.tool_calls, ARCTIC)
    assert out.classes
    assert set(out.classes) <= {"no_effect", "degraded"}


def test_parse_text_only_response_is_empty() -> None:
    response = SimpleNamespace(content=[SimpleNamespace(type="text", text="I refuse")])
    assert parse_submit_debate(response, ROSTER) == DebateParse()


def test_parse_ignores_other_tool_calls() -> None:
    other = SimpleNamespace(type="tool_use", name="propose_action", input=_payload())
    assert parse_submit_debate(SimpleNamespace(content=[other]), ROSTER) == DebateParse()


def test_parse_drops_non_latin_reaction_and_option(caplog: pytest.LogCaptureFixture) -> None:
    payload = _payload()
    payload["reactions"][0]["line"] = "Vorgrad's 頻道 is bleeding static"
    payload["options"][1]["title"] = "Поделиться грузом"
    with caplog.at_level(logging.WARNING, logger="app.agent.debate_format"):
        parsed = parse_submit_debate(_response(payload), ROSTER)
    assert VERA.id not in parsed.per_agent_text
    assert len(parsed.per_agent_text) == 3
    assert [c.args["name"] for c in parsed.tool_calls] == ["Burn the reserve", "Roll the dice"]
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2
