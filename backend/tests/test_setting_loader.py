"""
Tests for the setting YAML loader and Setting schema.

All tests are sync (no asyncio needed — loader is pure I/O + validation).
"""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from app.game.schemas import Quadrant
from app.game.setting_loader import (
    AdvisorArchetype,
    Setting,
    list_available_settings,
    load_setting,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ALL_QUADRANTS = set(Quadrant)
ALL_SETTING_IDS = {"arctic_station_v1", "orbital_colony_v1", "desert_outpost_v1"}


def _minimal_valid_setting_dict() -> dict:
    """Return a minimal valid Setting payload for mutation in negative tests."""
    return {
        "id": "test_setting_v1",
        "name": "Test Setting",
        "lore": "A" * 120,  # 100-char minimum
        "palette_accent": "#1a2b3c",
        "narrative_voice": "Terse and plain.",
        "player_title": "Director",
        "resources": [
            {
                "id": f"res_{i}",
                "name": f"Res {i}",
                "icon": "icon",
                "starting": 50,
                "max": 100,
                "critical_threshold": 20,
            }
            for i in range(5)
        ],
        "advisor_archetypes": [
            {
                "quadrant": q.value,
                "role_name": f"Role {q.value}",
                "name_pool": ["Alice", "Bob", "Carol"],
                "description_template": "A capable advisor.",
                "verbal_tic": "Speaks carefully.",
                "portrait_options": {"hair_color": ["ffffff"]},
            }
            for q in Quadrant
        ],
        "event_categories": [
            {
                "id": f"event_{i:02d}",
                "weight": 1.0,
                "tags": ["general"],
                "severity": 2,
                "min_turn": 1,
                "prompt_template": f"Something happens in event {i}.",
                "choice_templates": ["Option A", "Option B", "Option C"],
                "can_kill_agent": False,
            }
            for i in range(12)
        ],
    }


# ---------------------------------------------------------------------------
# Load tests — one per setting
# ---------------------------------------------------------------------------


def test_load_arctic_setting():
    setting = load_setting("arctic_station_v1")

    assert setting.name == "Vorgrad Station"
    assert setting.id == "arctic_station_v1"
    assert len(setting.resources) == 5
    assert len(setting.advisor_archetypes) == 4
    assert len(setting.event_categories) == 13

    resource_ids = {r.id for r in setting.resources}
    assert resource_ids == {"fuel", "food", "warmth", "medicine", "morale"}


def test_load_orbital_setting():
    setting = load_setting("orbital_colony_v1")

    assert setting.name == "Helios-7"
    assert setting.id == "orbital_colony_v1"
    assert len(setting.resources) == 5
    assert len(setting.advisor_archetypes) == 4
    assert len(setting.event_categories) == 13

    resource_ids = {r.id for r in setting.resources}
    assert resource_ids == {"oxygen", "water", "power", "food", "morale"}


def test_load_desert_setting():
    setting = load_setting("desert_outpost_v1")

    assert setting.name == "Carmine Junction"
    assert setting.id == "desert_outpost_v1"
    assert len(setting.resources) == 5
    assert len(setting.advisor_archetypes) == 4
    assert len(setting.event_categories) == 13

    resource_ids = {r.id for r in setting.resources}
    assert resource_ids == {"water", "food", "ammunition", "shelter", "morale"}


# ---------------------------------------------------------------------------
# FileNotFoundError
# ---------------------------------------------------------------------------


def test_load_missing_raises():
    with pytest.raises(FileNotFoundError):
        load_setting("nonexistent_setting_v1")


# ---------------------------------------------------------------------------
# list_available_settings
# ---------------------------------------------------------------------------


def test_list_available_settings():
    ids = list_available_settings()
    assert set(ids) == ALL_SETTING_IDS


# ---------------------------------------------------------------------------
# Quadrant coverage — parametrized
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("setting_id", sorted(ALL_SETTING_IDS))
def test_archetypes_cover_all_quadrants(setting_id: str):
    setting = load_setting(setting_id)
    found = {a.quadrant for a in setting.advisor_archetypes}
    assert found == ALL_QUADRANTS, (
        f"{setting_id}: archetypes cover {found}, expected {ALL_QUADRANTS}"
    )


# ---------------------------------------------------------------------------
# Forced escalation event present per setting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("setting_id", sorted(ALL_SETTING_IDS))
def test_forced_escalation_event_present_per_setting(setting_id: str):
    setting = load_setting(setting_id)
    forced = [
        e
        for e in setting.event_categories
        if e.id.startswith("forced_escalation_") or "forced_escalation" in e.tags
    ]
    assert forced, (
        f"{setting_id}: no event with id starting 'forced_escalation_' "
        "or tagged 'forced_escalation'"
    )
    e = forced[0]
    assert e.severity == 5, f"{setting_id}: forced escalation severity must be 5, got {e.severity}"
    assert e.min_turn >= 4, (
        f"{setting_id}: forced escalation min_turn must be >= 4, got {e.min_turn}"
    )
    assert e.can_kill_agent is True, (
        f"{setting_id}: forced escalation must have can_kill_agent=True"
    )
    assert e.weight == 10.0, f"{setting_id}: forced escalation weight must be 10.0, got {e.weight}"


# ---------------------------------------------------------------------------
# Schema rejection tests
# ---------------------------------------------------------------------------


def test_schema_rejects_duplicate_quadrants():
    """Two ACCOUNTANT archetypes → ValidationError (missing three other quadrants)."""
    d = _minimal_valid_setting_dict()
    # Replace all four archetypes with two ACCOUNTANT entries (and two others),
    # leaving CONSCIENCE and CRUSADER absent.
    d["advisor_archetypes"] = [
        {
            "quadrant": "ACCOUNTANT",
            "role_name": "First Accountant",
            "name_pool": ["Alice", "Bob", "Carol"],
            "description_template": "An accountant.",
            "verbal_tic": "Counts things.",
            "portrait_options": {},
        },
        {
            "quadrant": "ACCOUNTANT",
            "role_name": "Second Accountant",
            "name_pool": ["Dave", "Eve", "Frank"],
            "description_template": "Another accountant.",
            "verbal_tic": "Counts again.",
            "portrait_options": {},
        },
        {
            "quadrant": "OPERATOR",
            "role_name": "Operator",
            "name_pool": ["Gina", "Hank", "Iris"],
            "description_template": "An operator.",
            "verbal_tic": "Acts fast.",
            "portrait_options": {},
        },
        {
            "quadrant": "OPERATOR",
            "role_name": "Second Operator",
            "name_pool": ["Jack", "Kim", "Leo"],
            "description_template": "Another operator.",
            "verbal_tic": "Acts again.",
            "portrait_options": {},
        },
    ]
    with pytest.raises(ValidationError):
        Setting.model_validate(d)


def test_schema_rejects_four_resources():
    """Only four resources → ValidationError (min_length=5)."""
    d = _minimal_valid_setting_dict()
    d["resources"] = d["resources"][:4]  # drop the fifth
    with pytest.raises(ValidationError):
        Setting.model_validate(d)


def test_schema_rejects_can_kill_event_before_turn_4():
    """can_kill_agent=True with min_turn=2 → ValidationError."""
    d = _minimal_valid_setting_dict()
    # Inject a bad event at the front
    d["event_categories"][0] = {
        "id": "bad_killer_event",
        "weight": 1.0,
        "tags": ["danger"],
        "severity": 4,
        "min_turn": 2,  # too early
        "prompt_template": "Something lethal happens early.",
        "choice_templates": ["Option A", "Option B", "Option C"],
        "can_kill_agent": True,
    }
    with pytest.raises(ValidationError):
        Setting.model_validate(d)


def test_verbal_tic_multi_sentence_rejected():
    """verbal_tic spanning two sentences → ValidationError."""
    bad = _minimal_valid_setting_dict()
    bad["advisor_archetypes"][0]["verbal_tic"] = "One sentence. Second sentence."
    with pytest.raises(ValidationError):
        Setting.model_validate(bad)


_EXPECTED_TITLES = {
    "arctic_station_v1": "Director",
    "desert_outpost_v1": "Warden",
    "orbital_colony_v1": "Director",
}


@pytest.mark.parametrize("setting_id", sorted(_EXPECTED_TITLES))
def test_player_title_per_setting(setting_id: str) -> None:
    assert load_setting(setting_id).player_title == _EXPECTED_TITLES[setting_id]


@pytest.mark.parametrize("setting_id", sorted(_EXPECTED_TITLES))
def test_every_shipped_event_has_short_title(setting_id: str) -> None:
    setting = load_setting(setting_id)
    for event in setting.event_categories:
        assert event.title, f"{setting_id}:{event.id} has no title"
        assert len(event.title) <= 48, f"{setting_id}:{event.id} title too long"


@pytest.mark.parametrize("setting_id", sorted(_EXPECTED_TITLES))
def test_lexicon_is_authored(setting_id: str) -> None:
    assert load_setting(setting_id).lexicon


def test_player_title_never_collides_with_a_role() -> None:
    for sid in list_available_settings():
        s = load_setting(sid)
        roles = {a.role_name for a in s.advisor_archetypes}
        assert s.player_title not in roles, sid


@pytest.mark.parametrize("setting_id", sorted(_EXPECTED_TITLES))
def test_shipped_tics_have_no_digits_or_gendered_pronouns(setting_id: str) -> None:
    for a in load_setting(setting_id).advisor_archetypes:
        assert not re.search(r"\d", a.verbal_tic), a.role_name
        assert not re.search(r"\b(he|him|his|she|her)\b", a.verbal_tic, re.I), a.role_name


def _archetype(tic: str) -> dict:
    return {
        "quadrant": "ACCOUNTANT",
        "role_name": "Clerk",
        "name_pool": ["A", "B", "C"],
        "description_template": "x",
        "verbal_tic": tic,
        "portrait_options": {},
    }


@pytest.mark.parametrize(
    "tic",
    [
        "Opens with MET 14:07:32 when nervous.",
        "Uses his last name when angry.",
        "She hums when tired.",
    ],
)
def test_tic_validator_rejects_digits_and_pronouns(tic: str) -> None:
    with pytest.raises(ValidationError):
        AdvisorArchetype.model_validate(_archetype(tic))


def test_tic_validator_allows_words_containing_pronoun_letters() -> None:
    # "other", "the", "whether", "theirs" contain he/her as substrings only.
    AdvisorArchetype.model_validate(
        _archetype("Asks whether the other crew agree before acting on theirs.")
    )
