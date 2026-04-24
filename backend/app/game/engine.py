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

from app.game.schemas import (
    Agent,
    ColonyState,
    EndState,
    Proposal,
    ProposalFlavor,
    Resource,
    ToolCall,
)
from app.game.validator import validate_tool_call

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


def _is_pinned_memory(entry: str) -> bool:
    """Death memories are pinned (never evicted by FIFO)."""
    return entry.startswith("died in turn ")


def _pin_memory(memory: list[str], new_entry: str) -> list[str]:
    """Prepend new_entry as a pinned memory. FIFO eviction applies only to non-pinned tail.

    The result is: [new_entry, ...pinned, ...non-pinned (evicted if total > 10)]
    """
    # Separate existing entries into pinned head entries and non-pinned tail.
    # Pinned entries are those that start with "died in turn ".
    # Strategy: new_entry goes at head; then we reconstruct the list keeping the
    # cap at 10, evicting oldest non-pinned entries first.
    combined = [new_entry, *memory]
    if len(combined) <= 10:
        return combined

    # Need to evict.  Find non-pinned entries from oldest (end) to newest.
    pinned = [e for e in combined if _is_pinned_memory(e)]
    non_pinned = [e for e in combined if not _is_pinned_memory(e)]

    # Evict from the tail of non_pinned until total fits.
    target_non_pinned = 10 - len(pinned)
    if target_non_pinned < 0:
        target_non_pinned = 0
    non_pinned = non_pinned[:target_non_pinned]

    # Reconstruct: new pinned entry first (it IS pinned because it starts
    # with "died in turn "), then remaining entries in original relative order.
    # Re-merge preserving original ordering of non-new entries.
    result: list[str] = []
    # Rebuild from combined, keeping only entries that survived eviction.
    surviving_non_new = set()
    for e in non_pinned:
        surviving_non_new.add(id(e))

    # Walk combined: first entry is new_entry (always kept), rest filter by survival.
    result.append(combined[0])  # new_entry always kept
    for e in combined[1:]:
        if _is_pinned_memory(e) or id(e) in surviving_non_new:
            result.append(e)

    return result


def _append_memory(memory: list[str], new_entry: str) -> list[str]:
    """Append new_entry to memory with FIFO cap at 10 (never evicts pinned).

    Used for non-death memories (speak_to_agent, remember).
    New entry is appended at the end; oldest non-pinned entry is evicted if over cap.
    """
    combined = [*memory, new_entry]
    if len(combined) <= 10:
        return combined

    # Evict oldest non-pinned entries from the start of the list.
    # Walk from index 0 forward; remove the first non-pinned entry.
    for i, entry in enumerate(combined):
        if not _is_pinned_memory(entry):
            combined.pop(i)
            break

    return combined


def _replace_agent(state: ColonyState, updated: Agent) -> ColonyState:
    """Return state with one agent replaced by updated (matched by id)."""
    new_agents = [updated if a.id == updated.id else a for a in state.agents]
    return state.model_copy(update={"agents": new_agents})


# ---------------------------------------------------------------------------
# Section 2: apply_tool_call + apply_tool_calls
# ---------------------------------------------------------------------------


def apply_tool_call(state: ColonyState, tc: ToolCall) -> ColonyState:
    """Validate tc against state; apply if valid, stamp rejection if not.

    Always returns a new ColonyState. Never mutates the input.
    """
    result = validate_tool_call(tc, state)

    if not result.valid:
        # Stamp rejection reason on the tool call record and append to log.
        rejected_tc = tc.model_copy(update={"rejection_reason": result.reason})
        new_calls = [*state.turn_tool_calls, rejected_tc]
        return state.model_copy(update={"turn_tool_calls": new_calls})

    # Valid — apply effect then append the (non-rejected) tc.
    new_state = _apply_valid_effect(state, tc)
    new_calls = [*new_state.turn_tool_calls, tc]
    return new_state.model_copy(update={"turn_tool_calls": new_calls})


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

    # Should be unreachable — validator would have caught unknown_tool.
    return state  # pragma: no cover


def _effect_propose_action(state: ColonyState, tc: ToolCall) -> ColonyState:
    args = tc.args
    proposal = Proposal(
        id=uuid4().hex[:8],
        proposed_by_agent_id=tc.caller_id,
        flavor=ProposalFlavor[args["flavor"]],
        name=args["name"],
        description=args["description"],
        effects_preview=args.get("effects_preview", []),
        tool_calls_on_pick=[],  # wired in Phase 4
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
            death_tags.append((agent.id, death_tag))  # type: ignore[arg-type]
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
                for _dead_id, death_tag in death_tags:
                    current_memory = _pin_memory(current_memory, death_tag)
                updated_agents[i] = updated_agents[i].model_copy(
                    update={"memory": current_memory}
                )

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
        a
        for a in state.agents
        if a.is_alive and a.grievance >= 7 and a.id != initiator_id
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
# Section 6: advance_turn + compute_end_state
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
        all_resources_ok = all(
            r.value >= r.critical_threshold for r in state.resources.values()
        )
        if live_count >= 2 and all_resources_ok and state.legitimacy >= 20:
            return EndState.ENDURED
        return EndState.COLLAPSED

    # 6. Run continues.
    return None


def advance_turn(state: ColonyState) -> ColonyState:
    """Increment turn counter, apply passive legitimacy decay, evaluate end state, clear per-turn fields."""
    new_turn = state.turn_number + 1
    new_legitimacy = _clamp(state.legitimacy - 1, 0, 100)

    # Build the partially-updated state for end-state evaluation.
    partial = state.model_copy(
        update={
            "turn_number": new_turn,
            "legitimacy": new_legitimacy,
        }
    )

    end = compute_end_state(partial)
    new_end_state = end if (end is not None and partial.end_state is None) else partial.end_state

    # Clear per-turn fields; preserve event_log and consequence_seeds.
    return partial.model_copy(
        update={
            "end_state": new_end_state,
            "current_event": None,
            "turn_proposals": [],
            "turn_tool_calls": [],
        }
    )
