"""Anthropic tool schemas for Outpost agents.

Every state mutation in the game flows through one of these tools. The
engine validator (app/game/validator.py) checks each ToolCall against
ColonyState before applying; this file only defines the Anthropic-facing
contract.
"""

from typing import Any

AGENT_TOOL_NAMES = {
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

AGENT_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "propose_action",
        "description": (
            "Add a structured action to the player's choice pool for this turn; "
            "supply name, description, flavor, and optional previews."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "maxLength": 40},
                "description": {"type": "string", "maxLength": 200},
                "flavor": {
                    "type": "string",
                    "enum": ["DOMINANT", "MORAL", "WILDCARD"],
                },
                "effects_preview": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 5,
                },
                "tool_calls_on_pick": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["name", "description", "flavor"],
            "additionalProperties": False,
        },
    },
    {
        "name": "modify_resource",
        "description": (
            "Shift a colony resource by a signed integer delta; "
            "resource must exist in the run's setting and the engine rejects "
            "changes that would drive the value negative."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "resource_id": {"type": "string"},
                "delta": {"type": "integer", "minimum": -50, "maximum": 50},
                "reason": {"type": "string", "maxLength": 200},
            },
            "required": ["resource_id", "delta", "reason"],
            "additionalProperties": False,
        },
    },
    {
        "name": "modify_health",
        "description": (
            "Change the health of an agent or the player by a signed integer delta; "
            "target_id is an agent UUID or the literal string 'player'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_id": {"type": "string"},
                "delta": {"type": "integer", "minimum": -100, "maximum": 100},
                "reason": {"type": "string", "maxLength": 200},
            },
            "required": ["target_id", "delta", "reason"],
            "additionalProperties": False,
        },
    },
    {
        "name": "adjust_grievance",
        "description": (
            "Raise or lower another agent's grievance score by up to ±3; "
            "target_id must be a different agent's UUID, not your own."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_id": {"type": "string"},
                "delta": {"type": "integer", "minimum": -3, "maximum": 3},
                "reason": {"type": "string", "maxLength": 200},
            },
            "required": ["target_id", "delta", "reason"],
            "additionalProperties": False,
        },
    },
    {
        "name": "speak_to_agent",
        "description": (
            "Send a private message to another agent; "
            "the message is logged and may influence that agent's next response."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_id": {"type": "string"},
                "message": {"type": "string", "maxLength": 300},
            },
            "required": ["target_id", "message"],
            "additionalProperties": False,
        },
    },
    {
        "name": "adjust_relationship",
        "description": (
            "Shift your relationship score toward another agent by up to ±3; "
            "scores range from -10 (hostile) to +10 (allied)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_id": {"type": "string"},
                "delta": {"type": "integer", "minimum": -3, "maximum": 3},
            },
            "required": ["target_id", "delta"],
            "additionalProperties": False,
        },
    },
    {
        "name": "adjust_own_grievance",
        "description": (
            "Raise or lower your own grievance score by up to ±3 "
            "in response to a decision or colony event."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "delta": {"type": "integer", "minimum": -3, "maximum": 3},
                "reason": {"type": "string", "maxLength": 200},
            },
            "required": ["delta", "reason"],
            "additionalProperties": False,
        },
    },
    {
        "name": "remember",
        "description": (
            "Write a short fact to your persistent memory; "
            "the most recent 10 entries are injected into every future prompt."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fact": {"type": "string", "maxLength": 200},
            },
            "required": ["fact"],
            "additionalProperties": False,
        },
    },
    {
        "name": "initiate_coup",
        "description": (
            "Attempt to overthrow the player; "
            "the engine validates grievance thresholds and coalition support before resolving."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "back_leader",
        "description": (
            "Publicly endorse the player's last decision, granting a legitimacy bonus "
            "between 1 and 5 points."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "decision_id": {"type": "string"},
                "legitimacy_bonus": {"type": "integer", "minimum": 1, "maximum": 5},
            },
            "required": ["decision_id", "legitimacy_bonus"],
            "additionalProperties": False,
        },
    },
]

# Sanity: keep the set and the list aligned at import time.
assert {t["name"] for t in AGENT_TOOL_SCHEMAS} == AGENT_TOOL_NAMES, (
    "AGENT_TOOL_SCHEMAS and AGENT_TOOL_NAMES are out of sync"
)
