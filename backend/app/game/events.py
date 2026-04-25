"""Event selection — picks the event presented each turn.

Priority (highest first):

1. Seed-fired events (from Task 3.7 `check_and_fire_seeds`). Highest-severity
   wins; ties broken lexicographically by origin_seed_id.
2. Forced escalation guarantee: exactly one event tagged `forced_escalation`
   must fire in turns 5-9 per run. Probability rises linearly: 20% at turn 5,
   100% at turn 9.
3. Weighted random draw from the setting's pool, respecting cooldown (no
   severity-5 back-to-back), min_turn gating, can_kill_agent turn-4 gating,
   and wealth-scaling (Rimworld threat budget boost on high-severity events).

Empty-pool fallback: if nothing is eligible after all filters, returns a
sentinel `respite` event — severity 1, origin NATURAL, generic body. This
is the one case where the selector invents an event rather than drawing
from the pool.

Pure: no I/O, no global state. All randomness flows through the caller-
supplied `random.Random`.
"""

from __future__ import annotations

import random

from app.game.difficulty import wealth_scale_factor
from app.game.schemas import ColonyState, Event, EventOrigin
from app.game.setting_loader import EventCategory, Setting

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FORCED_WINDOW_START = 5
_FORCED_WINDOW_END = 9  # inclusive
_SEVERITY_COOLDOWN_THRESHOLD = 5  # no severity>=this back-to-back
_CAN_KILL_MIN_TURN = 4
_WEALTH_BOOST_SEVERITY_THRESHOLD = 3  # wealth only boosts sev >= 3


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def select_event(
    state: ColonyState,
    setting: Setting,
    rng: random.Random,
    *,
    seeded_events: list[Event] | None = None,
) -> Event:
    """Pick the event to present on this turn.

    Priority order:
    1. If `seeded_events` contains >=1 event -> return the highest-severity
       one (tiebreak by earliest origin_seed_id lexicographic).
    2. If turn in [5..9] AND no forced_escalation has fired yet in this run ->
       must return a forced_escalation category event with rising probability
       (turn 5: 20%, turn 9: 100%, linear). Only the roll gates it; if the
       roll fails, we fall through to rule 3.
    3. Otherwise weighted random from the setting's pool, respecting:
       - cooldown: no severity-5 event two turns in a row
       - min_turn: event.min_turn <= state.turn_number
       - wealth-scaling factor applied to severity weights for sev >= 3
       - can_kill_agent events only eligible for state.turn_number >= 4
       - forced_escalation events never picked here (only via rule 2)

    If no pool event is eligible after filters, returns a sentinel `respite`
    event (severity 1, origin NATURAL).
    """
    # Rule 1: seed priority.
    if seeded_events:
        return _pick_highest_severity_seed(seeded_events)

    # Rule 2: forced escalation window.
    turn = state.turn_number
    if _FORCED_WINDOW_START <= turn <= _FORCED_WINDOW_END and not has_forced_escalation_fired(
        state
    ):
        prob = forced_escalation_probability(turn)
        if rng.random() < prob:
            forced = _find_forced_escalation_category(setting)
            if forced is not None:
                return _build_event(forced, rng, EventOrigin.FORCED_ESCALATION)

    # Rule 3: weighted random from eligible pool.
    category = _weighted_pick(state, setting, rng)
    if category is None:
        return _respite_event()
    return _build_event(category, rng, EventOrigin.NATURAL)


def has_forced_escalation_fired(state: ColonyState) -> bool:
    """True if any resolved event has origin=FORCED_ESCALATION."""
    return any(
        resolved.event.origin is EventOrigin.FORCED_ESCALATION for resolved in state.event_log
    )


def forced_escalation_probability(turn: int) -> float:
    """Linear ramp: turn 5 -> 0.2, turn 9 -> 1.0. Outside window -> 0.0."""
    if turn < _FORCED_WINDOW_START or turn > _FORCED_WINDOW_END:
        return 0.0
    # Solve y = m*turn + b with (5, 0.2), (9, 1.0):
    #   m = (1.0 - 0.2) / (9 - 5) = 0.2 ; b = 0.2 - 0.2*5 = -0.8
    # Use integer division to avoid binary-float rounding at the endpoints.
    offset = turn - _FORCED_WINDOW_START  # 0..4
    return 0.2 + offset * 0.2


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _pick_highest_severity_seed(events: list[Event]) -> Event:
    """Return the highest-severity seeded event; tiebreak on origin_seed_id."""

    def key(evt: Event) -> tuple[int, str]:
        # Negative severity so max-sort picks highest; origin_seed_id string
        # sorts lexicographically ascending (earliest wins on tie).
        return (-evt.severity, str(evt.origin_seed_id or ""))

    return sorted(events, key=key)[0]


def _is_forced_escalation_category(category: EventCategory) -> bool:
    return category.id.startswith("forced_escalation") or "forced_escalation" in category.tags


def _find_forced_escalation_category(setting: Setting) -> EventCategory | None:
    for category in setting.event_categories:
        if _is_forced_escalation_category(category):
            return category
    return None


def _last_event_severity(state: ColonyState) -> int | None:
    """Severity of the event resolved on the most recent turn, if any."""
    if not state.event_log:
        return None
    # event_log is ordered by turn_number; grab the highest-turn record.
    latest = max(state.event_log, key=lambda r: r.turn_number)
    # Only enforce cooldown when the previous event was last turn (N vs N+1).
    if latest.turn_number != state.turn_number - 1:
        return None
    return latest.event.severity


def _eligible_categories(state: ColonyState, setting: Setting) -> list[EventCategory]:
    """Filter the setting's event pool against all selection rules."""
    cooldown_blocks_sev5 = _last_event_severity(state) == _SEVERITY_COOLDOWN_THRESHOLD

    eligible: list[EventCategory] = []
    for category in setting.event_categories:
        if _is_forced_escalation_category(category):
            # Only fires via rule 2.
            continue
        if category.min_turn > state.turn_number:
            continue
        if category.can_kill_agent and state.turn_number < _CAN_KILL_MIN_TURN:
            continue
        if cooldown_blocks_sev5 and category.severity >= _SEVERITY_COOLDOWN_THRESHOLD:
            continue
        eligible.append(category)
    return eligible


def _weighted_pick(
    state: ColonyState, setting: Setting, rng: random.Random
) -> EventCategory | None:
    eligible = _eligible_categories(state, setting)
    if not eligible:
        return None

    wealth = wealth_scale_factor(state)
    weights: list[float] = []
    for category in eligible:
        w = float(category.weight)
        if category.severity >= _WEALTH_BOOST_SEVERITY_THRESHOLD:
            w *= wealth
        weights.append(w)

    # All zero shouldn't happen given schema weights >= 0.1, but guard anyway.
    if not any(w > 0 for w in weights):
        return None

    return rng.choices(eligible, weights=weights, k=1)[0]


def _build_event(category: EventCategory, rng: random.Random, origin: EventOrigin) -> Event:
    """Materialise an Event from an EventCategory using the supplied rng.

    The id is made unique per-draw via a 32-bit hex token from the rng so the
    same category can fire multiple times across a run without id collisions.
    """
    unique = f"{rng.getrandbits(32):08x}"
    title = _derive_title(category.prompt_template)
    return Event(
        id=f"{category.id}:{unique}",
        category=category.id,
        title=title,
        body=category.prompt_template.strip(),
        severity=category.severity,
        origin=origin,
    )


def _derive_title(prompt_template: str) -> str:
    """Extract a terse title from the first sentence of the prompt template."""
    stripped = prompt_template.strip()
    # Take up to the first sentence break or 60 chars, whichever comes first.
    first_sentence = stripped.split(".")[0]
    title = first_sentence[:60].strip()
    return title or "Event"


def _respite_event() -> Event:
    """Sentinel used when no pool event is eligible.

    Severity 1, category='respite', origin NATURAL. The engine treats this as
    a regular natural event; content is deliberately minimal.
    """
    return Event(
        id="respite:quiet_turn",
        category="respite",
        title="A quiet turn",
        body="A quiet turn. Nothing demands a decision.",
        severity=1,
        origin=EventOrigin.NATURAL,
    )
