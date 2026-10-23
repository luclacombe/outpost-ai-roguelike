"""`submit_debate`: one forced tool call holding reactions and three options.

Many cheap models fail the older shape (free-text `<agent>` reactions plus three
parallel `propose_action` calls with nested effects). The flat shape puts every
reaction and option in one tool input. `parse_submit_debate` rebuilds the
existing `DebateParse` (`per_agent_text` plus `propose_action` ToolCalls with
nested `tool_calls_on_pick`), so the engine, validator and `prepare_proposals`
stay unchanged. The effect `reason` the validator requires is not asked of the
model; the parser supplies it from the option title.

Each option carries two effect lists, `resource_effects` (`{resource, delta}`)
and `person_effects` (`{kind, target, delta}`), with every item field required
in the schema. A single mixed list with optional `resource`/`target` let the
model send effects without the field the engine needs. The parser only
reshapes both lists into the nested engine calls; it never repairs or guesses a
value (the validator decides).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.agent.narration_guard import has_unknown_non_latin, non_latin_words
from app.agent.prompts import DebateParse
from app.game.schemas import Agent, ToolCall

logger = logging.getLogger(__name__)

DEBATE_TOOL_NAME = "submit_debate"
_FLAVORS = ["DOMINANT", "MORAL", "WILDCARD"]
_REASON_MAX = 60
_MAX_EFFECTS = 5
_PERSON_KINDS = ("health", "grievance")


def build_debate_tool(alive_agents: list[Agent], resource_ids: list[str]) -> dict[str, Any]:
    """Tool schema whose enums pin advisor names and resource ids to this colony."""
    names = [a.name for a in alive_agents]
    resource_effect: dict[str, Any] = {
        "type": "object",
        "required": ["resource", "delta"],
        "properties": {
            "resource": {"type": "string", "enum": list(resource_ids)},
            "delta": {"type": "integer", "description": "Signed integer change."},
        },
    }
    person_effect: dict[str, Any] = {
        "type": "object",
        "required": ["kind", "target", "delta"],
        "properties": {
            "kind": {"type": "string", "enum": list(_PERSON_KINDS)},
            "target": {
                "type": "string",
                "enum": [*names, "player"],
                "description": "An advisor name, or player (health only).",
            },
            "delta": {"type": "integer", "description": "Signed integer change."},
        },
    }
    option: dict[str, Any] = {
        "type": "object",
        "required": [
            "flavor",
            "advisor",
            "title",
            "description",
            "resource_effects",
            "person_effects",
        ],
        "properties": {
            "flavor": {"type": "string", "enum": _FLAVORS},
            "advisor": {"type": "string", "enum": names},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "risk_note": {"type": "string"},
            "resource_effects": {
                "type": "array",
                "maxItems": _MAX_EFFECTS,
                "items": resource_effect,
                "description": (
                    "Changes to colony resources. Use [] if none; give at least one effect "
                    "across resource_effects and person_effects."
                ),
            },
            "person_effects": {
                "type": "array",
                "maxItems": 3,
                "items": person_effect,
                "description": (
                    "Changes to advisor or player health or advisor grievance. Use [] if none; "
                    "give at least one effect across resource_effects and person_effects."
                ),
            },
        },
    }
    return {
        "name": DEBATE_TOOL_NAME,
        "description": (
            "Submit this turn's debate: one reaction per living advisor and exactly three options."
        ),
        "input_schema": {
            "type": "object",
            "required": ["reactions", "options"],
            "properties": {
                "reactions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["advisor", "line"],
                        "properties": {
                            "advisor": {"type": "string", "enum": names},
                            "line": {"type": "string"},
                        },
                    },
                },
                "options": {"type": "array", "minItems": 3, "maxItems": 3, "items": option},
            },
        },
    }


def _as_list(value: Any) -> list[Any]:
    """Model output is untrusted: anything that is not a list counts as empty."""
    return value if isinstance(value, list) else []


def _resource_call(effect: dict[str, Any], reason: str) -> dict[str, Any]:
    args = {"resource_id": effect.get("resource"), "delta": effect.get("delta"), "reason": reason}
    return {"name": "modify_resource", "args": args}


def _person_call(
    effect: dict[str, Any], by_name: dict[str, Agent], reason: str
) -> dict[str, Any] | None:
    kind = effect.get("kind")
    if kind not in _PERSON_KINDS:
        return None
    target = str(effect.get("target", ""))
    if target.lower() == "player":
        target_id = "player"
    elif target.lower() in by_name:
        target_id = str(by_name[target.lower()].id)
    else:
        target_id = target
    name = "modify_health" if kind == "health" else "adjust_grievance"
    return {
        "name": name,
        "args": {"target_id": target_id, "delta": effect.get("delta"), "reason": reason},
    }


def _nested_calls(
    option: dict[str, Any], by_name: dict[str, Agent], reason: str
) -> list[dict[str, Any]]:
    """Resource calls first, then person calls, capped at `_MAX_EFFECTS`."""
    calls = [
        _resource_call(e, reason)
        for e in _as_list(option.get("resource_effects"))
        if isinstance(e, dict)
    ]
    for effect in _as_list(option.get("person_effects")):
        call = _person_call(effect, by_name, reason) if isinstance(effect, dict) else None
        if call is not None:
            calls.append(call)
    return calls[:_MAX_EFFECTS]


def _debate_payload(response: Any) -> dict[str, Any]:
    """Input of the first `submit_debate` tool_use block, or {} when absent or malformed."""
    blocks = [
        b
        for b in _as_list(getattr(response, "content", None))
        if getattr(b, "type", "") == "tool_use" and getattr(b, "name", "") == DEBATE_TOOL_NAME
    ]
    raw = getattr(blocks[0], "input", None) if blocks else None
    return raw if isinstance(raw, dict) else {}


def incomplete_effects(response: Any) -> int:
    """Count effects in the first `submit_debate` input the parser cannot use as sent.

    A `resource_effects` entry needs a non-empty `resource`; a `person_effects`
    entry needs a `kind` of health or grievance and a non-empty `target`; an
    entry that is not an object counts too. Diagnostic only: nothing is dropped
    or repaired here (the engine validator decides). Each one logs a warning
    with its sorted key names (never values), so a sweep shows whether the model
    omitted the field or used another key. Never raises.
    """
    count = 0
    for option in _as_list(_debate_payload(response).get("options")):
        if not isinstance(option, dict):
            continue
        for list_name in ("resource_effects", "person_effects"):
            for effect in _as_list(option.get(list_name)):
                if not isinstance(effect, dict):
                    count += 1
                    logger.warning(
                        "submit_debate: %s entry is %s, not an object",
                        list_name,
                        type(effect).__name__,
                    )
                    continue
                keys = sorted(str(k) for k in effect)
                if list_name == "resource_effects":
                    if effect.get("resource"):
                        continue
                    msg, args = "submit_debate: resource effect lacks 'resource'; keys sent: %s", ()
                elif effect.get("kind") not in _PERSON_KINDS:
                    msg = "submit_debate: person effect has kind %r; keys sent: %s"
                    args = (effect.get("kind"),)
                elif effect.get("target"):
                    continue
                else:
                    msg, args = "submit_debate: person effect lacks 'target'; keys sent: %s", ()
                count += 1
                logger.warning(msg, *args, keys)
    return count


def parse_submit_debate(
    response: Any,
    alive_agents: list[Agent],
    *,
    allowed_terms: frozenset[str] = frozenset(),
) -> DebateParse:
    """Map the first `submit_debate` tool_use block to a `DebateParse`.

    Unknown advisor names drop a reaction and leave an option's caller_id None.
    Reactions and options containing a non-Latin word that is not in
    `allowed_terms` (the setting's own vocabulary plus this turn's event, from
    `build_allowed_terms`) are dropped with a warning. A response without the
    tool call parses to an empty `DebateParse`.
    """
    allowed_words = non_latin_words(allowed_terms)
    by_name = {a.name.lower(): a for a in alive_agents}
    payload = _debate_payload(response)

    per_agent_text: dict[UUID, str] = {}
    for reaction in _as_list(payload.get("reactions")):
        if not isinstance(reaction, dict):
            continue
        agent = by_name.get(str(reaction.get("advisor", "")).lower())
        line = reaction.get("line")
        if agent is None or not line:
            continue
        if has_unknown_non_latin(str(line), allowed_words):
            logger.warning("submit_debate: dropped non-Latin reaction from %s", agent.name)
            continue
        per_agent_text[agent.id] = str(line)

    tool_calls: list[ToolCall] = []
    for option in _as_list(payload.get("options")):
        if not isinstance(option, dict):
            continue
        title = str(option.get("title") or "option")
        description = str(option.get("description") or "")
        if has_unknown_non_latin(title, allowed_words) or has_unknown_non_latin(
            description, allowed_words
        ):
            logger.warning("submit_debate: dropped non-Latin option %r", title[:_REASON_MAX])
            continue
        agent = by_name.get(str(option.get("advisor", "")).lower())
        reason = title[:_REASON_MAX]
        nested = _nested_calls(option, by_name, reason)
        tool_calls.append(
            ToolCall(
                name="propose_action",
                caller_id=agent.id if agent else None,
                args={
                    "name": option.get("title"),
                    "description": option.get("description"),
                    "flavor": option.get("flavor"),
                    "risk_note": option.get("risk_note") or "",
                    "tool_calls_on_pick": nested,
                },
            )
        )
    return DebateParse(per_agent_text=per_agent_text, tool_calls=tool_calls)


OFFTURN_TOOL_NAME = "submit_offturn"
_OFFTURN_KINDS = [
    "remember",
    "adjust_own_grievance",
    "speak_to_agent",
    "adjust_relationship",
    "back_leader",
    "initiate_coup",
]
_OFFTURN_MAX_ACTIONS = 8
_FACT_MAX = 200
_MESSAGE_MAX = 300


def build_offturn_tool(alive_agents: list[Agent]) -> dict[str, Any]:
    """Tool schema for the autonomous phase: zero or more advisor actions in one call."""
    names = [a.name for a in alive_agents]
    action: dict[str, Any] = {
        "type": "object",
        "required": ["advisor", "kind"],
        "properties": {
            "advisor": {
                "type": "string",
                "enum": names,
                "description": "The advisor taking the action.",
            },
            "kind": {
                "type": "string",
                "enum": list(_OFFTURN_KINDS),
                "description": "Which off-turn action.",
            },
            "text": {
                "type": "string",
                "description": (
                    "Required for remember (the memory), speak_to_agent (the private "
                    "message) and adjust_own_grievance (the reason). Those actions are "
                    "rejected without it."
                ),
            },
            "target": {
                "type": "string",
                "enum": names,
                "description": (
                    "Required for speak_to_agent and adjust_relationship: the other advisor."
                ),
            },
            "delta": {
                "type": "integer",
                "description": (
                    "Required for adjust_own_grievance and adjust_relationship: an integer "
                    "from -3 to 3. For back_leader, the legitimacy bonus from 1 to 5."
                ),
            },
        },
    }
    return {
        "name": OFFTURN_TOOL_NAME,
        "description": (
            "Submit this off-turn's advisor actions, one action per entry. "
            "An empty list is fine when nobody acts."
        ),
        "input_schema": {
            "type": "object",
            "required": ["actions"],
            "properties": {
                "actions": {
                    "type": "array",
                    "maxItems": _OFFTURN_MAX_ACTIONS,
                    "items": action,
                },
            },
        },
    }


def _offturn_args(
    kind: str,
    item: dict[str, Any],
    by_name: dict[str, Agent],
    decision_id: str | None,
) -> dict[str, Any] | None:
    """Engine tool args for one action, or None when the action must be skipped."""
    text = str(item.get("text") or "")
    delta = item.get("delta")
    if kind == "remember":
        return {"fact": text[:_FACT_MAX]}
    if kind == "adjust_own_grievance":
        return {"delta": delta, "reason": text[:_FACT_MAX]}
    if kind == "initiate_coup":
        return {}
    if kind == "back_leader":
        if decision_id is None:
            return None
        bonus = delta if isinstance(delta, int) and not isinstance(delta, bool) else 1
        return {"decision_id": decision_id, "legitimacy_bonus": min(max(bonus or 1, 1), 5)}
    if kind in ("speak_to_agent", "adjust_relationship"):
        target = by_name.get(str(item.get("target", "")).lower())
        if target is None:
            return None
        if kind == "speak_to_agent":
            return {"target_id": str(target.id), "message": text[:_MESSAGE_MAX]}
        return {"target_id": str(target.id), "delta": delta}
    return None


def parse_submit_offturn(
    response: Any,
    alive_agents: list[Agent],
    decision_id: str | None,
    *,
    allowed_terms: frozenset[str] = frozenset(),
) -> list[ToolCall]:
    """Map the first `submit_offturn` tool_use block to engine `ToolCall`s.

    Skipped with a warning: unknown advisor or kind, a missing or unknown target
    where one is needed, `back_leader` without a decision id, and text with a
    non-Latin word outside `allowed_terms` (see `parse_submit_debate`). A response without the tool call, or with a non-list
    `actions`, parses to no calls. Never raises on malformed input.
    """
    by_name = {a.name.lower(): a for a in alive_agents}
    blocks = [
        b
        for b in _as_list(getattr(response, "content", None))
        if getattr(b, "type", "") == "tool_use" and getattr(b, "name", "") == OFFTURN_TOOL_NAME
    ]
    raw = getattr(blocks[0], "input", None) if blocks else None
    payload = raw if isinstance(raw, dict) else {}
    allowed_words = non_latin_words(allowed_terms)

    calls: list[ToolCall] = []
    for item in _as_list(payload.get("actions")):
        if not isinstance(item, dict):
            continue
        agent = by_name.get(str(item.get("advisor", "")).lower())
        kind = str(item.get("kind", ""))
        if agent is None or kind not in _OFFTURN_KINDS:
            logger.warning("submit_offturn: dropped action %r from %r", kind, item.get("advisor"))
            continue
        if has_unknown_non_latin(str(item.get("text") or ""), allowed_words):
            logger.warning("submit_offturn: dropped non-Latin %s from %s", kind, agent.name)
            continue
        args = _offturn_args(kind, item, by_name, decision_id)
        if args is None:
            logger.warning(
                "submit_offturn: skipped %s from %s (missing target/decision)", kind, agent.name
            )
            continue
        calls.append(ToolCall(name=kind, caller_id=agent.id, args=args))
    return calls
