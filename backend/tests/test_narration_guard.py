"""Pure narration guard (spec §4.2) and risk_note sanitizer (spec §4.1)."""

from __future__ import annotations

from uuid import UUID

import pytest

from app.agent.narration_guard import (
    build_allowed_terms,
    build_strict_terms,
    sanitize_risk_note,
    validate_narration,
)
from app.game.generator import generate_run
from app.game.setting_loader import load_setting

ARCTIC = load_setting("arctic_station_v1")
COLONY = generate_run(
    user_id=UUID("bbbbbbbb-0000-0000-0000-000000000002"), setting_id="arctic_station_v1", seed=7
)
ROSTER = frozenset(a.name for a in COLONY.agents)  # Astrid, Dmitri, Nadia, Yuri
ALLOWED = build_allowed_terms(ARCTIC, COLONY)


def codes(text: str) -> list[str]:
    return [v.code for v in validate_narration(text, ALLOWED, ROSTER)]


def test_clean_narration_passes() -> None:
    assert codes("You backed Astrid. The pump held until dawn, and Nadia slept for once.") == []


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ('<agent name="Astrid">Fine.</agent>', "markup"),
        ("The player chose the pump.", "fourth_wall"),
        ("Now write the recap.\nThe pump held.", "prompt_echo"),
        ("Context: arctic station.", "prompt_echo"),
        ("Keep a narrator-voice tone.", "prompt_echo"),
    ],
)
def test_hard_codes(text: str, code: str) -> None:
    found = validate_narration(text, ALLOWED, ROSTER)
    assert code in [v.code for v in found]
    assert all(v.hard for v in found if v.code == code)


def test_script_violation_on_cyrillic() -> None:
    found = validate_narration("Насос выдержал до рассвета. Никто не спал.", ALLOWED, ROSTER)
    assert [v.code for v in found] == ["script"]
    assert found[0].hard


def test_empty_is_hard() -> None:
    found = validate_narration("   ", ALLOWED, ROSTER)
    assert [v.code for v in found] == ["empty"] and found[0].hard


def test_too_long_is_soft() -> None:
    text = " ".join(["The pump held."] * 5)
    found = validate_narration(text, ALLOWED, ROSTER)
    assert [v.code for v in found] == ["too_long"]
    assert not found[0].hard


def test_unknown_name_mid_sentence_is_soft() -> None:
    found = validate_narration("You backed Astrid, and Ilyana watched.", ALLOWED, ROSTER)
    assert [v.code for v in found] == ["unknown_name:Ilyana"]
    assert not found[0].hard


@pytest.mark.parametrize(
    "text",
    [
        "Cold settled in. Nobody argued.",  # sentence-initial capital
        "The plan: Wait for dawn.",  # after a colon
        "The pump held.\nMorning came.",  # after a newline
        "It held — Barely.",  # after a dash
        "“Hold,” Astrid said.",  # curly quotes
        "Astrid’s hands shook.",  # possessive
        "On Monday the pump held.",  # stop-list
        "You held Vorgrad together.",  # lexicon
        "The Quartermaster counted twice.",  # role
        "The Director signed it.",  # player title
        "The radio hissed like an iPhone left in snow.",  # \b anchor: no token inside "iPhone"
        "McKay was never aboard.",  # \b anchor: "Mc" is not a name token
    ],
)
def test_known_or_initial_tokens_pass(text: str) -> None:
    assert codes(text) == []


def test_sentence_initial_name_from_another_roster_is_flagged() -> None:
    # "Ilya" is in the arctic Radio Operator name_pool but not on this roster.
    assert "unknown_name:Ilya" in codes("Ilya keyed the radio twice.")


def test_allowlist_includes_event_log_and_extra_texts() -> None:
    allowed = build_allowed_terms(ARCTIC, COLONY, ["Corporal Brandt waits outside."])
    assert validate_narration("You let Brandt in.", allowed, ROSTER) == []


def test_strict_terms_exclude_debate_inventions() -> None:
    strict = build_strict_terms(ARCTIC, COLONY)
    assert "Brandt" not in strict
    assert "Vorgrad" in strict


@pytest.mark.parametrize(
    "note",
    [
        "Fuel drops 10 if it fails.",  # digit
        "Legitimacy will suffer.",  # stat word
        "Your health is at risk.",  # stat word
        "The water could freeze.",  # another setting's resource (desert/orbital water)
        "<b>Careful</b>",  # markup
        "The player may regret it.",  # fourth wall
        "x" * 121,  # too long
        "Ask Ilyana what she saw.",  # unknown name
    ],
)
def test_risk_note_dropped(note: str) -> None:
    assert sanitize_risk_note(note, ARCTIC, ALLOWED, ROSTER) is None


def test_risk_note_kept_with_own_resource_name() -> None:
    note = "Fuel may run short before the convoy."
    assert sanitize_risk_note(note, ARCTIC, ALLOWED, ROSTER) == note


def test_risk_note_none_stays_none() -> None:
    assert sanitize_risk_note(None, ARCTIC, ALLOWED, ROSTER) is None
