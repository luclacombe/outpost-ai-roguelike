"""Wildermyth-inspired transformation system.

A mid-run mechanic (SPEC §6.4): at a high-emotion moment, at most once per run,
an advisor can undergo a permanent shift in how they're prompted for the rest
of the run. Eligibility is rule-based; application is pure.

Public API:
  - TransformationCandidate: dataclass describing a proposed shift.
  - check_transformation_eligibility: rule check → candidate or None.
  - apply_transformation: write the shift to ColonyState. Pure.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.game.schemas import Agent, ColonyState, Event, Transformation

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# Severity threshold: only high-emotion events can trigger a transformation.
_MIN_TRIGGER_SEVERITY = 4

# Grievance threshold for the "carrying resentment" branch.
_MIN_TRIGGER_GRIEVANCE = 5

# How many recent memory entries to scan for a death tag.
_DEATH_MEMORY_WINDOW = 3

# Axis pole strength: only agents with a *dominant* lean can transform. A
# lukewarm axis is too shapeless to pivot meaningfully.
_DOMINANT_AXIS_THRESHOLD = 0.6

# How far each transformation moves the axis (before clamping).
_AXIS_SHIFT_MAGNITUDE = 0.4

# Axis bounds.
_AXIS_MIN = -1.0
_AXIS_MAX = 1.0

# Memory prefix for the death-pin tag (matches engine.py).
_DEATH_MEMORY_PREFIX = "died in turn "

# ---------------------------------------------------------------------------
# Transformation description bank
# ---------------------------------------------------------------------------
#
# Keys: (axis, direction) where direction ∈ {"positive", "negative"} describes
# the SIGN of the applied shift. "ethics/negative" means ethics is pushed
# downward (toward pragmatist); the advisor was an idealist and is now not.
#
# Rules for the prose: short, concrete, grim-not-nihilistic. No em-dashes
# clustered, no "unfortunately", no rule-of-three. These are user-facing.
_TRANSFORMATION_DESCRIPTIONS: dict[tuple[str, str], str] = {
    ("ethics", "negative"): "The principle bent against the outcome. Now the outcome decides.",
    ("ethics", "positive"): "The ledger could not hold what was lost. Now conscience does.",
    ("temperament", "positive"): "Waiting made things worse. They will move first now.",
    ("temperament", "negative"): "Bold cost them. They will wait now, perhaps too long.",
    ("loyalty", "negative"): "The collective turned away. Only themselves left to trust.",
    ("loyalty", "positive"): "Alone they failed. The group is all that remains.",
}


# ---------------------------------------------------------------------------
# Candidate dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TransformationCandidate:
    """A proposed, not-yet-applied transformation for a single agent."""

    agent_id: UUID
    description: str               # short human-facing, e.g. "hardened by loss"
    axis_shift: dict[str, float]   # keys: ethics/temperament/loyalty
    trigger_reason: str            # short reason string for observability


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clamp_axis(v: float) -> float:
    return max(_AXIS_MIN, min(_AXIS_MAX, v))


def _has_recent_death_memory(agent: Agent) -> bool:
    """Return True if any of the agent's last N memory entries is a death pin."""
    recent = agent.memory[-_DEATH_MEMORY_WINDOW:] if agent.memory else []
    return any(m.startswith(_DEATH_MEMORY_PREFIX) for m in recent)


def _pick_shift(agent: Agent, event: Event) -> tuple[str, float, str] | None:
    """Decide which axis to shift, by how much, and with what reason.

    Returns (axis_name, signed_shift, trigger_reason) or None if no dominant
    pattern matches. The shift direction moves *away* from the current pole —
    this is what makes transformations narratively meaningful.

    Priority order: ethics → temperament → loyalty. The first dominant axis
    wins; later candidates do not stack, by design (once-per-run invariant).
    """
    if agent.ethics > _DOMINANT_AXIS_THRESHOLD:
        return ("ethics", -_AXIS_SHIFT_MAGNITUDE, "idealist_breaks_pragmatist")
    if agent.ethics < -_DOMINANT_AXIS_THRESHOLD:
        return ("ethics", _AXIS_SHIFT_MAGNITUDE, "pragmatist_breaks_idealist")
    if agent.temperament < -_DOMINANT_AXIS_THRESHOLD:
        return ("temperament", _AXIS_SHIFT_MAGNITUDE, "cautious_forced_bold")
    if agent.temperament > _DOMINANT_AXIS_THRESHOLD:
        return ("temperament", -_AXIS_SHIFT_MAGNITUDE, "bold_forced_cautious")
    # Loyalty shift gated on max-severity — reserved for the strongest moments.
    if event.severity == 5 and agent.loyalty > _DOMINANT_AXIS_THRESHOLD:
        return ("loyalty", -_AXIS_SHIFT_MAGNITUDE, "collective_abandons_member")
    if event.severity == 5 and agent.loyalty < -_DOMINANT_AXIS_THRESHOLD:
        return ("loyalty", _AXIS_SHIFT_MAGNITUDE, "individual_joins_collective")
    return None


def _description_for(axis: str, shift: float) -> str:
    direction = "positive" if shift > 0 else "negative"
    return _TRANSFORMATION_DESCRIPTIONS[(axis, direction)]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_transformation_eligibility(
    state: ColonyState,
    agent: Agent,
    triggering_event: Event,
) -> TransformationCandidate | None:
    """Return a transformation candidate for `agent`, or None if not eligible.

    Rules (all must pass):
      1. agent.transformation is None (once-per-run).
      2. agent.is_alive.
      3. triggering_event.severity >= _MIN_TRIGGER_SEVERITY.
      4. agent.grievance >= _MIN_TRIGGER_GRIEVANCE OR a recent death pin.
      5. A dominant-axis shift pattern matches (see _pick_shift).
    """
    # `state` is not consulted directly yet, but kept in the signature so
    # future rules (e.g. "only on odd turns") do not change the public API.
    del state  # intentionally unused

    if agent.transformation is not None:
        return None
    if not agent.is_alive:
        return None
    if triggering_event.severity < _MIN_TRIGGER_SEVERITY:
        return None
    if agent.grievance < _MIN_TRIGGER_GRIEVANCE and not _has_recent_death_memory(agent):
        return None

    picked = _pick_shift(agent, triggering_event)
    if picked is None:
        return None

    axis, shift, reason = picked
    return TransformationCandidate(
        agent_id=agent.id,
        description=_description_for(axis, shift),
        axis_shift={axis: shift},
        trigger_reason=reason,
    )


def apply_transformation(
    state: ColonyState,
    agent_id: UUID,
    candidate: TransformationCandidate,
) -> ColonyState:
    """Write `candidate` onto the named agent. Pure.

    Raises:
        KeyError: no agent with that id.
        ValueError: agent already has a transformation (once-per-run).
    """
    target_index: int | None = None
    for i, agent in enumerate(state.agents):
        if agent.id == agent_id:
            target_index = i
            break
    if target_index is None:
        raise KeyError(f"no agent with id {agent_id!r} in colony state")

    target = state.agents[target_index]
    if target.transformation is not None:
        raise ValueError(
            f"agent {agent_id!r} already has a transformation; "
            f"transformations are once-per-run"
        )

    # Compute new axis values with clamping.
    new_ethics = _clamp_axis(target.ethics + candidate.axis_shift.get("ethics", 0.0))
    new_temperament = _clamp_axis(
        target.temperament + candidate.axis_shift.get("temperament", 0.0)
    )
    new_loyalty = _clamp_axis(target.loyalty + candidate.axis_shift.get("loyalty", 0.0))

    transformation = Transformation(
        trigger_turn=state.turn_number,
        description=candidate.description,
        axis_shift=dict(candidate.axis_shift),
    )

    memory_entry = f"transformed: {candidate.description}"
    new_memory = [*target.memory, memory_entry]

    updated = target.model_copy(
        update={
            "ethics": new_ethics,
            "temperament": new_temperament,
            "loyalty": new_loyalty,
            "transformation": transformation,
            "memory": new_memory,
        }
    )

    new_agents = list(state.agents)
    new_agents[target_index] = updated
    return state.model_copy(update={"agents": new_agents})
