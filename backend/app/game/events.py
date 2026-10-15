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
   Caller-supplied `recently_fired_event_ids` further filters the pool so
   the same template doesn't recycle within the cooldown window.

Empty-pool fallback: if nothing is eligible after all filters (including
cooldown), returns a respite event drawn from a 10-template pool keyed
deterministically off (setting_id, seed, turn_number). This guards both
the daily-determinism property of the run and the "92% of runs ship the
same hardcoded fallback" bug from playtest report §4.1.

Pure: no I/O, no global state. All randomness flows through the caller-
supplied `random.Random`.
"""

from __future__ import annotations

import random
import zlib

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
    recently_fired_event_ids: set[str] | None = None,
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
       - `recently_fired_event_ids` excludes recently-used category ids so
         the same template doesn't recycle (playtest report §1.9)

    If no pool event is eligible after filters, returns a respite event
    drawn from a 10-template pool, deterministic per (setting_id, seed,
    turn_number).
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
            # Forced-escalation events ignore the cooldown set: the
            # "exactly once per run" guarantee outranks recycling concerns.
            if forced is not None:
                return _build_event(forced, rng, EventOrigin.FORCED_ESCALATION)

    # Rule 3: weighted random from eligible pool.
    excluded = recently_fired_event_ids or set()
    category = _weighted_pick(state, setting, rng, excluded_ids=excluded)
    if category is None:
        return _respite_event(state.setting_id, state.seed, state.turn_number)
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


def _eligible_categories(
    state: ColonyState, setting: Setting, *, excluded_ids: set[str]
) -> list[EventCategory]:
    """Filter the setting's event pool against all selection rules.

    `excluded_ids` is the cooldown set — categories whose `id` is in this
    set are dropped before weighting. Empty set is the no-op default.
    """
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
        if category.id in excluded_ids:
            # Per-template cooldown — caller-provided last-N event ids.
            continue
        eligible.append(category)
    return eligible


def _weighted_pick(
    state: ColonyState,
    setting: Setting,
    rng: random.Random,
    *,
    excluded_ids: set[str],
) -> EventCategory | None:
    eligible = _eligible_categories(state, setting, excluded_ids=excluded_ids)
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
    title = category.title or _derive_title(category.prompt_template)
    return Event(
        id=f"{category.id}:{unique}",
        category=category.id,
        title=title,
        body=category.prompt_template.strip(),
        severity=category.severity,
        origin=origin,
    )


_TITLE_MAX = 48


def _derive_title(prompt_template: str) -> str:
    """Fallback title for an event without an authored `title`.

    First sentence if it fits in 48 chars; otherwise cut at the last space
    before char 47 and append "…". Never cuts mid-word.
    """
    first_sentence = prompt_template.strip().split(".")[0].strip()
    if not first_sentence:
        return "Event"
    if len(first_sentence) <= _TITLE_MAX:
        return first_sentence
    cut = first_sentence[: _TITLE_MAX - 1]
    space = cut.rfind(" ")
    if space > 0:
        cut = cut[:space]
    return cut.rstrip(" ,;:—-") + "…"


_RESPITE_TEMPLATES: tuple[tuple[str, str], ...] = (
    (
        "The wind shifts",
        "The wind shifts. Not a storm, not yet. Someone in the next room is "
        "counting cans of something out loud and starting over.",
    ),
    (
        "Generator pitch",
        "The generator hum drops half a step and holds. No alarm. Nobody mentions "
        "it. Whoever is on shift writes the time in the logbook anyway.",
    ),
    (
        "Half-cold coffee",
        "Half a mug of coffee on the comms desk has gone cold. The owner is "
        "either asleep or pretending to be. Nobody picks it up.",
    ),
    (
        "Inventory recount",
        "Inventory comes up two short. They recount. The numbers match the second "
        "time. Nobody asks which count was wrong.",
    ),
    (
        "Quiet corridor",
        "Footsteps in the corridor, then nothing. Whoever it was turned back. "
        "The lights flicker once and steady out.",
    ),
    (
        "Old radio chatter",
        "The radio catches half a sentence in a language nobody here speaks, then "
        "static. It happens sometimes. Nobody touches the dial.",
    ),
    (
        "Maintenance log",
        "The maintenance log gets a new line: a valve that nobody remembers "
        "tightening is tight. Initialed and dated. Filed.",
    ),
    (
        "Cold hands",
        "Two of the crew sit at the table not talking. One of them is rubbing "
        "feeling back into his hands. The other refills his cup.",
    ),
    (
        "Shift change",
        "Shift change goes by the book. The handover sheet has nothing on it "
        "worth reading aloud. The relief takes the chair without a word.",
    ),
    (
        "Window frost",
        "Frost on the inside of the porthole, finger-thick at the corner. "
        "Someone scrapes a small clear circle with a thumbnail and walks past.",
    ),
)


def _respite_event(setting_id: str, seed: int, turn_number: int) -> Event:
    """Pick one of ten respite templates deterministically.

    Replaces the single hardcoded "A quiet turn" fallback that was firing on
    92% of corpus runs at turn 1 (playtest report §4.1). The same
    `(setting_id, seed)` always yields the same first respite — preserving
    the daily-determinism invariant — but `turn_number` mixes into the hash
    so a single run that hits respite multiple times gets variety.

    The 10 strings are project-thematic colony beats: a wind shift, a
    generator hum shift, a half-emptied coffee cup, the maintenance log
    getting a quiet line, etc. Severity 1, origin NATURAL, category
    'respite'. Eval can detect cross-turn repetition via the indexed id.

    Uses zlib.crc32 (not the builtin `hash`) for setting_id mixing so the
    same input gives the same output across interpreter restarts —
    `hash(str)` is salted by PYTHONHASHSEED and would break daily
    determinism across server reboots.
    """
    setting_hash = zlib.crc32(setting_id.encode("utf-8"))
    index = (seed ^ setting_hash ^ turn_number) % len(_RESPITE_TEMPLATES)
    title, body = _RESPITE_TEMPLATES[index]
    return Event(
        id=f"respite:quiet_turn:{index}",
        category="respite",
        title=title,
        body=body,
        severity=1,
        origin=EventOrigin.NATURAL,
    )
