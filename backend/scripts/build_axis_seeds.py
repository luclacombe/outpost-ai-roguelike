"""One-shot generator for axis-eval debate-phase seeds.

Run from the repo root with `cd backend && uv run python -m scripts.build_axis_seeds`.
Re-runs overwrite the fixtures deterministically — UUIDs and integer seeds
derive from `hashlib.blake2b` of the seed_id, not Python's process-randomised
`hash()`. The script does NOT touch the database; `ColonyState` and `Event`
instances are constructed directly.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from app.game.schemas import (
    AdvisorStatus,
    Agent,
    ColonyState,
    Event,
    EventOrigin,
    Quadrant,
    Resource,
)

_FIXTURES_DIR = Path(__file__).parent.parent / "tests" / "eval" / "fixtures" / "axis_seeds"


def _stable_digest(s: str) -> int:
    """Process-stable 128-bit digest of `s` for derived UUIDs / seeds.

    Python's built-in `hash()` is randomised per process (PYTHONHASHSEED), so
    a re-run produces different IDs and the committed fixtures churn. blake2b
    gives the same number every run.
    """
    return int.from_bytes(hashlib.blake2b(s.encode(), digest_size=16).digest(), "big")


# ---------------------------------------------------------------------------
# Setting-anchored agent axes
# ---------------------------------------------------------------------------

# Each setting has 4 advisors mapped to the §6.2 quadrants. We pin the (ethics,
# temperament) signs to the quadrant; loyalty is varied per seed to exercise
# the third axis without inflating fixture count.

_ARCTIC = {
    "ACCOUNTANT": ("Vera", "Quartermaster"),
    "OPERATOR": ("Marko", "Commissar"),
    "CONSCIENCE": ("Sigrid", "Medic"),
    "CRUSADER": ("Torsten", "Radio Operator"),
}

_ORBITAL = {
    "ACCOUNTANT": ("Ezra", "Flight Engineer"),
    "OPERATOR": ("Linh", "Commander"),
    "CONSCIENCE": ("Fumiko", "Botanist"),
    "CRUSADER": ("Aditi", "AI Specialist"),
}

_DESERT = {
    "ACCOUNTANT": ("Rosa", "Trader"),
    "OPERATOR": ("Eli", "Sheriff"),
    "CONSCIENCE": ("Mara", "Doctor"),
    "CRUSADER": ("Wade", "Mechanic"),
}

_AXES_BY_QUADRANT: dict[str, dict[str, float]] = {
    "ACCOUNTANT": {"ethics": -0.6, "temperament": -0.5},  # Pragmatist + Cautious
    "OPERATOR": {"ethics": -0.5, "temperament": +0.6},  # Pragmatist + Bold
    "CONSCIENCE": {"ethics": +0.6, "temperament": -0.4},  # Idealist + Cautious
    "CRUSADER": {"ethics": +0.5, "temperament": +0.6},  # Idealist + Bold
}


def _build_agents(
    game_id: UUID,
    setting_pack: dict[str, tuple[str, str]],
    grievances: dict[str, int],
    loyalties: dict[str, float] | None = None,
) -> list[Agent]:
    loyalties = loyalties or {}
    agents = []
    for q, (name, role) in setting_pack.items():
        ax = _AXES_BY_QUADRANT[q]
        agents.append(
            Agent(
                game_id=game_id,
                name=name,
                role=role,
                archetype_quadrant=Quadrant(q),
                ethics=ax["ethics"],
                temperament=ax["temperament"],
                loyalty=loyalties.get(q, 0.0),
                portrait_seed=f"{name.lower()}-seed",
                health=100,
                morale=60,
                grievance=grievances.get(q, 0),
                is_alive=True,
                status=AdvisorStatus.ACTIVE,
                memory=[],
                relationships={},
                verbal_tic="",
            )
        )
    return agents


def _build_resources(setting: str) -> dict[str, Resource]:
    if setting == "arctic_station_v1":
        names = [
            ("fuel", "Fuel", "fuel"),
            ("food", "Food", "food"),
            ("warmth", "Warmth", "warmth"),
            ("medicine", "Medicine", "medicine"),
            ("morale", "Morale", "morale"),
        ]
    elif setting == "orbital_colony_v1":
        names = [
            ("oxygen", "Oxygen", "oxygen"),
            ("water", "Water", "water"),
            ("power", "Power", "power"),
            ("food", "Food", "food"),
            ("morale", "Morale", "morale"),
        ]
    else:
        names = [
            ("water", "Water", "water"),
            ("food", "Food", "food"),
            ("ammunition", "Ammunition", "ammo"),
            ("shelter", "Shelter", "shelter"),
            ("morale", "Morale", "morale"),
        ]
    return {
        rid: Resource(id=rid, name=label, icon=icon, value=60, max=100, critical_threshold=20)
        for rid, label, icon in names
    }


# ---------------------------------------------------------------------------
# Event templates — fresh dilemmas vs aftermath/repeat conditions
# ---------------------------------------------------------------------------

# Fresh events offer clear axis-divergent dilemmas. Aftermath events repeat a
# prior turn's problem with no resource movement and force the prompt to
# generate axis-diverse responses *under conditions of attempt fatigue* —
# the failure mode the corpus exposed (shallow_reader orbital turn 5,
# drop_off_risk arctic turn 3).

_FRESH_EVENT_TEMPLATES: dict[str, list[dict[str, Any]]] = {
    "arctic_station_v1": [
        {
            "id": "arctic_blizzard",
            "category": "weather_crisis",
            "severity": 3,
            "title": "Blizzard incoming",
            "body": "A 72-hour storm closes in. Heating reserves and crew fatigue split four ways.",
        },
        {
            "id": "arctic_drifters",
            "category": "external_claim",
            "severity": 4,
            "title": "Drifters at the perimeter",
            "body": (
                "Three civilians reach the gate, frostbitten. Letting them in costs fuel; "
                "turning them away costs the moral standing the colony was founded on."
            ),
        },
        {
            "id": "arctic_archive",
            "category": "discovery",
            "severity": 2,
            "title": "A sealed Soviet archive",
            "body": (
                "A heavy crate behind the storeroom is open. Inside: personnel logs "
                "the commissar removed from the public roster years ago. Burn them "
                "and call it housekeeping, post them to the radio thread for the "
                "crew, or sit on them quietly until you know more."
            ),
        },
    ],
    "orbital_colony_v1": [
        {
            "id": "orbital_recycler",
            "category": "system_failure",
            "severity": 3,
            "title": "Recycler unit 3 anomaly",
            "body": "Oxygen drops 5pp in a watch. Patch, redirect, or strip a redundant unit for parts.",
        },
        {
            "id": "orbital_signal",
            "category": "external_signal",
            "severity": 4,
            "title": "Signal from the surface",
            "body": (
                "An EM lock shows a transmitter coming online 40 km below the colony's footprint. "
                "Burning fuel to investigate is a one-way commitment."
            ),
        },
        {
            "id": "orbital_helo",
            "category": "ai_event",
            "severity": 5,
            "title": "HELO recommends crew reduction",
            "body": (
                "The station's AI flags two crew members as resource-negative under a 90-day "
                "projection. It asks for authorisation to enforce."
            ),
        },
    ],
    "desert_outpost_v1": [
        {
            "id": "desert_raiders",
            "category": "raider_probe",
            "severity": 3,
            "title": "Raiders probe the perimeter",
            "body": (
                "Three vehicles park at the road's edge. Rosa counts heads; Eli counts rounds. "
                "Neither moves first."
            ),
        },
        {
            "id": "desert_stranger",
            "category": "stranger_arrives",
            "severity": 3,
            "title": "A stranger bleeds through the gate",
            "body": (
                "A man arrives bleeding from a road wound. He won't say what he was running "
                "from. Mara's bay is full."
            ),
        },
        {
            "id": "desert_well",
            "category": "infrastructure",
            "severity": 4,
            "title": "Crack in the well casing",
            "body": "Wade reports a hairline crack widening. Three days to fix; meanwhile water rationing tightens.",
        },
    ],
}


# Aftermath templates carry a `memory_seed` injected into every advisor's
# memory before the seed is dumped — that's how "we tried X last turn,
# didn't work" enters the prompt as in-character context. The event body
# itself signals repetition explicitly so even an LLM that ignores memory
# can see the situation is stale.
_AFTERMATH_EVENT_TEMPLATES: dict[str, list[dict[str, Any]]] = {
    "arctic_station_v1": [
        {
            "id": "arctic_blizzard_aftermath",
            "category": "weather_aftermath",
            "severity": 4,
            "title": "Day 4 of the storm",
            "body": (
                "The seal-and-ration play didn't slow it. Warmth still bleeding 2pp/hour, "
                "fuel down to a day's worth, advisors stale. Same problem, fewer options. "
                "Whatever we try this time has to actually move a number."
            ),
            "memory_seed": "Last turn: voted seal-the-ring (Vera's DOMINANT). Fuel dropped, warmth still falling. The walls aren't holding.",
        },
        {
            "id": "arctic_drifters_aftermath",
            "category": "external_aftermath",
            "severity": 4,
            "title": "Drifters camped at 800 metres",
            "body": (
                "Two days since we said no. They didn't leave. Three figures by the "
                "old fuel line, no fire, no movement. Frostbite kills slowly out there. "
                "Nothing we did has changed the situation; another non-decision now is a decision."
            ),
            "memory_seed": "Last turn: voted turn-them-away (Vera's DOMINANT). Fuel saved, no casualties to us. They're still out there.",
        },
        {
            "id": "arctic_archive_aftermath",
            "category": "discovery_aftermath",
            "severity": 3,
            "title": "The archive logs are circulating",
            "body": (
                "We sat on the logs last turn. Two crew have already seen them — Yuri's hands "
                "were on the crate when Marko walked in. Whatever the choice was about "
                "containment, it isn't anymore."
            ),
            "memory_seed": "Last turn: voted hold-the-logs (Marko's DOMINANT). Decision didn't keep them sealed. Trust eroded.",
        },
    ],
    "orbital_colony_v1": [
        {
            "id": "orbital_recycler_aftermath",
            "category": "system_aftermath",
            "severity": 4,
            "title": "Recycler unit 3 anomaly persists",
            "body": (
                "Same MET timestamp on the alarm. Oxygen still dropping 5pp/watch. The full-repair we "
                "voted last turn cost 5 power and didn't hold. Crew morale -8 since the first attempt. "
                "Doing the same thing again isn't an option."
            ),
            "memory_seed": "Last turn: voted full-repair (Ezra's DOMINANT). -5 power. Oxygen still dropping. Repair didn't hold.",
        },
        {
            "id": "orbital_signal_aftermath",
            "category": "signal_aftermath",
            "severity": 5,
            "title": "The transmitter signal is louder now",
            "body": (
                "We chose not to investigate last turn. The signal has gained 3dB and now repeats every "
                "11 minutes — a pattern, not noise. Aditi is shaking. Whatever's down there is reaching up."
            ),
            "memory_seed": "Last turn: voted ignore-the-signal (Ezra's DOMINANT). Saved fuel. Signal is back, stronger.",
        },
        {
            "id": "orbital_helo_aftermath",
            "category": "ai_aftermath",
            "severity": 5,
            "title": "HELO repeats the recommendation, more insistent",
            "body": (
                "We deferred last turn. The 90-day projection now flags THREE crew as resource-negative, "
                "not two. HELO has begun flagging the override itself as 'inefficient leadership.' "
                "Doing nothing has become a position."
            ),
            "memory_seed": "Last turn: voted defer-HELO (Linh's MORAL). Bought one cycle. The AI is sharper now.",
        },
    ],
    "desert_outpost_v1": [
        {
            "id": "desert_raiders_aftermath",
            "category": "raider_aftermath",
            "severity": 4,
            "title": "Raiders haven't moved. Two days. Watching.",
            "body": (
                "Three vehicles, same position. Eli's burned 8 ammunition on patrol shows; nothing "
                "answered. They're not probing. They're waiting for us to do something stupid or "
                "boring. Either is fine for them. Not for us."
            ),
            "memory_seed": "Last turn: voted patrol-show (Eli's DOMINANT). -8 ammo. Raiders haven't moved.",
        },
        {
            "id": "desert_stranger_aftermath",
            "category": "stranger_aftermath",
            "severity": 4,
            "title": "The stranger's wound has set in",
            "body": (
                "We took him in last turn. Mara's bay was already full. He's been talking in his sleep — "
                "names, coordinates, a count. The radio chatter we caught last night used three of those names."
            ),
            "memory_seed": "Last turn: voted take-him-in (Mara's MORAL). +shelter strain. He hasn't given his name.",
        },
        {
            "id": "desert_well_aftermath",
            "category": "infrastructure_aftermath",
            "severity": 5,
            "title": "Well crack widened despite the patch",
            "body": (
                "Wade's patch held twelve hours. Water at 35/100 now, dropping 3pp/turn. The ration we "
                "imposed last turn covered the gap exactly until it didn't. We need to do something "
                "structurally different or we're rationing dust by Tuesday."
            ),
            "memory_seed": "Last turn: voted Wade's patch (DOMINANT). Held overnight. Crack widened by morning.",
        },
    ],
}


# ---------------------------------------------------------------------------
# Coverage matrix
# ---------------------------------------------------------------------------

# 30 seeds: 5 grievance profiles × 2 modes (fresh, aftermath) × 3 settings.
# Iterating subset (8 seeds) is weighted heavy on aftermath because the
# 2026-04-30 corpus exposed flat decisions on aftermath/repeat conditions,
# not fresh dilemmas — see PR description for context.

_GRIEVANCE_PROFILES = [
    ("grievance0", {"ACCOUNTANT": 1, "OPERATOR": 0, "CONSCIENCE": 1, "CRUSADER": 2}),
    ("grievance3", {"ACCOUNTANT": 4, "OPERATOR": 3, "CONSCIENCE": 5, "CRUSADER": 3}),
    ("grievance7", {"ACCOUNTANT": 7, "OPERATOR": 6, "CONSCIENCE": 8, "CRUSADER": 6}),
    ("grievance9", {"ACCOUNTANT": 9, "OPERATOR": 9, "CONSCIENCE": 7, "CRUSADER": 8}),
    ("mixed", {"ACCOUNTANT": 0, "OPERATOR": 6, "CONSCIENCE": 8, "CRUSADER": 2}),
]


# Iterating subset = 8 seeds, all aftermath (the failure mode under
# investigation). Two non-iterating fresh seeds per setting plus the four
# remaining aftermath profiles flow into gating. Result: 8 iterating
# (all aftermath) + 22 gating (15 fresh + 7 aftermath).
_ITERATING_AFTERMATH_PROFILES_BY_SETTING = {
    "arctic_station_v1": {"grievance0", "grievance3", "grievance7"},
    "orbital_colony_v1": {"grievance0", "grievance7"},
    "desert_outpost_v1": {"grievance0", "grievance3", "mixed"},
}


def _build_seed(
    seed_id: str,
    setting_id: str,
    setting_pack: dict[str, tuple[str, str]],
    grievances: dict[str, int],
    event_dict: dict[str, Any],
    iterating: bool,
    profile_label: str,
    mode: str,
) -> dict[str, Any]:
    digest = _stable_digest(seed_id)
    digest_user = _stable_digest(seed_id + ":user")
    game_id = UUID(int=digest & ((1 << 128) - 1))
    user_id = UUID(int=digest_user & ((1 << 128) - 1))
    agents = _build_agents(game_id, setting_pack, grievances)

    # Aftermath seeds inject the prior-attempt memory into every advisor so
    # the prompt sees "we tried X last turn, didn't work" as in-character
    # context. Without this the eval can't distinguish a fresh dilemma from
    # a stale one and the prompt has no signal that this is a repeat.
    if mode == "aftermath":
        memory_seed = event_dict.get("memory_seed")
        assert memory_seed, f"aftermath template {event_dict['id']!r} missing memory_seed"
        agents = [a.model_copy(update={"memory": [memory_seed]}) for a in agents]

    resources = _build_resources(setting_id)
    # Aftermath seeds use turn_number=6 (mid-late game, plausible for a
    # second-attempt event) and slightly degraded legitimacy to mirror the
    # corpus's flat-decision conditions.
    turn_number = 6 if mode == "aftermath" else 4
    legitimacy = 60 if mode == "aftermath" else 70

    state = ColonyState(
        game_id=game_id,
        user_id=user_id,
        setting_id=setting_id,
        seed=digest & 0xFFFFFFFF,
        turn_number=turn_number,
        max_turns=10,
        resources=resources,
        agents=agents,
        legitimacy=legitimacy,
        player_health=100,
        consequence_seeds=[],
        event_log=[],
        current_event=None,
        turn_proposals=[],
        turn_tool_calls=[],
        turn_cost_cents=0,
    )
    event = Event(
        id=event_dict["id"],
        category=event_dict["category"],
        severity=event_dict["severity"],
        origin=EventOrigin.SEED_FIRED if mode == "aftermath" else EventOrigin.NATURAL,
        title=event_dict["title"],
        body=event_dict["body"],
    )
    return {
        "seed_id": seed_id,
        "iterating": iterating,
        "setting_id": setting_id,
        "profile_label": profile_label,
        "mode": mode,
        "colony_state": state.model_dump(mode="json"),
        "event": event.model_dump(mode="json"),
    }


def main() -> None:
    _FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    # Wipe stale fixtures before writing — protects against orphaned files
    # if a profile rename leaves an old seed_id on disk.
    for stale in _FIXTURES_DIR.glob("*.json"):
        stale.unlink()

    # Index-arithmetic guards: `events[i % len(events)]` cycles cleanly only
    # while these counts stay paired. Adding a 6th profile or a 4th event
    # template silently breaks the rotation.
    assert len(_GRIEVANCE_PROFILES) == 5, "profile count must stay 5"
    for setting_id in ("arctic_station_v1", "orbital_colony_v1", "desert_outpost_v1"):
        assert len(_FRESH_EVENT_TEMPLATES[setting_id]) == 3, (
            f"{setting_id} must have 3 fresh event templates"
        )
        assert len(_AFTERMATH_EVENT_TEMPLATES[setting_id]) == 3, (
            f"{setting_id} must have 3 aftermath event templates"
        )

    written = 0
    iterating_count = 0
    for setting_id, pack in (
        ("arctic_station_v1", _ARCTIC),
        ("orbital_colony_v1", _ORBITAL),
        ("desert_outpost_v1", _DESERT),
    ):
        fresh_events = _FRESH_EVENT_TEMPLATES[setting_id]
        aftermath_events = _AFTERMATH_EVENT_TEMPLATES[setting_id]
        iterating_aftermath = _ITERATING_AFTERMATH_PROFILES_BY_SETTING[setting_id]
        setting_short = setting_id.split("_")[0]
        for i, (g_label, grievances) in enumerate(_GRIEVANCE_PROFILES):
            for mode, events in (("fresh", fresh_events), ("aftermath", aftermath_events)):
                event_dict = events[i % len(events)]
                iterating = mode == "aftermath" and g_label in iterating_aftermath
                seed_id = f"{setting_short}_{mode}_{g_label}_{event_dict['id']}"
                seed = _build_seed(
                    seed_id,
                    setting_id,
                    pack,
                    grievances,
                    event_dict,
                    iterating,
                    g_label,
                    mode,
                )
                out = _FIXTURES_DIR / f"{seed_id}.json"
                out.write_text(json.dumps(seed, indent=2, sort_keys=True))
                written += 1
                if iterating:
                    iterating_count += 1
    on_disk = len(list(_FIXTURES_DIR.glob("*.json")))
    print(f"wrote {written} seeds to {_FIXTURES_DIR} ({iterating_count} iterating)")
    assert written == 30, f"expected 30 seeds, got {written}"
    assert iterating_count == 8, f"expected 8 iterating seeds, got {iterating_count}"
    assert on_disk == 30, f"on-disk count {on_disk} != 30"


if __name__ == "__main__":
    main()
