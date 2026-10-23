"""submit_debate tool schema and parser (flat debate format)."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from app.agent.debate_format import (
    DEBATE_TOOL_NAME,
    OFFTURN_TOOL_NAME,
    build_debate_tool,
    build_offturn_tool,
    incomplete_effects,
    parse_submit_debate,
    parse_submit_offturn,
)
from app.agent.narration_guard import build_allowed_terms, non_latin_words
from app.agent.prepare import prepare_proposals
from app.agent.prompts import DebateParse
from app.game.engine import apply_tool_call
from app.game.generator import generate_run
from app.game.schemas import Event, EventOrigin
from app.game.setting_loader import load_setting
from tests._factories import _agent, _colony

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


def _option(
    advisor: str,
    title: str,
    flavor: str,
    resource_effects: list[dict[str, Any]] | None = None,
    person_effects: list[dict[str, Any]] | None = None,
) -> dict:
    return {
        "flavor": flavor,
        "advisor": advisor,
        "title": title,
        "description": f"{title} now.",
        "resource_effects": resource_effects if resource_effects is not None else [],
        "person_effects": person_effects if person_effects is not None else [],
    }


def _payload() -> dict[str, Any]:
    return {
        "reactions": [{"advisor": n, "line": f"{n} has a view."} for n in NAMES],
        "options": [
            _option(
                "Vera",
                "Burn the reserve",
                "DOMINANT",
                resource_effects=[{"resource": "fuel", "delta": -5}],
            ),
            _option(
                "Omar",
                "Share the load",
                "MORAL",
                person_effects=[{"kind": "health", "target": "Lina", "delta": 3}],
            ),
            _option(
                "Kai",
                "Roll the dice",
                "WILDCARD",
                person_effects=[{"kind": "grievance", "target": "Vera", "delta": 4}],
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
    resource = option_props["resource_effects"]
    assert resource["maxItems"] == 5
    assert resource["items"]["properties"]["resource"]["enum"] == ["fuel", "food"]
    person = option_props["person_effects"]
    assert person["maxItems"] == 3
    person_props = person["items"]["properties"]
    assert person_props["target"]["enum"] == [*NAMES, "player"]
    assert person_props["kind"]["enum"] == ["health", "grievance"]
    assert person_props["delta"]["type"] == "integer"


def test_debate_tool_splits_effect_lists() -> None:
    tool = build_debate_tool(ROSTER, ["fuel", "food"])
    option = tool["input_schema"]["properties"]["options"]["items"]
    props = option["properties"]
    assert "effects" not in props
    assert "resource_effects" in props
    assert "person_effects" in props
    assert option["required"] == [
        "flavor",
        "advisor",
        "title",
        "description",
        "resource_effects",
        "person_effects",
    ]
    assert props["resource_effects"]["type"] == "array"
    assert props["person_effects"]["type"] == "array"
    assert "minItems" not in props["resource_effects"]
    assert "minItems" not in props["person_effects"]
    assert props["resource_effects"]["items"]["required"] == ["resource", "delta"]
    assert props["person_effects"]["items"]["required"] == ["kind", "target", "delta"]
    assert props["person_effects"]["items"]["properties"]["kind"]["enum"] == [
        "health",
        "grievance",
    ]
    assert "at least one effect" in props["resource_effects"]["description"]
    assert "at least one effect" in props["person_effects"]["description"]


def test_debate_tool_effect_fields_describe_requirements() -> None:
    tool = build_debate_tool(ROSTER, ["fuel", "food"])
    option = tool["input_schema"]["properties"]["options"]["items"]
    resource = option["properties"]["resource_effects"]["items"]["properties"]
    person = option["properties"]["person_effects"]["items"]["properties"]
    assert resource["delta"]["description"] == "Signed integer change."
    assert person["delta"]["description"] == "Signed integer change."
    assert person["target"]["description"] == "An advisor name, or player (health only)."


def test_debate_tool_is_deterministic() -> None:
    assert build_debate_tool(ROSTER, ["fuel", "food"]) == build_debate_tool(
        ROSTER, ["fuel", "food"]
    )


def _effects_response(resource_effects: Any = None, person_effects: Any = None) -> SimpleNamespace:
    option = _option("Vera", "Plan", "DOMINANT")
    option["resource_effects"] = resource_effects
    option["person_effects"] = person_effects
    return _response({"reactions": [], "options": [option]})


def test_incomplete_effects_counts_new_shape(caplog: pytest.LogCaptureFixture) -> None:
    response = _effects_response(
        resource_effects=[
            {"delta": -5},
            {"resource": "", "delta": -5},
            {"resource": "fuel", "delta": -5},
        ],
        person_effects=[
            {"kind": "health", "delta": 2},
            {"kind": "resource", "target": "Lina", "delta": 2},
            {"kind": "grievance", "target": "Vera", "delta": 4},
        ],
    )
    with caplog.at_level(logging.WARNING, logger="app.agent.debate_format"):
        assert incomplete_effects(response) == 4
    logged = [r.getMessage() for r in caplog.records]
    assert len(logged) == 4
    assert logged[0] == "submit_debate: resource effect lacks 'resource'; keys sent: ['delta']"
    assert logged[1] == (
        "submit_debate: resource effect lacks 'resource'; keys sent: ['delta', 'resource']"
    )
    assert logged[2] == "submit_debate: person effect lacks 'target'; keys sent: ['delta', 'kind']"
    assert logged[3] == (
        "submit_debate: person effect has kind 'resource'; keys sent: ['delta', 'kind', 'target']"
    )
    assert not any("fuel" in m or "Lina" in m for m in logged)


def test_incomplete_effects_counts_bad_kind_and_missing_target_once() -> None:
    response = _effects_response(person_effects=[{"kind": "mood", "delta": 1}])
    assert incomplete_effects(response) == 1


def test_incomplete_effects_counts_non_dict_entries() -> None:
    assert incomplete_effects(_effects_response(["x", 3], [None])) == 3


def test_incomplete_effects_complete_payload_is_zero() -> None:
    assert incomplete_effects(_response(_payload())) == 0


def test_incomplete_effects_never_raises_on_malformed_output() -> None:
    assert incomplete_effects(SimpleNamespace(content=[])) == 0
    assert incomplete_effects(SimpleNamespace()) == 0
    assert incomplete_effects(_response({"options": "nope"})) == 0
    assert incomplete_effects(_response({"options": ["x", {"resource_effects": "y"}]})) == 0
    assert incomplete_effects(_effects_response("y", {"kind": "health"})) == 0
    assert incomplete_effects(_effects_response(None, None)) == 0
    assert incomplete_effects(_effects_response([], [{"kind": ["health"], "target": "x"}])) == 1
    assert incomplete_effects(_effects_response([{"resource": ["fuel"]}], [])) == 0
    block = SimpleNamespace(type="tool_use", name=DEBATE_TOOL_NAME, input="text")
    assert incomplete_effects(SimpleNamespace(content=[block])) == 0


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
    payload["options"][0]["resource_effects"] = []
    payload["options"][0]["person_effects"] = [
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
                resource_effects=[{"resource": "unobtainium", "delta": -5}],
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


def test_parse_survives_malformed_containers() -> None:
    payload: dict[str, Any] = {
        "reactions": 5,
        "options": [
            {**_option("Vera", "Odd", "DOMINANT"), "resource_effects": 5},
            {**_option("Omar", "Odder", "MORAL"), "person_effects": True},
        ],
    }
    parsed = parse_submit_debate(_response(payload), ROSTER)
    assert parsed.per_agent_text == {}
    assert [c.args["tool_calls_on_pick"] for c in parsed.tool_calls] == [[], []]
    assert parse_submit_debate(_response({"reactions": True, "options": 3}), ROSTER) == (
        DebateParse()
    )


def test_parse_maps_both_lists() -> None:
    payload = _payload()
    payload["options"][0]["title"] = "Mixed plan"
    payload["options"][0]["resource_effects"] = [{"resource": "food", "delta": 5}]
    payload["options"][0]["person_effects"] = [
        {"kind": "grievance", "target": "lina", "delta": 1},
        {"kind": "health", "target": "player", "delta": -2},
    ]
    nested = (
        parse_submit_debate(_response(payload), ROSTER).tool_calls[0].args["tool_calls_on_pick"]
    )
    assert [c["name"] for c in nested] == ["modify_resource", "adjust_grievance", "modify_health"]
    assert nested[0]["args"]["resource_id"] == "food"
    assert nested[1]["args"]["target_id"] == str(LINA.id)
    assert nested[2]["args"]["target_id"] == "player"
    assert all(c["args"]["reason"] == "Mixed plan" for c in nested)
    assert all(len(c["args"]["reason"]) <= 60 for c in nested)


def test_parse_person_only_option() -> None:
    payload = _payload()
    option = payload["options"][0]
    option["resource_effects"] = []
    option["person_effects"] = [{"kind": "health", "target": "Omar", "delta": 2}]
    nested = (
        parse_submit_debate(_response(payload), ROSTER).tool_calls[0].args["tool_calls_on_pick"]
    )
    assert [c["name"] for c in nested] == ["modify_health"]


def test_parse_missing_or_malformed_lists_give_no_effects() -> None:
    absent = {"reactions": [], "options": [{"flavor": "DOMINANT", "advisor": "Vera", "title": "A"}]}
    strings = {
        "reactions": [],
        "options": [
            {**_option("Vera", "A", "DOMINANT"), "resource_effects": "food +5"},
            {**_option("Omar", "B", "MORAL"), "person_effects": "Lina -2"},
        ],
    }
    for payload in (absent, strings):
        parsed = parse_submit_debate(_response(payload), ROSTER)
        assert parsed.tool_calls
        assert all(c.args["tool_calls_on_pick"] == [] for c in parsed.tool_calls)


def test_parse_caps_combined_effects_at_five() -> None:
    payload = _payload()
    option = payload["options"][0]
    option["resource_effects"] = [{"resource": "fuel", "delta": i + 1} for i in range(4)]
    option["person_effects"] = [
        {"kind": "health", "target": "Vera", "delta": i + 1} for i in range(3)
    ]
    nested = (
        parse_submit_debate(_response(payload), ROSTER).tool_calls[0].args["tool_calls_on_pick"]
    )
    assert [c["name"] for c in nested] == ["modify_resource"] * 4 + ["modify_health"]
    assert nested[4]["args"]["delta"] == 1


def test_parse_skips_unknown_person_kind_and_non_dict_entries() -> None:
    payload = _payload()
    option = payload["options"][0]
    option["resource_effects"] = ["x", {"resource": "fuel", "delta": 1}]
    option["person_effects"] = [
        {"kind": "resource", "target": "Vera", "delta": 5},
        None,
        {"kind": "grievance", "target": "Vera", "delta": 1},
    ]
    nested = (
        parse_submit_debate(_response(payload), ROSTER).tool_calls[0].args["tool_calls_on_pick"]
    )
    assert [c["name"] for c in nested] == ["modify_resource", "adjust_grievance"]


def test_legacy_effects_key_ignored() -> None:
    payload = _payload()
    option = {
        "flavor": "DOMINANT",
        "advisor": "Vera",
        "title": "Old shape",
        "description": "Old.",
        "effects": [{"kind": "resource", "resource": "fuel", "delta": -5}],
    }
    payload["options"] = [option]
    parsed = parse_submit_debate(_response(payload), ROSTER)
    assert parsed.tool_calls[0].args["tool_calls_on_pick"] == []
    assert incomplete_effects(_response(payload)) == 0


def test_parse_survives_non_dict_payload_and_missing_content() -> None:
    block = SimpleNamespace(type="tool_use", name=DEBATE_TOOL_NAME, input="not a dict")
    assert parse_submit_debate(SimpleNamespace(content=[block]), ROSTER) == DebateParse()
    assert parse_submit_debate(SimpleNamespace(content=None), ROSTER) == DebateParse()


# --- submit_offturn ---------------------------------------------------------

DECISION = "11111111-2222-3333-4444-555555555555"


def _offturn_response(actions: Any) -> SimpleNamespace:
    block = SimpleNamespace(type="tool_use", name=OFFTURN_TOOL_NAME, input={"actions": actions})
    return SimpleNamespace(content=[block])


def test_offturn_schema_enums() -> None:
    tool = build_offturn_tool(ROSTER)
    assert tool["name"] == "submit_offturn"
    actions = tool["input_schema"]["properties"]["actions"]
    assert actions["maxItems"] == 8
    item = actions["items"]
    assert item["required"] == ["advisor", "kind"]
    props = item["properties"]
    assert props["advisor"]["enum"] == NAMES
    assert props["target"]["enum"] == NAMES
    assert props["kind"]["enum"] == [
        "remember",
        "adjust_own_grievance",
        "speak_to_agent",
        "adjust_relationship",
        "back_leader",
        "initiate_coup",
    ]
    assert props["text"]["type"] == "string"
    assert props["delta"]["type"] == "integer"


def test_offturn_schema_carries_field_descriptions() -> None:
    tool = build_offturn_tool(ROSTER)
    assert tool["description"]
    props = tool["input_schema"]["properties"]["actions"]["items"]["properties"]
    for field in ("advisor", "kind", "text", "target", "delta"):
        assert props[field]["description"], field


def test_offturn_maps_each_kind() -> None:
    actions = [
        {"advisor": "Vera", "kind": "remember", "text": "x" * 250},
        {"advisor": "Omar", "kind": "adjust_own_grievance", "delta": 2, "text": "Ignored"},
        {"advisor": "Lina", "kind": "adjust_own_grievance", "delta": -1},
        {"advisor": "Kai", "kind": "speak_to_agent", "target": "vera", "text": "y" * 400},
        {"advisor": "Vera", "kind": "adjust_relationship", "target": "Omar", "delta": -2},
        {"advisor": "Omar", "kind": "back_leader", "delta": 9},
        {"advisor": "Lina", "kind": "back_leader"},
        {"advisor": "Kai", "kind": "initiate_coup"},
    ]
    calls = parse_submit_offturn(_offturn_response(actions), ROSTER, DECISION)
    assert [(c.name, c.caller_id) for c in calls] == [
        ("remember", VERA.id),
        ("adjust_own_grievance", OMAR.id),
        ("adjust_own_grievance", LINA.id),
        ("speak_to_agent", KAI.id),
        ("adjust_relationship", VERA.id),
        ("back_leader", OMAR.id),
        ("back_leader", LINA.id),
        ("initiate_coup", KAI.id),
    ]
    assert calls[0].args == {"fact": "x" * 200}
    assert calls[1].args == {"delta": 2, "reason": "Ignored"}
    assert calls[2].args == {"delta": -1, "reason": ""}
    assert calls[3].args == {"target_id": str(VERA.id), "message": "y" * 300}
    assert calls[4].args == {"target_id": str(OMAR.id), "delta": -2}
    assert calls[5].args == {"decision_id": DECISION, "legitimacy_bonus": 5}
    assert calls[6].args == {"decision_id": DECISION, "legitimacy_bonus": 1}
    assert calls[7].args == {}


def test_offturn_empty_list_gives_no_calls() -> None:
    assert parse_submit_offturn(_offturn_response([]), ROSTER, DECISION) == []
    assert parse_submit_offturn(SimpleNamespace(content=[]), ROSTER, DECISION) == []
    assert parse_submit_offturn(_offturn_response("nope"), ROSTER, DECISION) == []
    assert parse_submit_offturn(_offturn_response(["bad", 3, None]), ROSTER, DECISION) == []


def test_offturn_skips_unknown_target_and_non_latin_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    actions = [
        {"advisor": "Ghost", "kind": "remember", "text": "unknown advisor"},
        {"advisor": "Vera", "kind": "speak_to_agent", "target": "Nobody", "text": "hi"},
        {"advisor": "Vera", "kind": "speak_to_agent", "text": "no target"},
        {"advisor": "Vera", "kind": "adjust_relationship", "target": "Nobody", "delta": 1},
        {"advisor": "Omar", "kind": "remember", "text": "\u4f60\u597d the colony"},
        {"advisor": "Omar", "kind": "teleport"},
        {"advisor": "Kai", "kind": "remember", "text": "kept"},
    ]
    with caplog.at_level(logging.WARNING, logger="app.agent.debate_format"):
        calls = parse_submit_offturn(_offturn_response(actions), ROSTER, DECISION)
    assert [(c.name, c.args) for c in calls] == [("remember", {"fact": "kept"})]
    assert "submit_offturn" in caplog.text


def test_offturn_back_leader_needs_decision_id() -> None:
    actions = [
        {"advisor": "Omar", "kind": "back_leader", "delta": 2},
        {"advisor": "Kai", "kind": "remember", "text": "kept"},
    ]
    calls = parse_submit_offturn(_offturn_response(actions), ROSTER, None)
    assert [c.name for c in calls] == ["remember"]


# --- non-Latin filter vs the setting's own vocabulary ------------------------

_ARCHIVE = next(e for e in ARCTIC.event_categories if e.id == "soviet_archive_find")
ARCHIVE_EVENT = Event(
    id="soviet_archive_find",
    category=_ARCHIVE.id,
    title=_ARCHIVE.title or "",
    body=_ARCHIVE.prompt_template,
    severity=2,
    origin=EventOrigin.NATURAL,
)
_COLONY = generate_run(user_id=USER, setting_id="arctic_station_v1", seed=7)
ALLOWED = build_allowed_terms(
    ARCTIC,
    _COLONY,
    [ARCTIC.lore, ARCTIC.narrative_voice, ARCHIVE_EVENT.title, ARCHIVE_EVENT.body],
)


def test_allowed_terms_keep_cyrillic_words_whole() -> None:
    assert "секретно" in non_latin_words(ALLOWED)
    assert non_latin_words(["Say «СЕКРЕТНО», then \u9891\u9053."]) == {"секретно", "\u9891\u9053"}


def test_parse_keeps_reaction_quoting_the_events_own_word() -> None:
    payload = _payload()
    payload["reactions"][0]["line"] = "The box says СЕКРЕТНО. Leave it sealed."
    parsed = parse_submit_debate(_response(payload), ROSTER, allowed_terms=ALLOWED)
    assert parsed.per_agent_text[VERA.id] == "The box says СЕКРЕТНО. Leave it sealed."
    # Same text with no allowed terms (the safe default) is dropped.
    assert VERA.id not in parse_submit_debate(_response(payload), ROSTER).per_agent_text


def test_parse_keeps_option_quoting_the_events_own_word() -> None:
    payload = _payload()
    payload["options"][0]["title"] = "Burn the СЕКРЕТНО box"
    parsed = parse_submit_debate(_response(payload), ROSTER, allowed_terms=ALLOWED)
    assert [c.args["name"] for c in parsed.tool_calls][0] == "Burn the СЕКРЕТНО box"


def test_parse_still_drops_a_stray_foreign_word_with_terms_supplied() -> None:
    payload = _payload()
    payload["reactions"][0]["line"] = "Vorgrad's \u983b\u9053 is bleeding static"
    payload["reactions"][1]["line"] = "СЕКРЕТНО and \u983b\u9053 together"
    parsed = parse_submit_debate(_response(payload), ROSTER, allowed_terms=ALLOWED)
    assert VERA.id not in parsed.per_agent_text
    assert OMAR.id not in parsed.per_agent_text
    assert len(parsed.per_agent_text) == 2


def test_parse_drops_option_with_unknown_cyrillic_word() -> None:
    payload = _payload()
    payload["options"][1]["title"] = "Поделиться грузом"
    payload["options"][2]["description"] = "СЕКРЕТНО but also груз"
    parsed = parse_submit_debate(_response(payload), ROSTER, allowed_terms=ALLOWED)
    assert [c.args["name"] for c in parsed.tool_calls] == ["Burn the reserve"]


def test_offturn_keeps_allowed_foreign_word_and_drops_unknown() -> None:
    actions = [
        {"advisor": "Vera", "kind": "remember", "text": "The box was marked СЕКРЕТНО."},
        {"advisor": "Omar", "kind": "remember", "text": "\u4f60\u597d the colony"},
    ]
    calls = parse_submit_offturn(
        _offturn_response(actions), ROSTER, DECISION, allowed_terms=ALLOWED
    )
    assert [c.args["fact"] for c in calls] == ["The box was marked СЕКРЕТНО."]


def test_parse_reads_input_defensively() -> None:
    bare = SimpleNamespace(type="tool_use", name=DEBATE_TOOL_NAME)
    assert parse_submit_debate(SimpleNamespace(content=[bare]), ROSTER) == DebateParse()
    bare_off = SimpleNamespace(type="tool_use", name=OFFTURN_TOOL_NAME)
    assert parse_submit_offturn(SimpleNamespace(content=[bare_off]), ROSTER, DECISION) == []


def test_offturn_grievance_without_text_reaches_validator() -> None:
    actions = [{"advisor": "Lina", "kind": "adjust_own_grievance", "delta": 2}]
    calls = parse_submit_offturn(_offturn_response(actions), ROSTER, DECISION)
    assert len(calls) == 1
    assert calls[0].args == {"delta": 2, "reason": ""}
    state = _colony(agents=[LINA])
    out = apply_tool_call(state, calls[0])
    assert out.turn_tool_calls[-1].rejection_reason == "missing_reason"


def test_offturn_schema_states_required_fields() -> None:
    tool = build_offturn_tool(ROSTER)
    props = tool["input_schema"]["properties"]["actions"]["items"]["properties"]
    for field in ("text", "target", "delta"):
        assert props[field]["description"].startswith("Required for "), field
