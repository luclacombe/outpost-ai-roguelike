"""Pure game engine — applies validated tool calls, handles deaths and coups.

All functions are pure: they take a ColonyState and return a new one,
never mutating the input. Per invariant #11 (see CLAUDE.md), every state
mutation in the project flows through apply_tool_call → the validator → an
effect dispatch here.

No I/O, no LLM calls, no logging.
"""

from __future__ import annotations

import functools
from uuid import UUID, uuid4

from app.game.resolution import derive_resolution_consequences  # noqa: F401  (re-export)
from app.game.schemas import (
    AdvisorStatus,
    Agent,
    ColonyState,
    EndState,
    Proposal,
    ProposalFlavor,
    Resource,
    ToolCall,
    ToolCallPhase,
)
from app.game.validator import strip_scaffold_prefix, validate_tool_call

# ---------------------------------------------------------------------------
# Section 1: Helpers
# ---------------------------------------------------------------------------


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def _agent_by_id(state: ColonyState, agent_id: UUID) -> Agent | None:
    """Return agent matching agent_id, or None."""
    for agent in state.agents:
        if agent.id == agent_id:
            return agent
    return None


_PIN_PREFIX = "died in turn "
MEMORY_CAP = 10


def _is_pinned_memory(entry: str) -> bool:
    """Death memories are pinned (never evicted by FIFO preference)."""
    return entry.startswith(_PIN_PREFIX)


def _evict_to_cap(memory: list[str], newly_added_index: int, oldest_first: bool) -> list[str]:
    """Trim memory down to MEMORY_CAP, evicting the oldest non-pinned entry first.

    Never evicts the item at newly_added_index. Falls back to evicting the
    oldest pinned entry when all other slots are pinned.
    oldest_first=True  → oldest non-pinned is the smallest index (append layout)
    oldest_first=False → oldest non-pinned is the largest index (prepend layout)
    """
    if len(memory) <= MEMORY_CAP:
        return memory

    non_pinned = [
        i for i, e in enumerate(memory) if i != newly_added_index and not _is_pinned_memory(e)
    ]
    if non_pinned:
        evict_idx = non_pinned[0] if oldest_first else non_pinned[-1]
    else:
        # All other slots are pinned — evict the oldest pinned that is not the new entry.
        candidates = [i for i in range(len(memory)) if i != newly_added_index]
        evict_idx = candidates[0] if oldest_first else candidates[-1]

    out = list(memory)
    out.pop(evict_idx)
    return out


def _pin_memory(memory: list[str], new_entry: str) -> list[str]:
    """Prepend new_entry at the head and cap to MEMORY_CAP.

    Dedupes: if new_entry already exists in memory, return memory unchanged.
    Evicts the oldest (highest-indexed) non-pinned entry first.
    """
    if new_entry in memory:
        return memory
    combined = [new_entry, *memory]
    return _evict_to_cap(combined, newly_added_index=0, oldest_first=False)


def _append_memory(memory: list[str], new_entry: str) -> list[str]:
    """FIFO-append new_entry at the tail and cap to MEMORY_CAP.

    Never drops the new entry. Evicts the oldest (lowest-indexed) non-pinned entry first.
    """
    combined = [*memory, new_entry]
    return _evict_to_cap(combined, newly_added_index=len(combined) - 1, oldest_first=True)


def _replace_agent(state: ColonyState, updated: Agent) -> ColonyState:
    """Return state with one agent replaced by updated (matched by id)."""
    new_agents = [updated if a.id == updated.id else a for a in state.agents]
    return state.model_copy(update={"agents": new_agents})


# ---------------------------------------------------------------------------
# Section 2: apply_tool_call + apply_tool_calls
# ---------------------------------------------------------------------------


def apply_tool_call(state: ColonyState, tc: ToolCall) -> ColonyState:
    """Validate tc against state; apply if valid, stamp rejection if not.

    Valid calls get `applied_delta` (what actually changed after clamping)
    for numeric tools. Always returns a new ColonyState.
    """
    result = validate_tool_call(tc, state)

    if not result.valid:
        rejected_tc = tc.model_copy(update={"rejection_reason": result.reason})
        return state.model_copy(update={"turn_tool_calls": [*state.turn_tool_calls, rejected_tc]})

    new_state = _apply_valid_effect(state, tc)
    logged = tc.model_copy(update={"applied_delta": _applied_delta(state, new_state, tc)})
    return new_state.model_copy(update={"turn_tool_calls": [*new_state.turn_tool_calls, logged]})


def _applied_delta(before: ColonyState, after: ColonyState, tc: ToolCall) -> int | None:
    """Post-clamp change for numeric tools; None for everything else."""
    name, args = tc.name, tc.args
    if name == "modify_resource":
        rid = args["resource_id"]
        return after.resources[rid].value - before.resources[rid].value
    if name in ("modify_legitimacy", "back_leader"):
        return after.legitimacy - before.legitimacy
    if name == "modify_health":
        if args["target_id"] == "player":
            return after.player_health - before.player_health
        return _health_of(after, args["target_id"]) - _health_of(before, args["target_id"])
    if name in ("adjust_grievance", "adjust_own_grievance"):
        target = args["target_id"] if name == "adjust_grievance" else str(tc.caller_id)
        return _grievance_of(after, target) - _grievance_of(before, target)
    if name == "adjust_relationship":
        assert tc.caller_id is not None
        target = UUID(args["target_id"])
        b = _agent_by_id(before, tc.caller_id)
        a = _agent_by_id(after, tc.caller_id)
        assert a is not None and b is not None
        return a.relationships.get(target, 0) - b.relationships.get(target, 0)
    return None


def _health_of(state: ColonyState, agent_id: str) -> int:
    agent = _agent_by_id(state, UUID(agent_id))
    assert agent is not None
    return agent.health


def _grievance_of(state: ColonyState, agent_id: str) -> int:
    agent = _agent_by_id(state, UUID(agent_id))
    assert agent is not None
    return agent.grievance


def stamp_phase(calls: list[ToolCall], phase: ToolCallPhase) -> list[ToolCall]:
    """Return copies of `calls` tagged with `phase`. Never mutates the input."""
    return [tc.model_copy(update={"phase": phase}) for tc in calls]


def record_rejected(state: ColonyState, tc: ToolCall, reason: str) -> ColonyState:
    """Log `tc` as rejected without validating or applying it.

    For calls the graph refuses by policy (e.g. a non-propose_action call in
    the debate phase), so the record still shows them.
    """
    rejected = tc.model_copy(update={"rejection_reason": reason})
    return state.model_copy(update={"turn_tool_calls": [*state.turn_tool_calls, rejected]})


def apply_tool_calls(state: ColonyState, tcs: list[ToolCall]) -> ColonyState:
    """Fold apply_tool_call over a list of tool calls."""
    return functools.reduce(apply_tool_call, tcs, state)


# ---------------------------------------------------------------------------
# Section 3: _apply_valid_effect dispatch
# ---------------------------------------------------------------------------


def _apply_valid_effect(state: ColonyState, tc: ToolCall) -> ColonyState:  # noqa: PLR0911
    """Dispatch to the correct effect handler for a validated tool call.

    The validator has already checked all invariants; this function only mutates.
    """
    name = tc.name
    args = tc.args

    if name == "propose_action":
        return _effect_propose_action(state, tc)
    if name == "modify_resource":
        return _effect_modify_resource(state, args)
    if name == "modify_health":
        return _effect_modify_health(state, args)
    if name == "adjust_grievance":
        return _effect_adjust_grievance(state, args)
    if name == "adjust_own_grievance":
        return _effect_adjust_own_grievance(state, tc)
    if name == "adjust_relationship":
        return _effect_adjust_relationship(state, tc, args)
    if name == "speak_to_agent":
        return _effect_speak_to_agent(state, args)
    if name == "remember":
        return _effect_remember(state, tc, args)
    if name == "initiate_coup":
        assert tc.caller_id is not None  # validator already ensured this
        return resolve_coup(state, initiator_id=tc.caller_id)
    if name == "back_leader":
        return _effect_back_leader(state, args)
    if name == "modify_legitimacy":
        return _effect_modify_legitimacy(state, args)
    if name == "set_advisor_status":
        return _effect_set_advisor_status(state, args)

    # Should be unreachable — validator would have caught unknown_tool.
    return state  # pragma: no cover


def _effect_propose_action(state: ColonyState, tc: ToolCall) -> ColonyState:
    args = tc.args
    # Validator already shape-checked tool_calls_on_pick (name in whitelist,
    # args is a dict). State-dependent rejects fire when the proposal is
    # picked and the nested calls fold through apply_tool_call again.
    nested_calls = [
        ToolCall(name=item["name"], caller_id=None, args=item["args"])
        for item in args.get("tool_calls_on_pick") or []
    ]
    proposal = Proposal(
        id=uuid4().hex[:8],
        proposed_by_agent_id=tc.caller_id,
        flavor=ProposalFlavor[args["flavor"]],
        name=strip_scaffold_prefix(args["name"]),
        description=args["description"],
        effects_preview=[],
        tool_calls_on_pick=nested_calls,
        risk_note=args.get("risk_note") if isinstance(args.get("risk_note"), str) else None,
    )
    new_proposals = [*state.turn_proposals, proposal]
    return state.model_copy(update={"turn_proposals": new_proposals})


def _effect_modify_resource(state: ColonyState, args: dict) -> ColonyState:
    resource_id: str = args["resource_id"]
    delta: int = args["delta"]
    resource: Resource = state.resources[resource_id]
    new_value = _clamp(resource.value + delta, 0, resource.max)
    updated_resource = resource.model_copy(update={"value": new_value})
    new_resources = {**state.resources, resource_id: updated_resource}
    return state.model_copy(update={"resources": new_resources})


def _effect_modify_health(state: ColonyState, args: dict) -> ColonyState:
    target_id: str = args["target_id"]
    delta: int = args["delta"]

    if target_id == "player":
        new_health = _clamp(state.player_health + delta, 0, 100)
        return state.model_copy(update={"player_health": new_health})

    # Agent target.
    agent = _agent_by_id(state, UUID(target_id))
    assert agent is not None  # validator already checked
    new_health = _clamp(agent.health + delta, 0, 100)
    updated = agent.model_copy(update={"health": new_health})
    return _replace_agent(state, updated)


def _effect_adjust_grievance(state: ColonyState, args: dict) -> ColonyState:
    target_id = UUID(args["target_id"])
    delta: int = args["delta"]
    agent = _agent_by_id(state, target_id)
    assert agent is not None  # validator already checked
    new_grievance = _clamp(agent.grievance + delta, 0, 10)
    updated = agent.model_copy(update={"grievance": new_grievance})
    return _replace_agent(state, updated)


def _effect_adjust_own_grievance(state: ColonyState, tc: ToolCall) -> ColonyState:
    assert tc.caller_id is not None  # validator already checked
    delta: int = tc.args["delta"]
    agent = _agent_by_id(state, tc.caller_id)
    assert agent is not None  # validator already checked
    new_grievance = _clamp(agent.grievance + delta, 0, 10)
    updated = agent.model_copy(update={"grievance": new_grievance})
    return _replace_agent(state, updated)


def _effect_adjust_relationship(state: ColonyState, tc: ToolCall, args: dict) -> ColonyState:
    assert tc.caller_id is not None  # validator already checked
    target_id = UUID(args["target_id"])
    delta: int = args["delta"]
    caller = _agent_by_id(state, tc.caller_id)
    assert caller is not None  # validator already checked
    current = caller.relationships.get(target_id, 0)
    new_score = _clamp(current + delta, -10, 10)
    new_relationships = {**caller.relationships, target_id: new_score}
    updated = caller.model_copy(update={"relationships": new_relationships})
    return _replace_agent(state, updated)


def _effect_speak_to_agent(state: ColonyState, args: dict) -> ColonyState:
    target_id = UUID(args["target_id"])
    message: str = args["message"][:200]  # truncate to 200 chars
    agent = _agent_by_id(state, target_id)
    assert agent is not None  # validator already checked
    new_memory = _append_memory(agent.memory, message)
    updated = agent.model_copy(update={"memory": new_memory})
    return _replace_agent(state, updated)


def _effect_remember(state: ColonyState, tc: ToolCall, args: dict) -> ColonyState:
    assert tc.caller_id is not None  # validator already checked
    fact: str = args["fact"]
    caller = _agent_by_id(state, tc.caller_id)
    assert caller is not None  # validator already checked
    new_memory = _append_memory(caller.memory, fact)
    updated = caller.model_copy(update={"memory": new_memory})
    return _replace_agent(state, updated)


def _effect_back_leader(state: ColonyState, args: dict) -> ColonyState:
    bonus: int = args["legitimacy_bonus"]
    new_legitimacy = _clamp(state.legitimacy + bonus, 0, 100)
    return state.model_copy(update={"legitimacy": new_legitimacy})


def _effect_modify_legitimacy(state: ColonyState, args: dict) -> ColonyState:
    delta: int = args["delta"]
    new_legitimacy = _clamp(state.legitimacy + delta, 0, 100)
    return state.model_copy(update={"legitimacy": new_legitimacy})


def _effect_set_advisor_status(state: ColonyState, args: dict) -> ColonyState:
    target_id = UUID(args["target_id"])
    new_status = AdvisorStatus(args["new_status"])
    agent = _agent_by_id(state, target_id)
    assert agent is not None  # validator already checked
    updated = agent.model_copy(update={"status": new_status})
    return _replace_agent(state, updated)


# ---------------------------------------------------------------------------
# Section 4: check_deaths
# ---------------------------------------------------------------------------


def check_deaths(state: ColonyState) -> ColonyState:
    """Mark agents with health <= 0 as dead, pin death memories, check player death.

    Pure. Invariant #3: once dead, never revived.
    """
    updated_agents = list(state.agents)
    death_tags: list[str] = []

    # First pass: find agents that just died (health <= 0 AND is_alive).
    for i, agent in enumerate(updated_agents):
        if agent.health <= 0 and agent.is_alive:
            death_tag = f"died in turn {state.turn_number}"
            death_tags.append(death_tag)
            # Mark dead, pin death memory to their own memory first.
            new_memory = _pin_memory(agent.memory, death_tag)
            updated_agents[i] = agent.model_copy(
                update={"is_alive": False, "health": 0, "memory": new_memory}
            )

    # Second pass: pin death memories to every *surviving* agent.
    if death_tags:
        for i, agent in enumerate(updated_agents):
            if agent.is_alive:
                current_memory = updated_agents[i].memory
                for death_tag in death_tags:
                    current_memory = _pin_memory(current_memory, death_tag)
                updated_agents[i] = updated_agents[i].model_copy(update={"memory": current_memory})

    new_state = state.model_copy(update={"agents": updated_agents})

    # Check player death.
    if new_state.player_health <= 0 and new_state.end_state is None:
        new_state = new_state.model_copy(update={"end_state": EndState.KILLED})

    return new_state


# ---------------------------------------------------------------------------
# Section 5: resolve_coup
# ---------------------------------------------------------------------------


def resolve_coup(state: ColonyState, initiator_id: UUID) -> ColonyState:
    """Resolve a coup attempt. Rule-based. No LLM, no randomness.

    Per ARCHITECTURE §3.5 and SPEC §5.1.
    Defensive: if end_state is already set, return state unchanged.
    """
    if state.end_state is not None:
        return state

    initiator = _agent_by_id(state, initiator_id)
    if initiator is None:
        return state  # should not happen post-validation

    co_conspirators = [
        a for a in state.agents if a.is_alive and a.grievance >= 7 and a.id != initiator_id
    ]

    total_boldness = initiator.temperament + sum(a.temperament for a in co_conspirators)
    max_accomplice_grievance = max((a.grievance for a in co_conspirators), default=0)

    kills = (
        initiator.grievance >= 9
        and max_accomplice_grievance >= 9
        and total_boldness > 0.5
        and state.legitimacy < 15
    )

    end = EndState.KILLED if kills else EndState.OUSTED
    return state.model_copy(update={"end_state": end})


# ---------------------------------------------------------------------------
# Section 6: advance_turn + close_turn + compute_end_state
# ---------------------------------------------------------------------------


def compute_end_state(state: ColonyState) -> EndState | None:
    """Derive end state from current colony state. Priority-ordered.

    Ousted/Killed via coup are set directly by resolve_coup; this function
    does not reassign them.
    """
    # 1. Already set — idempotent.
    if state.end_state is not None:
        return state.end_state

    # 2. Player dead.
    if state.player_health <= 0:
        return EndState.KILLED

    # 3. All agents dead.
    live_agents = [a for a in state.agents if a.is_alive]
    live_count = len(live_agents)
    if live_count == 0:
        return EndState.COLLAPSED

    # 4. Critical resource at 0 (Phase 3 approximation; 2-turn gating deferred to Phase 4/5).
    for resource in state.resources.values():
        if resource.value == 0:
            return EndState.COLLAPSED

    # 5. Turn limit reached.
    if state.turn_number >= state.max_turns:
        all_resources_ok = all(r.value >= r.critical_threshold for r in state.resources.values())
        if live_count >= 2 and all_resources_ok and state.legitimacy >= 20:
            return EndState.ENDURED
        return EndState.COLLAPSED

    # 6. Run continues.
    return None


CLOSE_LEGITIMACY_DELTA = -1
CLOSE_REASON = "end of turn"


def close_turn(state: ColonyState) -> ColonyState:
    """End-of-turn passive legitimacy decay, as a validated tool call.

    Runs every turn, including one a coup or death already ended, as the
    decay always has.
    """
    tc = ToolCall(
        name="modify_legitimacy",
        caller_id=None,
        args={"delta": CLOSE_LEGITIMACY_DELTA, "reason": CLOSE_REASON},
        phase="close",
    )
    return apply_tool_call(state, tc)


def advance_turn(state: ColonyState) -> ColonyState:
    """Increment the turn, evaluate end state, clear per-turn fields.

    Passive decay lives in `close_turn` (call it first).
    """
    partial = state.model_copy(update={"turn_number": state.turn_number + 1})
    return partial.model_copy(
        update={
            "end_state": compute_end_state(partial),
            "current_event": None,
            "turn_proposals": [],
            "turn_tool_calls": [],
        }
    )
