"""Difficulty rails and wealth-scaling — pure functions enforcing SPEC §7.2.

Encodes the anti-snowball and anti-coast rails as code:

- `validate_event_pool_for_setting` — static check at setting-load time; the
  programmatic belt-and-braces layer that also protects against Settings
  built without passing through the YAML loader.
- `apply_early_turn_floors` — per-tool-call clamp on turns 1-3 so no single
  action can violate the SPEC §7.2 setup-act floors (30% resource drop cap,
  no lethal damage, legitimacy floor).
- `wealth_scale_factor` — Rimworld-style threat budget multiplier; well-
  resourced colonies get pulled toward harder events via event weight boost.

Pure: no I/O, no global state, no mutation of inputs.
"""

from __future__ import annotations

from statistics import mean

from app.game.schemas import ColonyState, ToolCall
from app.game.setting_loader import Setting

# ---------------------------------------------------------------------------
# Constants (SPEC §7.2)
# ---------------------------------------------------------------------------

# No single decision in turns 1-3 may drop a resource below 70% of its max.
_RESOURCE_FLOOR_RATIO = 0.7

# Agents cannot be killed on turns 1-3.
_MIN_HEALTH_EARLY = 1

# Legitimacy cannot fall below 20 before turn 4 from any single action.
_LEGITIMACY_FLOOR_EARLY = 20

# Wealth-scaling bounds (Rimworld threat budget).
_WEALTH_MIN = 0.5
_WEALTH_MAX = 2.0


# ---------------------------------------------------------------------------
# Pool validation
# ---------------------------------------------------------------------------


def validate_event_pool_for_setting(setting: Setting) -> list[str]:
    """Static check at setting-load time. Returns a list of error strings.

    Empty list means the pool is valid. Rules:

    1. Every event with can_kill_agent=True has min_turn >= 4.
    2. Exactly one forced_escalation category in the pool.
    3. The forced_escalation category has can_kill_agent=True and severity=5.
    4. No duplicate category ids in the pool.

    The Task 3.3 EventCategory schema already enforces (1) on YAML load; this
    second layer protects against programmatically-constructed Settings that
    bypass the YAML loader (e.g. via model_construct).
    """
    errors: list[str] = []
    seen_ids: set[str] = set()
    forced: list[str] = []

    for category in setting.event_categories:
        # Rule 4: duplicate ids.
        if category.id in seen_ids:
            errors.append(
                f"{setting.id}: duplicate event category id {category.id!r}"
            )
        seen_ids.add(category.id)

        # Rule 1: can_kill_agent requires min_turn >= 4.
        if category.can_kill_agent and category.min_turn < 4:
            errors.append(
                f"{setting.id}: event {category.id!r} has can_kill_agent=True "
                f"but min_turn={category.min_turn} (rail: min_turn >= 4)"
            )

        if _is_forced_escalation(category.id, category.tags):
            forced.append(category.id)

    # Rule 2: exactly one forced_escalation.
    if len(forced) == 0:
        errors.append(
            f"{setting.id}: no forced_escalation event found (rail: exactly 1)"
        )
    elif len(forced) > 1:
        errors.append(
            f"{setting.id}: expected exactly 1 forced_escalation event, "
            f"found {len(forced)}: {forced}"
        )

    # Rule 3: forced_escalation must be lethal sev=5.
    if len(forced) == 1:
        fe_id = forced[0]
        fe = next(c for c in setting.event_categories if c.id == fe_id)
        if not fe.can_kill_agent:
            errors.append(
                f"{setting.id}: forced_escalation event {fe_id!r} must have "
                f"can_kill_agent=True"
            )
        if fe.severity != 5:
            errors.append(
                f"{setting.id}: forced_escalation event {fe_id!r} must have "
                f"severity=5 (got {fe.severity})"
            )

    return errors


def _is_forced_escalation(event_id: str, tags: list[str]) -> bool:
    """Canonical rule: id starts with 'forced_escalation' OR tag present."""
    return event_id.startswith("forced_escalation") or "forced_escalation" in tags


# ---------------------------------------------------------------------------
# Early-turn floors (turns 1-3 validator clamp)
# ---------------------------------------------------------------------------


def apply_early_turn_floors(state: ColonyState, tc: ToolCall) -> ToolCall:
    """Clamp tool-call effects to respect SPEC §7.2 setup-act floors.

    Applied ONLY when state.turn_number <= 3 AND tc.caller_id is not None
    (LLM-proposed only; engine-emitted calls with caller_id=None bypass, since
    the engine is the author of those and is trusted).

    For modify_resource: the resulting resource value must stay >= 70% of max.
    For modify_health on an agent: block lethal deltas — resulting health
    clamped to at least 1 so no agent dies in turns 1-3.
    For any tool call with a legitimacy-cost argument: clamp so the resulting
    legitimacy would not fall below 20. This is a forward-compatible no-op
    today; once orders wire in during Phase 4 with an explicit legitimacy_cost
    arg, the clamp activates.

    back_leader has a legitimacy BONUS, not cost — untouched.

    Returns a possibly-modified ToolCall with clamped args. Must NOT change
    caller_id, name, id. Returns the same tc unchanged when no clamp applies.
    """
    # Gate: only turns 1-3, only LLM-authored calls.
    if state.turn_number > 3:
        return tc
    if tc.caller_id is None:
        return tc

    if tc.name == "modify_resource":
        return _clamp_resource_delta(state, tc)

    if tc.name == "modify_health":
        return _clamp_health_delta(state, tc)

    # Forward-compatible: any tool with an explicit 'legitimacy_cost' arg
    # (not a bonus) gets clamped against the pre-turn-4 legitimacy floor.
    if "legitimacy_cost" in tc.args and tc.name != "back_leader":
        return _clamp_legitimacy_cost(state, tc)

    return tc


def _clamp_resource_delta(state: ColonyState, tc: ToolCall) -> ToolCall:
    resource_id = tc.args.get("resource_id")
    delta = tc.args.get("delta")
    if not isinstance(resource_id, str) or not isinstance(delta, int):
        return tc
    resource = state.resources.get(resource_id)
    if resource is None:
        return tc
    if delta >= 0:
        return tc

    floor = int(resource.max * _RESOURCE_FLOOR_RATIO)
    projected = resource.value + delta
    if projected >= floor:
        return tc

    # Clamp delta so resulting value == floor (or zero delta if already below).
    clamped = max(floor - resource.value, delta)
    # Guard against positive clamp (if current value is already below floor,
    # we shouldn't flip a negative delta into a positive one).
    if clamped > 0:
        clamped = 0
    new_args = {**tc.args, "delta": clamped}
    return tc.model_copy(update={"args": new_args})


def _clamp_health_delta(state: ColonyState, tc: ToolCall) -> ToolCall:
    target_id = tc.args.get("target_id")
    delta = tc.args.get("delta")
    if not isinstance(target_id, str) or not isinstance(delta, int):
        return tc
    if delta >= 0:
        return tc

    if target_id == "player":
        current = state.player_health
    else:
        agent = next((a for a in state.agents if str(a.id) == target_id), None)
        if agent is None:
            return tc
        current = agent.health

    projected = current + delta
    if projected >= _MIN_HEALTH_EARLY:
        return tc

    clamped = _MIN_HEALTH_EARLY - current  # e.g. current=50 → delta = 1-50 = -49
    if clamped > 0:
        clamped = 0
    new_args = {**tc.args, "delta": clamped}
    return tc.model_copy(update={"args": new_args})


def _clamp_legitimacy_cost(state: ColonyState, tc: ToolCall) -> ToolCall:
    cost = tc.args.get("legitimacy_cost")
    if not isinstance(cost, int) or cost <= 0:
        return tc

    projected = state.legitimacy - cost
    if projected >= _LEGITIMACY_FLOOR_EARLY:
        return tc

    clamped = max(state.legitimacy - _LEGITIMACY_FLOOR_EARLY, 0)
    new_args = {**tc.args, "legitimacy_cost": clamped}
    return tc.model_copy(update={"args": new_args})


# ---------------------------------------------------------------------------
# Wealth scaling (Rimworld threat budget)
# ---------------------------------------------------------------------------


def wealth_scale_factor(state: ColonyState) -> float:
    """Rimworld-style threat budget multiplier. Clamped to [0.5, 2.0].

    Formula:
        resource_stability = mean(r.value / r.max for r in resources.values())
        agent_stability    = mean(a.health / 100 for a in agents if a.is_alive)
        raw = 0.5 + resource_stability * 0.75 + agent_stability * 0.75
        return clamp(raw, 0.5, 2.0)

    Pristine state (all resources at max, all agents at full health):
        raw = 0.5 + 0.75 + 0.75 = 2.0 -> 2.0.
    Catastrophic (all zero / no live agents):
        raw = 0.5 + 0 + 0 = 0.5 -> 0.5.
    Mid-game balanced (~50% stable):
        raw = 0.5 + 0.375 + 0.375 = 1.25.

    Edge cases:
    - resources == {}: resource_stability = 1.0 (neutral; no resources to drain).
    - zero living agents: agent_stability = 0.0 (pushes toward floor).
    """
    if state.resources:
        resource_stability = mean(
            r.value / r.max for r in state.resources.values() if r.max > 0
        )
    else:
        resource_stability = 1.0

    live_agents = [a for a in state.agents if a.is_alive]
    agent_stability = (
        mean(a.health / 100.0 for a in live_agents) if live_agents else 0.0
    )

    raw = 0.5 + resource_stability * 0.75 + agent_stability * 0.75
    return max(_WEALTH_MIN, min(_WEALTH_MAX, raw))
