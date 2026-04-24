"""Unit tests for app.game.consequence — consequence seed scheduler.

All tests are synchronous; no asyncio, no DB, no LLM.
"""

from __future__ import annotations

import random
from uuid import UUID, uuid4

from app.game.consequence import (
    build_seed,
    check_and_fire_seeds,
    decide_foreshadow,
    foreshadow_signals,
    mark_dormant_seeds,
    schedule_seed,
)
from app.game.schemas import (
    Agent,
    ColonyState,
    ConsequenceSeed,
    DecisionRef,
    EventOrigin,
    Quadrant,
    Resource,
    SeedPayload,
)

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

GAME_ID = UUID("aaaaaaaa-0000-0000-0000-000000000010")
USER_ID = UUID("bbbbbbbb-0000-0000-0000-000000000020")

# ---------------------------------------------------------------------------
# Fabricators (minimal, no import from test_engine)
# ---------------------------------------------------------------------------


def _agent(name: str = "A") -> Agent:
    return Agent(
        id=uuid4(),
        game_id=GAME_ID,
        name=name,
        role="Advisor",
        archetype_quadrant=Quadrant.ACCOUNTANT,
        ethics=0.0,
        temperament=0.0,
        loyalty=0.0,
        portrait_seed="seed",
        health=100,
        morale=50,
        grievance=0,
        is_alive=True,
    )


def _resource(rid: str = "fuel") -> Resource:
    return Resource(
        id=rid, name=rid.capitalize(), icon="X", value=50, max=100, critical_threshold=20
    )


def _colony(turn_number: int = 1, seeds: list[ConsequenceSeed] | None = None) -> ColonyState:
    return ColonyState(
        game_id=GAME_ID,
        user_id=USER_ID,
        setting_id="arctic_station_v1",
        seed=42,
        turn_number=turn_number,
        max_turns=10,
        resources={"fuel": _resource()},
        agents=[_agent()],
        legitimacy=60,
        player_health=100,
        consequence_seeds=seeds or [],
        event_log=[],
        current_event=None,
        turn_proposals=[],
        turn_tool_calls=[],
    )


def _payload(
    description: str = "Something bad happens.",
    category: str = "crisis",
    severity: int = 3,
    event_id: str | None = None,
) -> SeedPayload:
    return SeedPayload(
        event_id=event_id or f"evt-{uuid4().hex[:8]}",
        severity=severity,
        category=category,
        description=description,
    )


def _decision_ref(turn: int = 1) -> DecisionRef:
    return DecisionRef(
        turn_number=turn,
        event_id=f"evt-{uuid4().hex[:8]}",
        chosen_proposal_id=f"prop-{uuid4().hex[:8]}",
    )


def _seed(
    *,
    written_at_turn: int = 1,
    fires_at_turn: int = 5,
    foreshadow: bool = False,
    foreshadow_turn: int | None = None,
    fired: bool = False,
    dormant: bool = False,
    payload: SeedPayload | None = None,
) -> ConsequenceSeed:
    """Build a ConsequenceSeed with sensible defaults."""
    return ConsequenceSeed(
        game_id=GAME_ID,
        written_at_turn=written_at_turn,
        fires_at_turn=fires_at_turn,
        foreshadowed=foreshadow,
        foreshadow_turn=foreshadow_turn,
        payload=payload or _payload(),
        origin_decision=_decision_ref(written_at_turn),
        fired=fired,
        dormant=dormant,
    )


def _foreshadowing_seed(
    *,
    written_at_turn: int = 1,
    fires_at_turn: int = 7,
    foreshadow_turn: int = 5,
    fired: bool = False,
    dormant: bool = False,
    payload: SeedPayload | None = None,
) -> ConsequenceSeed:
    """Seed that is foreshadowed on a specific turn."""
    return ConsequenceSeed(
        game_id=GAME_ID,
        written_at_turn=written_at_turn,
        fires_at_turn=fires_at_turn,
        foreshadowed=True,
        foreshadow_turn=foreshadow_turn,
        payload=payload or _payload(),
        origin_decision=_decision_ref(written_at_turn),
        fired=fired,
        dormant=dormant,
    )


# ---------------------------------------------------------------------------
# schedule_seed
# ---------------------------------------------------------------------------


def test_schedule_seed_appends_and_preserves_others() -> None:
    existing = _seed(fires_at_turn=3)
    state = _colony(seeds=[existing])
    original_len = len(state.consequence_seeds)

    new_seed = _seed(fires_at_turn=6)
    new_state = schedule_seed(state, new_seed)

    # New seed appended
    assert len(new_state.consequence_seeds) == 2
    assert new_state.consequence_seeds[-1].id == new_seed.id
    # Existing seed preserved
    assert new_state.consequence_seeds[0].id == existing.id
    # Input unmutated
    assert len(state.consequence_seeds) == original_len


def test_pureness_schedule_seed() -> None:
    state = _colony(seeds=[_seed(fires_at_turn=4)])
    before = state.model_dump()
    schedule_seed(state, _seed(fires_at_turn=8))
    assert state.model_dump() == before


# ---------------------------------------------------------------------------
# check_and_fire_seeds
# ---------------------------------------------------------------------------


def test_check_and_fire_seeds_fires_matching_turn() -> None:
    seed = _seed(fires_at_turn=5)
    state = _colony(turn_number=5, seeds=[seed])

    new_state, events = check_and_fire_seeds(state)

    assert len(events) == 1
    assert new_state.consequence_seeds[0].fired is True


def test_check_and_fire_seeds_does_not_fire_earlier() -> None:
    seed = _seed(fires_at_turn=5)
    state = _colony(turn_number=4, seeds=[seed])

    new_state, events = check_and_fire_seeds(state)

    assert events == []
    assert new_state.consequence_seeds[0].fired is False


def test_check_and_fire_seeds_does_not_re_fire() -> None:
    seed = _seed(fires_at_turn=5, fired=True)
    state = _colony(turn_number=5, seeds=[seed])

    _, events = check_and_fire_seeds(state)

    assert events == []


def test_check_and_fire_seeds_multiple_on_same_turn() -> None:
    s1 = _seed(fires_at_turn=5)
    s2 = _seed(fires_at_turn=5)
    state = _colony(turn_number=5, seeds=[s1, s2])

    new_state, events = check_and_fire_seeds(state)

    assert len(events) == 2
    assert all(s.fired for s in new_state.consequence_seeds)


def test_fired_event_origin_seed_id_links_back() -> None:
    seed = _seed(fires_at_turn=5)
    state = _colony(turn_number=5, seeds=[seed])

    _, events = check_and_fire_seeds(state)

    assert events[0].origin == EventOrigin.SEED_FIRED
    assert events[0].origin_seed_id == seed.id


def test_check_and_fire_seeds_event_id_from_payload() -> None:
    payload = _payload(event_id="my-event-42")
    seed = _seed(fires_at_turn=5, payload=payload)
    state = _colony(turn_number=5, seeds=[seed])

    _, events = check_and_fire_seeds(state)

    assert events[0].id == "my-event-42"


def test_check_and_fire_seeds_whole_list_updated() -> None:
    """Ensure the returned state contains ALL seeds (not just the fired ones)."""
    s_fire = _seed(fires_at_turn=5)
    s_wait = _seed(written_at_turn=3, fires_at_turn=8)
    state = _colony(turn_number=5, seeds=[s_fire, s_wait])

    new_state, events = check_and_fire_seeds(state)

    assert len(new_state.consequence_seeds) == 2
    assert new_state.consequence_seeds[0].fired is True  # the one that fired
    assert new_state.consequence_seeds[1].fired is False  # the one still waiting


# ---------------------------------------------------------------------------
# foreshadow_signals
# ---------------------------------------------------------------------------


def test_foreshadow_signals_returns_ordered_list() -> None:
    # Three seeds all foreshadowing on turn 5, written at different turns
    s1 = _foreshadowing_seed(
        written_at_turn=3, fires_at_turn=7, foreshadow_turn=5, payload=_payload(category="flood")
    )
    s2 = _foreshadowing_seed(
        written_at_turn=1, fires_at_turn=7, foreshadow_turn=5, payload=_payload(category="fire")
    )
    s3 = _foreshadowing_seed(
        written_at_turn=2, fires_at_turn=7, foreshadow_turn=5, payload=_payload(category="storm")
    )
    state = _colony(turn_number=5, seeds=[s1, s2, s3])

    signals = foreshadow_signals(state)

    # Should be sorted by written_at_turn ASC: s2(1), s3(2), s1(3)
    assert len(signals) == 3
    assert "fire" in signals[0]  # written_at_turn=1
    assert "storm" in signals[1]  # written_at_turn=2
    assert "flood" in signals[2]  # written_at_turn=3


def test_foreshadow_signals_only_for_this_turn() -> None:
    s_this = _foreshadowing_seed(foreshadow_turn=5, fires_at_turn=7)
    s_other = _foreshadowing_seed(foreshadow_turn=6, fires_at_turn=8)
    state = _colony(turn_number=5, seeds=[s_this, s_other])

    signals = foreshadow_signals(state)

    assert len(signals) == 1


def test_foreshadow_signals_skip_fired_seeds() -> None:
    s_fired = _foreshadowing_seed(foreshadow_turn=5, fires_at_turn=7, fired=True)
    s_ok = _foreshadowing_seed(foreshadow_turn=5, fires_at_turn=7)
    state = _colony(turn_number=5, seeds=[s_fired, s_ok])

    signals = foreshadow_signals(state)

    assert len(signals) == 1


def test_foreshadow_signals_skip_dormant_seeds() -> None:
    s_dormant = _foreshadowing_seed(foreshadow_turn=5, fires_at_turn=7, dormant=True)
    state = _colony(turn_number=5, seeds=[s_dormant])

    signals = foreshadow_signals(state)

    assert signals == []


def test_foreshadow_signals_skip_non_foreshadowed() -> None:
    s_silent = _seed(written_at_turn=1, fires_at_turn=7)  # foreshadow=False
    state = _colony(turn_number=5, seeds=[s_silent])

    signals = foreshadow_signals(state)

    assert signals == []


# ---------------------------------------------------------------------------
# mark_dormant_seeds
# ---------------------------------------------------------------------------


def test_mark_dormant_seeds_flips_unfired() -> None:
    s_fired = _seed(fires_at_turn=3, fired=True)
    s_unfired1 = _seed(fires_at_turn=6)
    s_unfired2 = _seed(written_at_turn=2, fires_at_turn=8)
    state = _colony(seeds=[s_fired, s_unfired1, s_unfired2])

    new_state = mark_dormant_seeds(state)

    seeds = {s.id: s for s in new_state.consequence_seeds}
    assert seeds[s_fired.id].dormant is False
    assert seeds[s_fired.id].fired is True
    assert seeds[s_unfired1.id].dormant is True
    assert seeds[s_unfired2.id].dormant is True


def test_mark_dormant_seeds_idempotent() -> None:
    s = _seed(fires_at_turn=5)
    state = _colony(seeds=[s])

    once = mark_dormant_seeds(state)
    twice = mark_dormant_seeds(once)

    assert once.consequence_seeds[0].dormant is True
    assert twice.consequence_seeds[0].dormant is True
    assert once.model_dump() == twice.model_dump()


def test_pureness_mark_dormant() -> None:
    state = _colony(seeds=[_seed(fires_at_turn=4)])
    before = state.model_dump()
    mark_dormant_seeds(state)
    assert state.model_dump() == before


# ---------------------------------------------------------------------------
# decide_foreshadow
# ---------------------------------------------------------------------------


def test_decide_foreshadow_is_deterministic() -> None:
    rng = random.Random(99)
    results = [decide_foreshadow(rng) for _ in range(100)]

    rng2 = random.Random(99)
    results2 = [decide_foreshadow(rng2) for _ in range(100)]

    assert results == results2


def test_decide_foreshadow_distribution() -> None:
    rng = random.Random(12345)
    n = 1000
    true_count = sum(decide_foreshadow(rng) for _ in range(n))
    ratio = true_count / n
    assert 0.65 <= ratio <= 0.75, f"Expected ratio ~0.70, got {ratio:.3f}"


def test_decide_foreshadow_custom_threshold() -> None:
    rng = random.Random(7)
    n = 2000
    true_count = sum(decide_foreshadow(rng, threshold=0.3) for _ in range(n))
    ratio = true_count / n
    assert 0.25 <= ratio <= 0.35, f"Expected ratio ~0.30, got {ratio:.3f}"


# ---------------------------------------------------------------------------
# build_seed
# ---------------------------------------------------------------------------


def test_build_seed_no_foreshadow() -> None:
    seed = build_seed(
        game_id=GAME_ID,
        written_at_turn=1,
        fires_at_turn=5,
        payload=_payload(),
        origin_decision=_decision_ref(),
        foreshadow=False,
    )
    assert seed.foreshadowed is False
    assert seed.foreshadow_turn is None


def test_build_seed_default_foreshadow_offset() -> None:
    seed = build_seed(
        game_id=GAME_ID,
        written_at_turn=1,
        fires_at_turn=7,
        payload=_payload(),
        origin_decision=_decision_ref(),
        foreshadow=True,
    )
    assert seed.foreshadowed is True
    assert seed.foreshadow_turn == 5  # 7 - 2


def test_build_seed_foreshadow_clamped_to_written_at() -> None:
    # fires_at_turn=3, written_at_turn=2 → 3-2=1 < 2 → clamped to 2
    seed = build_seed(
        game_id=GAME_ID,
        written_at_turn=2,
        fires_at_turn=3,
        payload=_payload(),
        origin_decision=_decision_ref(2),
        foreshadow=True,
    )
    assert seed.foreshadow_turn == 2


def test_build_seed_custom_offset() -> None:
    seed = build_seed(
        game_id=GAME_ID,
        written_at_turn=1,
        fires_at_turn=10,
        payload=_payload(),
        origin_decision=_decision_ref(),
        foreshadow=True,
        foreshadow_turn_offset=4,
    )
    assert seed.foreshadow_turn == 6  # 10 - 4
