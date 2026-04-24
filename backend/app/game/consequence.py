"""Consequence seed scheduler — pure functions for scheduling, firing,
foreshadowing, and retiring delayed consequences.

All functions are pure: take state in, return state out or a derived value.
No I/O, no logging, no mutation of inputs.
"""

from __future__ import annotations

import random
from uuid import UUID

from app.game.schemas import (
    ColonyState,
    ConsequenceSeed,
    DecisionRef,
    Event,
    EventOrigin,
    SeedPayload,
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def schedule_seed(state: ColonyState, seed: ConsequenceSeed) -> ColonyState:
    """Append a new seed to state.consequence_seeds. Does NOT mutate input.

    The seed is expected to be fully validated by the caller (e.g. via
    build_seed). No re-validation is performed here.
    """
    return state.model_copy(update={"consequence_seeds": [*state.consequence_seeds, seed]})


def check_and_fire_seeds(
    state: ColonyState,
) -> tuple[ColonyState, list[Event]]:
    """Fire seeds whose fires_at_turn matches the current turn.

    For each eligible seed (fires_at_turn == state.turn_number, not fired,
    not dormant): build an Event and mark the seed fired=True.

    Returns (new_state, list_of_fired_events). Multiple seeds may fire on the
    same turn; the caller decides ordering / priority.
    """
    fired_events: list[Event] = []
    updated_seeds: list[ConsequenceSeed] = []

    for seed in state.consequence_seeds:
        if seed.fires_at_turn == state.turn_number and not seed.fired and not seed.dormant:
            event = Event(
                id=seed.payload.event_id,
                category=seed.payload.category,
                title=f"Seeded: {seed.payload.description[:60]}",
                body=seed.payload.description,
                severity=seed.payload.severity,
                origin=EventOrigin.SEED_FIRED,
                origin_seed_id=seed.id,
            )
            fired_events.append(event)
            updated_seeds.append(seed.model_copy(update={"fired": True}))
        else:
            updated_seeds.append(seed)

    new_state = state.model_copy(update={"consequence_seeds": updated_seeds})
    return new_state, fired_events


def foreshadow_signals(state: ColonyState) -> list[str]:
    """Return advisor-facing hint strings for seeds foreshadowing this turn.

    Eligibility: foreshadowed=True AND foreshadow_turn == state.turn_number
    AND not fired AND not dormant.

    Ordered by written_at_turn ASC; ties broken lexicographically by seed.id.
    """
    eligible = [
        seed
        for seed in state.consequence_seeds
        if (
            seed.foreshadowed
            and seed.foreshadow_turn == state.turn_number
            and not seed.fired
            and not seed.dormant
        )
    ]
    eligible.sort(key=lambda s: (s.written_at_turn, str(s.id)))
    return [f"Foreshadow [{s.payload.category}]: {s.payload.description[:100]}" for s in eligible]


def mark_dormant_seeds(state: ColonyState) -> ColonyState:
    """Flip dormant=True on every unfired seed. Idempotent.

    Called once when end_state transitions from None to a terminal value.
    Already-fired seeds are never marked dormant.
    """
    updated_seeds = [
        seed.model_copy(update={"dormant": True}) if not seed.fired else seed
        for seed in state.consequence_seeds
    ]
    return state.model_copy(update={"consequence_seeds": updated_seeds})


# ---------------------------------------------------------------------------
# Helpers for callers (generator / engine)
# ---------------------------------------------------------------------------


def decide_foreshadow(rng: random.Random, threshold: float = 0.7) -> bool:
    """True 70 % of the time by default (SPEC §5.2).

    The caller supplies the RNG so determinism is controlled externally.
    """
    return rng.random() < threshold


def build_seed(
    *,
    game_id: UUID,
    written_at_turn: int,
    fires_at_turn: int,
    payload: SeedPayload,
    origin_decision: DecisionRef,
    foreshadow: bool,
    foreshadow_turn_offset: int = 2,
) -> ConsequenceSeed:
    """Factory with the default foreshadow_turn = fires_at_turn - 2 rule.

    When the offset would place foreshadow_turn before written_at_turn (e.g.
    fires_at_turn=3, written_at_turn=2 → 3-2=1 < 2), clamp to written_at_turn
    so the schema validator passes.
    """
    foreshadow_turn: int | None = None
    if foreshadow:
        raw = fires_at_turn - foreshadow_turn_offset
        foreshadow_turn = max(written_at_turn, raw)

    return ConsequenceSeed(
        game_id=game_id,
        written_at_turn=written_at_turn,
        fires_at_turn=fires_at_turn,
        foreshadowed=foreshadow,
        foreshadow_turn=foreshadow_turn,
        payload=payload,
        origin_decision=origin_decision,
    )
