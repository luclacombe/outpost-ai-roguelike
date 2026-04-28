"""Registry shape tests for the seven personas."""

from __future__ import annotations

from app.eval.playtest.personas import PERSONAS, get_persona
from app.eval.playtest.schemas import PersonaId


def test_seven_personas() -> None:
    assert len(PERSONAS) == 7
    expected: set[PersonaId] = {
        "optimizer",
        "pessimist",
        "narrative_driven",
        "shallow_reader",
        "brinksmanship",
        "veteran_roguelike",
        "drop_off_risk",
    }
    assert {p.id for p in PERSONAS} == expected


def test_get_persona_by_id() -> None:
    p = get_persona("brinksmanship")
    assert p.id == "brinksmanship"
    assert "stability" in p.picking_rule.lower() or "rails" in p.picking_rule.lower()


def test_get_persona_unknown_raises() -> None:
    import pytest

    with pytest.raises(KeyError):
        get_persona("not_real")  # type: ignore[arg-type]


def test_every_persona_has_substantive_fields() -> None:
    for p in PERSONAS:
        assert len(p.picking_rule.strip()) > 30, f"{p.id} picking_rule too short"
        assert len(p.evaluation_lens.strip()) > 20, f"{p.id} evaluation_lens too short"
        assert len(p.voice_traits.strip()) > 5, f"{p.id} voice_traits too short"
