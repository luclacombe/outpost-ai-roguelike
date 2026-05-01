"""
Pure tool-call validator — the gate for every state mutation in Outpost.

Called from apply_tool_calls nodes (Phase 4) and apply_tool_call helpers
(Task 3.6). Never mutates state, never raises, never does I/O.

All failures flow through ValidationResult(valid=False, reason=...).
Reason strings are snake_case so the eval metric and tests can match on them
without fragile substring searches.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.game.schemas import Agent, ColonyState, ToolCall
from app.game.tools import KNOWN_TOOL_NAMES

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ValidationResult:
    valid: bool
    reason: str | None = None  # None when valid; human-readable snake_case when not


_OK = ValidationResult(valid=True)


def _fail(reason: str) -> ValidationResult:
    return ValidationResult(valid=False, reason=reason)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_agent_by_id(state: ColonyState, target_id: str) -> Agent | None:
    """Return the Agent whose UUID string matches target_id, or None."""
    for agent in state.agents:
        if str(agent.id) == target_id:
            return agent
    return None


def _require_reason(args: dict[str, Any], max_len: int = 200) -> str | None:
    """Return failure reason string if reason is missing/empty/too long, else None."""
    reason = args.get("reason")
    if not reason or not isinstance(reason, str) or not reason.strip():
        return "missing_reason"
    if len(reason) > max_len:
        return "missing_reason"
    return None


def _require_integer_delta(args: dict[str, Any], lo: int, hi: int) -> tuple[int | None, str | None]:
    """Return (delta, None) if valid, or (None, failure_reason)."""
    delta = args.get("delta")
    if delta is None or not isinstance(delta, int) or isinstance(delta, bool):
        return None, "missing_delta"
    if not (lo <= delta <= hi):
        return None, "delta_out_of_range"
    return delta, None


# ---------------------------------------------------------------------------
# Per-tool validators  (one private function each)
# ---------------------------------------------------------------------------


_NESTED_TOOL_WHITELIST: frozenset[str] = frozenset(
    {"modify_resource", "modify_health", "adjust_grievance"}
)


def _validate_propose_action(tc: ToolCall, state: ColonyState) -> ValidationResult:
    args = tc.args
    name = args.get("name")
    if not name or not isinstance(name, str) or not name.strip():
        return _fail("missing_name")
    description = args.get("description")
    if not description or not isinstance(description, str) or not description.strip():
        return _fail("missing_description")
    flavor = args.get("flavor")
    if flavor not in {"DOMINANT", "MORAL", "WILDCARD"}:
        return _fail("invalid_flavor")

    # Duplicate-proposal check (case-insensitive, stripped)
    normalised = name.strip().lower()
    for proposal in state.turn_proposals:
        if proposal.name.strip().lower() == normalised:
            return _fail("duplicate_proposal")

    # Shape-only check on nested tool_calls_on_pick. State-dependent
    # validation (e.g. resource underflow at pick time) re-runs through
    # apply_tool_call when the proposal is chosen; we only guard the
    # per-item shape and tool-name whitelist here.
    nested = args.get("tool_calls_on_pick")
    if nested is not None:
        if not isinstance(nested, list):
            return _fail("nested_tool_malformed")
        for item in nested:
            if not isinstance(item, dict):
                return _fail("nested_tool_malformed")
            if not isinstance(item.get("name"), str) or not isinstance(item.get("args"), dict):
                return _fail("nested_tool_malformed")
            if item["name"] not in _NESTED_TOOL_WHITELIST:
                return _fail("nested_tool_not_allowed")

    return _OK


def _validate_modify_resource(tc: ToolCall, state: ColonyState) -> ValidationResult:
    args = tc.args
    resource_id = args.get("resource_id")
    resource = state.resources.get(resource_id) if isinstance(resource_id, str) else None
    if resource is None:
        return _fail("unknown_resource")

    delta = args.get("delta")
    if delta is None or not isinstance(delta, int) or isinstance(delta, bool):
        return _fail("missing_delta")

    if abs(delta) > 50:
        return _fail("delta_out_of_range")

    fail_reason = _require_reason(args)
    if fail_reason:
        return _fail(fail_reason)

    projected = resource.value + delta
    if projected < 0:
        return _fail("would_underflow")
    if projected > resource.max:
        return _fail("would_overflow")

    return _OK


def _validate_modify_health(tc: ToolCall, state: ColonyState) -> ValidationResult:
    args = tc.args
    target_id = args.get("target_id")

    fail_reason = _require_reason(args)
    if fail_reason:
        return _fail(fail_reason)

    delta = args.get("delta")
    if delta is None or not isinstance(delta, int) or isinstance(delta, bool):
        return _fail("delta_out_of_range")
    if not (-100 <= delta <= 100):
        return _fail("delta_out_of_range")

    if target_id == "player":
        if state.player_health <= 0 and delta < 0:
            return _fail("target_already_dead")
        return _OK

    agent = _get_agent_by_id(state, str(target_id)) if target_id is not None else None
    if agent is None:
        return _fail("unknown_target")

    if not agent.is_alive and delta < 0:
        return _fail("target_already_dead")

    return _OK


def _validate_adjust_grievance(tc: ToolCall, state: ColonyState) -> ValidationResult:
    args = tc.args
    target_id = args.get("target_id")

    agent = _get_agent_by_id(state, str(target_id)) if target_id is not None else None
    if agent is None:
        return _fail("unknown_target")

    if tc.caller_id is not None and str(tc.caller_id) == str(target_id):
        return _fail("cannot_self_target")

    if not agent.is_alive:
        return _fail("target_dead")

    delta, fail = _require_integer_delta(args, -3, 3)
    if fail:
        return _fail(fail)

    fail_reason = _require_reason(args)
    if fail_reason:
        return _fail(fail_reason)

    return _OK


def _validate_speak_to_agent(tc: ToolCall, state: ColonyState) -> ValidationResult:
    args = tc.args

    if tc.caller_id is None:
        return _fail("missing_caller")

    target_id = args.get("target_id")
    agent = _get_agent_by_id(state, str(target_id)) if target_id is not None else None
    if agent is None or not agent.is_alive:
        return _fail("target_unknown_or_dead")

    message = args.get("message")
    if not message or not isinstance(message, str) or not message.strip():
        return _fail("missing_message")
    if len(message) > 300:
        return _fail("message_too_long")

    return _OK


def _validate_adjust_relationship(tc: ToolCall, state: ColonyState) -> ValidationResult:
    args = tc.args
    target_id = args.get("target_id")

    agent = _get_agent_by_id(state, str(target_id)) if target_id is not None else None
    if agent is None:
        return _fail("unknown_target")

    if tc.caller_id is not None and str(tc.caller_id) == str(target_id):
        return _fail("cannot_self_target")

    if not agent.is_alive:
        return _fail("target_dead")

    delta, fail = _require_integer_delta(args, -3, 3)
    if fail:
        return _fail(fail)

    return _OK


def _validate_adjust_own_grievance(tc: ToolCall, state: ColonyState) -> ValidationResult:
    args = tc.args

    if tc.caller_id is None:
        return _fail("missing_caller")

    caller = _get_agent_by_id(state, str(tc.caller_id))
    if caller is None:
        return _fail("missing_caller")

    if not caller.is_alive:
        return _fail("caller_dead")

    delta, fail = _require_integer_delta(args, -3, 3)
    if fail:
        return _fail(fail)

    fail_reason = _require_reason(args)
    if fail_reason:
        return _fail(fail_reason)

    return _OK


def _validate_remember(tc: ToolCall, state: ColonyState) -> ValidationResult:
    args = tc.args

    if tc.caller_id is None:
        return _fail("missing_caller")

    caller = _get_agent_by_id(state, str(tc.caller_id))
    if caller is None or not caller.is_alive:
        return _fail("missing_caller")

    fact = args.get("fact")
    if not fact or not isinstance(fact, str) or not fact.strip():
        return _fail("missing_fact")
    if len(fact) > 200:
        return _fail("fact_too_long")

    return _OK


def _validate_initiate_coup(tc: ToolCall, state: ColonyState) -> ValidationResult:
    if tc.caller_id is None:
        return _fail("missing_caller")

    caller = _get_agent_by_id(state, str(tc.caller_id))
    if caller is None or not caller.is_alive:
        return _fail("missing_caller")

    if state.end_state is not None:
        return _fail("run_already_ended")

    if caller.grievance < 9:
        return _fail("grievance_too_low")

    supporters = [a for a in state.agents if a.is_alive and a.id != caller.id and a.grievance >= 7]
    if not supporters:
        return _fail("no_faction_support")

    return _OK


def _validate_back_leader(tc: ToolCall, state: ColonyState) -> ValidationResult:
    if tc.caller_id is None:
        return _fail("missing_caller")

    caller = _get_agent_by_id(state, str(tc.caller_id))
    if caller is None or not caller.is_alive:
        return _fail("missing_caller")

    if caller.grievance >= 3:
        return _fail("grievance_too_high")

    args = tc.args
    decision_id = args.get("decision_id")
    if not decision_id or not isinstance(decision_id, str) or not decision_id.strip():
        return _fail("missing_decision_id")

    legitimacy_bonus = args.get("legitimacy_bonus")
    if (
        legitimacy_bonus is None
        or not isinstance(legitimacy_bonus, int)
        or isinstance(legitimacy_bonus, bool)
        or not (1 <= legitimacy_bonus <= 5)
    ):
        return _fail("invalid_legitimacy_bonus")

    return _OK


def _validate_modify_legitimacy(tc: ToolCall, state: ColonyState) -> ValidationResult:
    """Engine-only tool. Rejects any caller_id."""
    if tc.caller_id is not None:
        return _fail("engine_only_tool")

    args = tc.args
    delta = args.get("delta")
    if delta is None or not isinstance(delta, int) or isinstance(delta, bool):
        return _fail("missing_delta")
    if not (-100 <= delta <= 100):
        return _fail("delta_out_of_range")

    fail_reason = _require_reason(args)
    if fail_reason:
        return _fail(fail_reason)

    return _OK


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

_VALIDATORS = {
    "propose_action": _validate_propose_action,
    "modify_resource": _validate_modify_resource,
    "modify_health": _validate_modify_health,
    "adjust_grievance": _validate_adjust_grievance,
    "speak_to_agent": _validate_speak_to_agent,
    "adjust_relationship": _validate_adjust_relationship,
    "adjust_own_grievance": _validate_adjust_own_grievance,
    "remember": _validate_remember,
    "initiate_coup": _validate_initiate_coup,
    "back_leader": _validate_back_leader,
    "modify_legitimacy": _validate_modify_legitimacy,
}


def validate_tool_call(tc: ToolCall, state: ColonyState) -> ValidationResult:
    """Pure function: (tool_call, state) → ValidationResult.

    Never mutates state. Never raises; all failures flow through
    ValidationResult(valid=False, reason=...).
    """
    if tc.name not in KNOWN_TOOL_NAMES:
        return _fail("unknown_tool")

    validator = _VALIDATORS[tc.name]
    return validator(tc, state)
