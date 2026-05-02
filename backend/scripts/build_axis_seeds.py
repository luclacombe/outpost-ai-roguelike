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
# Event templates
# ---------------------------------------------------------------------------

_EVENT_TEMPLATES: dict[str, list[dict[str, Any]]] = {
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


# ---------------------------------------------------------------------------
# Coverage matrix
# ---------------------------------------------------------------------------

# 30 seeds: 10 per setting. Per setting:
#   2 grievance-0 (1 iterating, 1 gating)
#   2 grievance-3 (1 iterating, 1 gating)
#   2 grievance-7 (1 iterating, 1 gating)
#   2 grievance-9 (0 iterating, 2 gating)
#   2 mixed (varied per advisor)

_GRIEVANCE_PROFILES = [
    ("grievance0", {"ACCOUNTANT": 1, "OPERATOR": 0, "CONSCIENCE": 1, "CRUSADER": 2}),
    ("grievance0_b", {"ACCOUNTANT": 0, "OPERATOR": 2, "CONSCIENCE": 0, "CRUSADER": 1}),
    ("grievance3", {"ACCOUNTANT": 4, "OPERATOR": 3, "CONSCIENCE": 5, "CRUSADER": 3}),
    ("grievance3_b", {"ACCOUNTANT": 3, "OPERATOR": 5, "CONSCIENCE": 4, "CRUSADER": 4}),
    ("grievance7", {"ACCOUNTANT": 7, "OPERATOR": 6, "CONSCIENCE": 8, "CRUSADER": 6}),
    ("grievance7_b", {"ACCOUNTANT": 8, "OPERATOR": 7, "CONSCIENCE": 6, "CRUSADER": 7}),
    ("grievance9", {"ACCOUNTANT": 9, "OPERATOR": 9, "CONSCIENCE": 7, "CRUSADER": 8}),
    ("grievance9_b", {"ACCOUNTANT": 7, "OPERATOR": 9, "CONSCIENCE": 9, "CRUSADER": 8}),
    ("mixed", {"ACCOUNTANT": 0, "OPERATOR": 6, "CONSCIENCE": 8, "CRUSADER": 2}),
    ("mixed_b", {"ACCOUNTANT": 5, "OPERATOR": 1, "CONSCIENCE": 3, "CRUSADER": 7}),
]


# Iterating subset (8 seeds) — explicitly mapped per (setting, profile) so
# the count is exact. Distribution: 3 arctic + 2 orbital + 3 desert,
# spanning grievance bands 0–2 / 3–5 / 6–8 (the 9–10 band is gating-only).
_ITERATING_PROFILES_BY_SETTING = {
    "arctic_station_v1": {"grievance0", "grievance3", "grievance7"},
    "orbital_colony_v1": {"grievance0", "grievance3"},
    "desert_outpost_v1": {"grievance0", "grievance7", "mixed"},
}


def _build_seed(
    seed_id: str,
    setting_id: str,
    setting_pack: dict[str, tuple[str, str]],
    grievances: dict[str, int],
    event_dict: dict[str, Any],
    iterating: bool,
    profile_label: str,
) -> dict[str, Any]:
    digest = _stable_digest(seed_id)
    digest_user = _stable_digest(seed_id + ":user")
    game_id = UUID(int=digest & ((1 << 128) - 1))
    user_id = UUID(int=digest_user & ((1 << 128) - 1))
    agents = _build_agents(game_id, setting_pack, grievances)
    resources = _build_resources(setting_id)
    state = ColonyState(
        game_id=game_id,
        user_id=user_id,
        setting_id=setting_id,
        seed=digest & 0xFFFFFFFF,
        turn_number=4,
        max_turns=10,
        resources=resources,
        agents=agents,
        legitimacy=70,
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
        origin=EventOrigin.NATURAL,
        title=event_dict["title"],
        body=event_dict["body"],
    )
    return {
        "seed_id": seed_id,
        "iterating": iterating,
        "setting_id": setting_id,
        "profile_label": profile_label,
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
    # while these counts stay paired. Adding an 11th profile or a 4th event
    # template silently breaks the rotation.
    assert len(_GRIEVANCE_PROFILES) == 10, "profile count must stay 10"
    for setting_id in ("arctic_station_v1", "orbital_colony_v1", "desert_outpost_v1"):
        assert len(_EVENT_TEMPLATES[setting_id]) == 3, f"{setting_id} must have 3 event templates"

    written = 0
    iterating_count = 0
    for setting_id, pack in (
        ("arctic_station_v1", _ARCTIC),
        ("orbital_colony_v1", _ORBITAL),
        ("desert_outpost_v1", _DESERT),
    ):
        events = _EVENT_TEMPLATES[setting_id]
        iterating_set = _ITERATING_PROFILES_BY_SETTING[setting_id]
        for i, (g_label, grievances) in enumerate(_GRIEVANCE_PROFILES):
            event_dict = events[i % len(events)]
            iterating = g_label in iterating_set
            seed_id = f"{setting_id.split('_')[0]}_{g_label}_{event_dict['id']}"
            seed = _build_seed(
                seed_id, setting_id, pack, grievances, event_dict, iterating, g_label
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
