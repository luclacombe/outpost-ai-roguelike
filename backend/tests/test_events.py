"""Unit tests for app.game.events — event selection + difficulty rails.

All tests are synchronous; no asyncio, no DB, no LLM.
"""

from __future__ import annotations

import random
from uuid import UUID, uuid4

from app.game.events import (
    forced_escalation_probability,
    has_forced_escalation_fired,
    select_event,
)
from app.game.schemas import (
    Agent,
    ColonyState,
    Event,
    EventOrigin,
    Quadrant,
    ResolvedEvent,
    Resource,
)
from app.game.setting_loader import (
    AdvisorArchetype,
    EventCategory,
    ResourceDef,
    Setting,
    load_setting,
)

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

GAME_ID = UUID("aaaaaaaa-0000-0000-0000-000000000010")
USER_ID = UUID("bbbbbbbb-0000-0000-0000-000000000020")


# ---------------------------------------------------------------------------
# Fabricators
# ---------------------------------------------------------------------------


def _agent(name: str = "A", health: int = 100, is_alive: bool = True) -> Agent:
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
        health=health,
        morale=50,
        grievance=0,
        is_alive=is_alive,
    )


def _resource(rid: str = "fuel", value: int = 50) -> Resource:
    return Resource(
        id=rid, name=rid.capitalize(), icon="X", value=value, max=100, critical_threshold=20
    )


def _colony(
    turn_number: int = 1,
    event_log: list[ResolvedEvent] | None = None,
    resources: dict[str, Resource] | None = None,
    agents: list[Agent] | None = None,
) -> ColonyState:
    return ColonyState(
        game_id=GAME_ID,
        user_id=USER_ID,
        setting_id="arctic_station_v1",
        seed=42,
        turn_number=turn_number,
        max_turns=10,
        resources=resources if resources is not None else {"fuel": _resource()},
        agents=agents if agents is not None else [_agent()],
        legitimacy=60,
        player_health=100,
        consequence_seeds=[],
        event_log=event_log or [],
        current_event=None,
        turn_proposals=[],
        turn_tool_calls=[],
    )


def _category(
    *,
    cid: str = "test_event",
    weight: float = 1.0,
    tags: list[str] | None = None,
    severity: int = 2,
    min_turn: int = 1,
    prompt_template: str = "A thing happens. Details here.",
    can_kill_agent: bool = False,
) -> EventCategory:
    return EventCategory(
        id=cid,
        weight=weight,
        tags=tags or ["test"],
        severity=severity,
        min_turn=min_turn,
        prompt_template=prompt_template,
        choice_templates=["Option A.", "Option B.", "Option C."],
        can_kill_agent=can_kill_agent,
    )


def _minimal_archetypes() -> list[AdvisorArchetype]:
    out: list[AdvisorArchetype] = []
    for q in Quadrant:
        out.append(
            AdvisorArchetype(
                quadrant=q,
                role_name=f"Role {q.value}",
                name_pool=["Alpha", "Bravo", "Charlie"],
                description_template="A capable advisor.",
                verbal_tic="Speaks plainly.",
                portrait_options={"clothing_color": ["aabbcc"], "hair_color": ["112233"]},
            )
        )
    return out


def _minimal_resources() -> list[ResourceDef]:
    return [
        ResourceDef(
            id=f"res_{i}", name=f"Res {i}", icon="i",
            starting=50, max=100, critical_threshold=20,
        )
        for i in range(5)
    ]


def _setting_with_events(categories: list[EventCategory]) -> Setting:
    """Build a Setting with custom event_categories.

    The schema requires min_length=12 event_categories; pad with plausible
    filler categories so tests can target a small handful of edge cases.
    """
    # Pad to >= 12 if needed with innocuous low-severity filler that is
    # gated off before the tested turn so it won't distort outcomes.
    filler_needed = max(0, 12 - len(categories))
    filler = [
        _category(cid=f"filler_{i}", weight=0.1, severity=1, min_turn=99)
        for i in range(filler_needed)
    ]
    return Setting(
        id="test_setting_v1",
        name="Test Setting",
        lore="L" * 120,
        palette_accent="#1a2b3c",
        narrative_voice="Terse.",
        resources=_minimal_resources(),
        advisor_archetypes=_minimal_archetypes(),
        event_categories=[*categories, *filler],
    )


def _forced_escalation_category() -> EventCategory:
    return _category(
        cid="forced_escalation_test",
        tags=["forced_escalation", "moral"],
        severity=5,
        min_turn=5,
        weight=10.0,
        can_kill_agent=True,
    )


def _resolved(turn: int, event: Event) -> ResolvedEvent:
    return ResolvedEvent(
        turn_number=turn,
        event=event,
        chosen_proposal_id="prop-x",
        summary="did a thing",
    )


def _make_seed_event(sev: int, seed_id: UUID | None = None) -> Event:
    return Event(
        id=f"seeded-{uuid4().hex[:6]}",
        category="crisis",
        title="Seeded event",
        body="A seeded consequence fires.",
        severity=sev,
        origin=EventOrigin.SEED_FIRED,
        origin_seed_id=seed_id or uuid4(),
    )


# ---------------------------------------------------------------------------
# Seed priority
# ---------------------------------------------------------------------------


def test_seeded_events_take_priority() -> None:
    setting = _setting_with_events([
        _category(cid="natural_a", severity=2, min_turn=1),
        _forced_escalation_category(),
    ])
    state = _colony(turn_number=6)
    seeded = [_make_seed_event(sev=3)]

    result = select_event(state, setting, random.Random(1), seeded_events=seeded)

    assert result.origin is EventOrigin.SEED_FIRED
    assert result.id == seeded[0].id


def test_seed_priority_picks_highest_severity() -> None:
    setting = _setting_with_events([
        _category(cid="natural_a"),
        _forced_escalation_category(),
    ])
    state = _colony(turn_number=6)
    low = _make_seed_event(sev=2)
    high = _make_seed_event(sev=5)

    result = select_event(state, setting, random.Random(1), seeded_events=[low, high])

    assert result.severity == 5
    assert result.id == high.id


# ---------------------------------------------------------------------------
# Forced escalation window
# ---------------------------------------------------------------------------


def test_forced_escalation_fires_in_window() -> None:
    setting = _setting_with_events([
        _category(cid="natural_a", severity=2, min_turn=1),
        _forced_escalation_category(),
    ])
    state = _colony(turn_number=9)

    # Probability is 1.0 at turn 9 — any rng produces the forced event.
    for seed in range(20):
        result = select_event(state, setting, random.Random(seed))
        assert result.origin is EventOrigin.FORCED_ESCALATION
        assert result.category == "forced_escalation_test"


def test_forced_escalation_probability_rises_linearly() -> None:
    assert forced_escalation_probability(5) == 0.2
    assert abs(forced_escalation_probability(6) - 0.4) < 1e-9
    assert abs(forced_escalation_probability(7) - 0.6) < 1e-9
    assert abs(forced_escalation_probability(8) - 0.8) < 1e-9
    assert forced_escalation_probability(9) == 1.0
    assert forced_escalation_probability(4) == 0.0
    assert forced_escalation_probability(10) == 0.0
    assert forced_escalation_probability(0) == 0.0


def test_forced_escalation_fires_exactly_once_in_run() -> None:
    """Simulate 100 runs with fixed rng seeds; exactly one FORCED_ESCALATION
    event fires across turns 1-12 for each run under the natural pipeline.
    """
    setting = load_setting("arctic_station_v1")
    # Healthy colony so wealth-scaling stays consistent.
    resources = {
        r.id: Resource(
            id=r.id, name=r.name, icon=r.icon,
            value=r.max, max=r.max, critical_threshold=r.critical_threshold,
        )
        for r in setting.resources
    }
    agents = [_agent(name=f"A{i}") for i in range(4)]

    for trial in range(100):
        rng = random.Random(trial)
        event_log: list[ResolvedEvent] = []
        for turn in range(1, 13):
            state = _colony(
                turn_number=turn,
                event_log=list(event_log),
                resources=resources,
                agents=agents,
            )
            event = select_event(state, setting, rng)
            event_log.append(_resolved(turn, event))

        forced_count = sum(
            1 for r in event_log if r.event.origin is EventOrigin.FORCED_ESCALATION
        )
        assert forced_count == 1, (
            f"trial {trial}: expected exactly 1 forced_escalation, got {forced_count}"
        )


# ---------------------------------------------------------------------------
# Cooldown / min_turn / can_kill_agent filters
# ---------------------------------------------------------------------------


def test_cooldown_prevents_severity_5_back_to_back() -> None:
    categories = [
        _category(cid="sev5_a", severity=5, min_turn=4, weight=10.0, can_kill_agent=True),
        _category(cid="sev5_b", severity=5, min_turn=4, weight=10.0, can_kill_agent=True),
        _category(cid="sev3", severity=3, min_turn=1, weight=1.0),
        _forced_escalation_category(),
    ]
    setting = _setting_with_events(categories)

    # Previous turn had a severity-5 event. Also mark forced_escalation as
    # already fired so rule 2 doesn't trigger on this test turn (rule 2
    # bypasses the cooldown — we're targeting the rule-3 filter here).
    prev_event = Event(
        id="prev:1", category="sev5_a", title="t", body="b",
        severity=5, origin=EventOrigin.NATURAL,
    )
    already_forced = Event(
        id="fe:1", category="forced_escalation_test", title="t", body="b",
        severity=5, origin=EventOrigin.FORCED_ESCALATION,
    )
    log = [_resolved(turn=4, event=prev_event), _resolved(turn=3, event=already_forced)]
    state = _colony(turn_number=5, event_log=log)

    for seed in range(50):
        result = select_event(state, setting, random.Random(seed))
        assert result.severity != 5, f"seed {seed} returned severity-5: {result.category}"


def test_min_turn_filters_pool() -> None:
    categories = [
        _category(cid="early", severity=2, min_turn=1, weight=1.0),
        _category(cid="locked", severity=3, min_turn=5, weight=5.0),
        _forced_escalation_category(),
    ]
    setting = _setting_with_events(categories)
    state = _colony(turn_number=2)

    for seed in range(50):
        result = select_event(state, setting, random.Random(seed))
        assert result.category != "locked", (
            f"seed {seed}: min_turn=5 category fired on turn 2"
        )


def test_can_kill_agent_blocked_before_turn_4() -> None:
    # Use each launch setting; forced_escalation is itself can_kill but gated
    # by rule 2 (turn window) and min_turn, so turn 1-3 never sees it anyway.
    for setting_id in ("arctic_station_v1", "orbital_colony_v1", "desert_outpost_v1"):
        setting = load_setting(setting_id)
        for turn in (1, 2, 3):
            for seed in range(50):
                state = _colony(turn_number=turn)
                result = select_event(state, setting, random.Random(seed))
                # If the chosen event maps back to a can_kill category, fail.
                matched = next(
                    (c for c in setting.event_categories if c.id == result.category),
                    None,
                )
                if matched is None:
                    # respite fallback or ad-hoc categories are fine.
                    continue
                assert not matched.can_kill_agent, (
                    f"{setting_id} turn={turn} seed={seed}: "
                    f"can_kill_agent category {matched.id!r} fired"
                )


# ---------------------------------------------------------------------------
# Wealth scaling
# ---------------------------------------------------------------------------


def test_wealth_scaling_boosts_high_severity() -> None:
    """Aggregate draws across pristine vs depleted colonies; high-sev count
    should be measurably higher in the full-health run.
    """
    categories = [
        _category(cid="low_sev", severity=1, min_turn=1, weight=1.0),
        _category(cid="high_sev", severity=4, min_turn=1, weight=1.0),
        _forced_escalation_category(),
    ]
    setting = _setting_with_events(categories)

    # Pristine: maxed resources, full-health live agents.
    pristine_resources = {"fuel": _resource("fuel", value=100)}
    pristine_agents = [_agent(health=100)]
    depleted_resources = {"fuel": _resource("fuel", value=5)}
    depleted_agents = [_agent(health=5)]

    def count_high(resources, agents, label: int) -> int:
        high = 0
        for seed in range(500):
            state = _colony(
                turn_number=2,
                resources=resources,
                agents=agents,
            )
            # Use a seeded rng derived from the label so pristine/depleted
            # runs have different rng sequences (otherwise identical random
            # draws would make the wealth multiplier irrelevant for choices
            # that happen to be the same anyway).
            rng = random.Random(label * 10_000 + seed)
            evt = select_event(state, setting, rng)
            if evt.severity >= 3:
                high += 1
        return high

    high_pristine = count_high(pristine_resources, pristine_agents, 0)
    high_depleted = count_high(depleted_resources, depleted_agents, 1)

    assert high_pristine >= 1.3 * high_depleted, (
        f"wealth scaling did not measurably boost high-sev events: "
        f"pristine={high_pristine}, depleted={high_depleted}"
    )


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_selection_is_deterministic_with_same_rng() -> None:
    setting = _setting_with_events([
        _category(cid="alpha", weight=2.0, severity=2, min_turn=1),
        _category(cid="beta", weight=1.0, severity=3, min_turn=1),
        _forced_escalation_category(),
    ])
    state = _colony(turn_number=2)

    a = select_event(state, setting, random.Random(42))
    b = select_event(state, setting, random.Random(42))

    assert a.id == b.id
    assert a.category == b.category


# ---------------------------------------------------------------------------
# Empty-pool fallback
# ---------------------------------------------------------------------------


def test_empty_pool_returns_respite_fallback() -> None:
    # Every real event gated behind a future min_turn; only forced_escalation
    # (excluded from natural selection) is present otherwise.
    categories = [
        _category(cid=f"future_{i}", severity=2, min_turn=99)
        for i in range(5)
    ]
    categories.append(_forced_escalation_category())
    setting = _setting_with_events(categories)
    state = _colony(turn_number=2)

    result = select_event(state, setting, random.Random(7))

    assert result.category == "respite"
    assert result.severity == 1
    assert result.origin is EventOrigin.NATURAL


# ---------------------------------------------------------------------------
# has_forced_escalation_fired
# ---------------------------------------------------------------------------


def test_has_forced_escalation_fired_detects_prior() -> None:
    fe_event = Event(
        id="fe:1", category="forced_escalation_test", title="t", body="b",
        severity=5, origin=EventOrigin.FORCED_ESCALATION,
    )
    state = _colony(turn_number=6, event_log=[_resolved(turn=5, event=fe_event)])

    assert has_forced_escalation_fired(state) is True


def test_has_forced_escalation_fired_no_history() -> None:
    state = _colony(turn_number=6)
    assert has_forced_escalation_fired(state) is False
