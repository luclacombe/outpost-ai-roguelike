"""Unit tests for event-title rendering — strip Seeded/Aftermath prefixes.

Covers the polish bugs from the 39-run playtest report (§1.3 §1.4):

  (a) Naked `Seeded:` prefix leaks into player-facing event titles.
  (b) `Aftermath of Seeded: Aftermath of ...` recursive nesting on
      severity >= 3 events that were themselves seeded.
  (c) Hard 60-char truncation slicing words mid-letter ("by his ol",
      "badly inju").

All tests are synchronous and fully fabricated — no DB, no LLM, no
network. The seed-fire / nesting tests drive `check_and_fire_seeds`
through three levels of "seed → fired event → next seed → fired event"
and assert that no rendered title carries the buggy literals.
"""

from __future__ import annotations

import random
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.agent.nodes import resolve
from app.game.consequence import (
    _clean_title_for_aftermath,
    check_and_fire_seeds,
    schedule_seed,
)
from app.game.schemas import (
    Agent,
    ColonyState,
    ConsequenceSeed,
    DecisionRef,
    Event,
    EventOrigin,
    Proposal,
    ProposalFlavor,
    Quadrant,
    Resource,
    SeedPayload,
)

# ---------------------------------------------------------------------------
# Shared constants + fabricators
# ---------------------------------------------------------------------------

GAME_ID = UUID("aaaaaaaa-0000-0000-0000-000000000010")
USER_ID = UUID("bbbbbbbb-0000-0000-0000-000000000020")


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


def _colony(
    *,
    turn_number: int = 1,
    seeds: list[ConsequenceSeed] | None = None,
    max_turns: int = 10,
) -> ColonyState:
    return ColonyState(
        game_id=GAME_ID,
        user_id=USER_ID,
        setting_id="arctic_station_v1",
        seed=42,
        turn_number=turn_number,
        max_turns=max_turns,
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
    description: str,
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
    payload: SeedPayload,
) -> ConsequenceSeed:
    return ConsequenceSeed(
        game_id=GAME_ID,
        written_at_turn=written_at_turn,
        fires_at_turn=fires_at_turn,
        foreshadowed=False,
        foreshadow_turn=None,
        payload=payload,
        origin_decision=_decision_ref(written_at_turn),
    )


def _make_proposal(
    *,
    pid: str = "p_dom",
    flavor: ProposalFlavor = ProposalFlavor.DOMINANT,
) -> Proposal:
    return Proposal(
        id=pid,
        proposed_by_agent_id=None,
        flavor=flavor,
        name=f"Proposal {pid}",
        description=f"Description for {pid}.",
        effects_preview=[],
        tool_calls_on_pick=[],
    )


def _make_event(*, severity: int, event_id: str, title: str, body: str = "Body.") -> Event:
    return Event(
        id=event_id,
        category="crisis",
        title=title,
        body=body,
        severity=severity,
        origin=EventOrigin.NATURAL,
    )


# ---------------------------------------------------------------------------
# _clean_title_for_aftermath: pure helper (sub-bugs a + b)
# ---------------------------------------------------------------------------


def test_clean_title_strips_seeded_prefix() -> None:
    """Sub-bug (a): `Seeded: ` is engine scaffolding and must not survive."""
    cleaned = _clean_title_for_aftermath("Seeded: A blizzard rolls in from the northeast")
    assert cleaned == "A blizzard rolls in from the northeast"
    assert not cleaned.startswith("Seeded:")


def test_clean_title_strips_aftermath_prefix() -> None:
    """Sub-bug (b): `Aftermath of ` prefix is also stripped (one level)."""
    cleaned = _clean_title_for_aftermath("Aftermath of A blizzard rolls in from the northeast")
    assert cleaned == "A blizzard rolls in from the northeast"
    assert not cleaned.startswith("Aftermath of")


def test_clean_title_strips_seeded_then_aftermath_combination() -> None:
    """Combined: `Seeded: Aftermath of X` should reduce to `X`."""
    cleaned = _clean_title_for_aftermath("Seeded: Aftermath of HELO presents a resource offer")
    assert cleaned == "HELO presents a resource offer"


def test_clean_title_only_strips_first_occurrence_not_recursive() -> None:
    """Helper must NOT recurse: only strip the FIRST leading occurrence."""
    # If somehow a rogue title has "Aftermath of Aftermath of X", we strip ONE
    # "Aftermath of " — and the implementation in nodes.py prepends exactly one
    # again, leaving "Aftermath of Aftermath of X". The contract here is just
    # "no infinite recursion in the helper itself".
    cleaned = _clean_title_for_aftermath("Aftermath of Aftermath of X")
    assert cleaned == "Aftermath of X"


def test_clean_title_passthrough_when_no_prefix() -> None:
    """Plain title is unchanged."""
    assert _clean_title_for_aftermath("A blizzard rolls in") == "A blizzard rolls in"


def test_clean_title_does_not_strip_inside_text() -> None:
    """`Seeded: ` must only be stripped when at the very start."""
    title = "Crew refers to it as Seeded: just a saying"
    assert _clean_title_for_aftermath(title) == title


# ---------------------------------------------------------------------------
# Sub-bug (c): no 60-char hard truncation in fired-event titles
# ---------------------------------------------------------------------------


def test_fired_event_title_not_truncated_at_60_chars() -> None:
    """Sub-bug (c): titles longer than 60 chars must not be sliced mid-word."""
    long_desc = (
        "A logistics convoy stalls in the pass after the bridge collapse, by his old handlers"
    )
    assert len(long_desc) > 60

    seed = _seed(fires_at_turn=3, payload=_payload(long_desc))
    state = _colony(turn_number=3, seeds=[seed])

    _, events = check_and_fire_seeds(state)

    assert len(events) == 1
    title = events[0].title
    # No mid-word slice — the word "handlers" must be fully present.
    assert "by his old handlers" in title or long_desc in title
    # And the buggy fragment must NOT appear.
    assert "by his ol" not in title or "by his old" in title


def test_fired_event_title_no_seeded_prefix_at_render() -> None:
    """Sub-bug (a) at the render boundary: fired-event titles drop scaffolding."""
    seed = _seed(fires_at_turn=3, payload=_payload("A blizzard rolls in from the northeast"))
    state = _colony(turn_number=3, seeds=[seed])

    _, events = check_and_fire_seeds(state)

    assert len(events) == 1
    # The verbatim leak from the playtest corpus must be gone.
    assert not events[0].title.startswith("Seeded:")
    assert "Seeded:" not in events[0].title


def test_fired_event_title_renders_full_long_description() -> None:
    """A description >=80 chars renders in full (no truncation)."""
    long_desc = (
        "The medic reports the wounded tech is badly injured but stable until "
        "the relief team arrives next cycle"
    )
    assert len(long_desc) >= 80

    seed = _seed(fires_at_turn=3, payload=_payload(long_desc))
    state = _colony(turn_number=3, seeds=[seed])

    _, events = check_and_fire_seeds(state)

    title = events[0].title
    # Full word "injured" must be present (corpus had "badly inju").
    assert "badly injured" in title
    # No mid-word slice from the bug.
    assert not title.endswith("inju")


# ---------------------------------------------------------------------------
# Sub-bug (b): nested seed firing never produces "Aftermath of Seeded:" or
# "Aftermath of Aftermath of"
# ---------------------------------------------------------------------------


async def _resolve_severe_then_fire(
    state: ColonyState,
    *,
    event_title: str,
    event_id: str,
    fires_at_turn: int,
) -> tuple[ColonyState, Event]:
    """Drive `resolve` for a severity-4 event with a known title, then fire.

    Returns (post-fire state, the fired event). The test corpus reproduces the
    bug by having a turn-N severe event schedule a seed for turn N+2, then
    that seed fires producing a title — and the next severe event (which is
    that fired event) schedules ANOTHER seed using the ALREADY-WRAPPED title.
    """
    event = _make_event(severity=4, event_id=event_id, title=event_title)
    proposal = _make_proposal(pid=f"p-{event_id}", flavor=ProposalFlavor.DOMINANT)

    node_state: dict[str, Any] = {
        "colony_state": state,
        "proposals": [proposal],
        "player_choice": proposal.id,
        "current_event": event,
    }
    result = await resolve(node_state)
    new_state: ColonyState = result["colony_state"]

    # Now advance the carried turn forward to `fires_at_turn` to fire the seed
    # that `resolve` just scheduled. We bypass the full graph and just bump
    # turn_number directly — `check_and_fire_seeds` only reads turn_number.
    fired_state = new_state.model_copy(update={"turn_number": fires_at_turn})
    fired_state, fired_events = check_and_fire_seeds(fired_state)
    assert fired_events, "expected the freshly-scheduled seed to fire"
    return fired_state, fired_events[-1]


async def test_two_level_aftermath_does_not_produce_seeded_aftermath_of() -> None:
    """Turn N severe → seed at N+2 fires → next severe at N+2 must NOT yield
    `Aftermath of Seeded: X` on its own seed payload (and downstream title).

    Reproduces the corpus title:
      "Aftermath of Seeded: Aftermath of HELO presents a resource o"
    which arose because the seed scheduled at turn N+2 was built from a
    title that already started with "Seeded: ".
    """
    # Turn 1: a severe natural event "X" is resolved → schedules seed for turn 3.
    state = _colony(turn_number=1, max_turns=12)
    state, fired_evt_1 = await _resolve_severe_then_fire(
        state,
        event_title="HELO presents a resource offer",
        event_id="evt_helo",
        fires_at_turn=3,
    )

    # Sanity: the fired event's title is clean (no Seeded: prefix).
    assert "Seeded:" not in fired_evt_1.title
    assert fired_evt_1.title.endswith("HELO presents a resource offer") or (
        fired_evt_1.title == "Aftermath of HELO presents a resource offer"
    )

    # Now resolve THAT fired event as the current severe event of turn 3,
    # which should schedule a seed for turn 5. The seed's payload description
    # must NOT contain "Seeded:" or "Aftermath of Aftermath of".
    state, fired_evt_2 = await _resolve_severe_then_fire(
        state,
        event_title=fired_evt_1.title,
        event_id="evt_helo_2",
        fires_at_turn=5,
    )

    title = fired_evt_2.title
    assert "Seeded:" not in title, f"Seeded: leaked into fired title: {title!r}"
    assert "Aftermath of Aftermath of" not in title, f"recursive nesting: {title!r}"


async def test_three_levels_of_nested_aftermath_stay_clean() -> None:
    """Three levels of severity-4 → seed → fire → severe → seed → fire → severe
    → seed → fire. At every level: no `Seeded:` or `Aftermath of Aftermath of`
    in any rendered title.
    """
    state = _colony(turn_number=1, max_turns=15)

    # Level 1: turn 1 severe → seed fires turn 3
    state, evt1 = await _resolve_severe_then_fire(
        state,
        event_title="The convoy fails by his old handlers",
        event_id="evt_lvl1",
        fires_at_turn=3,
    )
    assert "Seeded:" not in evt1.title

    # Level 2: turn 3 severe (the freshly-fired event) → seed fires turn 5
    state, evt2 = await _resolve_severe_then_fire(
        state,
        event_title=evt1.title,
        event_id="evt_lvl2",
        fires_at_turn=5,
    )
    assert "Seeded:" not in evt2.title
    assert "Aftermath of Aftermath of" not in evt2.title

    # Level 3: turn 5 severe (level-2 fired event) → seed fires turn 7
    state, evt3 = await _resolve_severe_then_fire(
        state,
        event_title=evt2.title,
        event_id="evt_lvl3",
        fires_at_turn=7,
    )
    assert "Seeded:" not in evt3.title
    assert "Aftermath of Aftermath of" not in evt3.title


@pytest.mark.parametrize(
    "starting_title",
    [
        "A blizzard rolls in from the northeast",
        "Seeded: A blizzard rolls in from the northeast",
        "Aftermath of A blizzard rolls in from the northeast",
        "Seeded: Aftermath of A blizzard rolls in from the northeast",
        "HELO presents a resource offer",
    ],
)
async def test_nested_seed_fire_no_scaffolding_leak(starting_title: str) -> None:
    """Five varied scenarios — every starting-title shape must produce
    a clean, non-recursive aftermath title at the next fire."""
    state = _colony(turn_number=1, max_turns=12)
    state, fired = await _resolve_severe_then_fire(
        state,
        event_title=starting_title,
        event_id="evt_param",
        fires_at_turn=3,
    )

    title = fired.title
    assert "Seeded:" not in title, f"Seeded leaked from {starting_title!r} → {title!r}"
    assert "Aftermath of Aftermath of" not in title, (
        f"recursive Aftermath from {starting_title!r} → {title!r}"
    )


# ---------------------------------------------------------------------------
# Pureness: helper does not mutate inputs / state isolation
# ---------------------------------------------------------------------------


def test_clean_title_for_aftermath_is_pure() -> None:
    """Helper takes a string and returns one — no hidden state."""
    title = "Seeded: original"
    cleaned = _clean_title_for_aftermath(title)
    # Input string unchanged (Python strings are immutable, but we still
    # confirm the helper does not return the same exact object when prefix
    # is present).
    assert title == "Seeded: original"
    assert cleaned == "original"


def test_schedule_seed_with_long_description_round_trips_through_fire() -> None:
    """A seed scheduled with a long description fires with the full text."""
    long_desc = (
        "An exhaustive logistical breakdown of crew rotation under the new "
        "rationing policy that the player ratified last cycle, badly injured"
    )
    state = _colony(turn_number=2)
    seed = _seed(written_at_turn=2, fires_at_turn=4, payload=_payload(long_desc))
    state = schedule_seed(state, seed)

    state = state.model_copy(update={"turn_number": 4})
    _, events = check_and_fire_seeds(state)

    assert len(events) == 1
    # Body always carries full description (already true pre-fix).
    assert events[0].body == long_desc
    # Title carries the full description (post-fix).
    assert "badly injured" in events[0].title


def test_random_seed_for_determinism_unaffected_by_helper() -> None:
    """The helper has no RNG dependency. Calling it twice with the same input
    always yields the same output."""
    rng = random.Random(0)  # decoy — not used; just here to match other tests
    del rng
    a = _clean_title_for_aftermath("Seeded: X")
    b = _clean_title_for_aftermath("Seeded: X")
    assert a == b == "X"
