"""Smoke tests for app.game.tools — schema structure and JSON Schema correctness."""

import jsonschema
import pytest

from app.game.tools import AGENT_TOOL_SCHEMAS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCHEMA_BY_NAME: dict = {t["name"]: t for t in AGENT_TOOL_SCHEMAS}


def _get(name: str) -> dict:
    return _SCHEMA_BY_NAME[name]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_all_tools_present() -> None:
    assert {t["name"] for t in AGENT_TOOL_SCHEMAS} == {
        "propose_action",
        "modify_resource",
        "modify_health",
        "adjust_grievance",
        "speak_to_agent",
        "adjust_relationship",
        "adjust_own_grievance",
        "remember",
        "initiate_coup",
        "back_leader",
    }


def test_each_schema_has_required_fields() -> None:
    for tool in AGENT_TOOL_SCHEMAS:
        name = tool["name"]
        assert "name" in tool, f"{name}: missing 'name'"
        assert "description" in tool, f"{name}: missing 'description'"
        assert "input_schema" in tool, f"{name}: missing 'input_schema'"

        schema = tool["input_schema"]
        assert schema.get("type") == "object", f"{name}: input_schema.type must be 'object'"
        assert schema.get("additionalProperties") is False, (
            f"{name}: additionalProperties must be False"
        )
        assert isinstance(schema.get("properties"), dict), (
            f"{name}: properties must be a dict"
        )
        assert isinstance(schema.get("required"), list), (
            f"{name}: required must be a list"
        )


def test_every_required_arg_is_declared_in_properties() -> None:
    for tool in AGENT_TOOL_SCHEMAS:
        name = tool["name"]
        schema = tool["input_schema"]
        properties = set(schema.get("properties", {}).keys())
        for field in schema.get("required", []):
            assert field in properties, (
                f"{name}: required field '{field}' not declared in properties"
            )


def test_schemas_are_valid_json_schema() -> None:
    for tool in AGENT_TOOL_SCHEMAS:
        jsonschema.Draft7Validator.check_schema(tool["input_schema"])


def test_initiate_coup_accepts_empty_args() -> None:
    schema = _get("initiate_coup")["input_schema"]
    jsonschema.validate(instance={}, schema=schema)  # must not raise


def test_modify_resource_rejects_out_of_range_delta() -> None:
    schema = _get("modify_resource")["input_schema"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            instance={"resource_id": "fuel", "delta": 999, "reason": "too much"},
            schema=schema,
        )


def test_modify_resource_rejects_additional_properties() -> None:
    schema = _get("modify_resource")["input_schema"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            instance={"resource_id": "fuel", "delta": 1, "reason": "x", "extra": "no"},
            schema=schema,
        )


def test_descriptions_non_empty_and_single_line() -> None:
    for tool in AGENT_TOOL_SCHEMAS:
        desc = tool["description"]
        assert len(desc) >= 30, (
            f"{tool['name']}: description too short ({len(desc)} chars)"
        )
        assert "\n" not in desc, f"{tool['name']}: description must be single-line"


def test_propose_action_flavor_enum_values() -> None:
    schema = _get("propose_action")["input_schema"]
    flavor = schema["properties"]["flavor"]
    assert flavor.get("enum") == ["DOMINANT", "MORAL", "WILDCARD"]


def test_caller_agent_id_present_in_all_tools() -> None:
    """Every agent-callable tool must declare caller_agent_id as an optional string/uuid."""
    for tool in AGENT_TOOL_SCHEMAS:
        name = tool["name"]
        props = tool["input_schema"]["properties"]
        required = tool["input_schema"]["required"]

        assert "caller_agent_id" in props, (
            f"{name}: missing caller_agent_id in properties"
        )
        field = props["caller_agent_id"]
        assert field.get("type") == "string", (
            f"{name}: caller_agent_id must be type 'string', got {field.get('type')!r}"
        )
        assert field.get("format") == "uuid", (
            f"{name}: caller_agent_id must have format 'uuid', got {field.get('format')!r}"
        )
        assert "caller_agent_id" not in required, (
            f"{name}: caller_agent_id must NOT be in required (engine calls omit it)"
        )
