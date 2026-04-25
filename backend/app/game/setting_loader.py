"""
Setting YAML loader — loads and validates settings from YAML files.

Schemas live here (not in app/game/schemas.py) because they model static
content definitions, not runtime game state.

Usage:
    from app.game.setting_loader import load_setting, list_available_settings
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from app.game.schemas import Quadrant

SETTINGS_DIR = Path(__file__).parent / "settings"

# ---------------------------------------------------------------------------
# Content schema models (static YAML content, not runtime state)
# ---------------------------------------------------------------------------

_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]*$")
_HEX_COLOR = re.compile(r"^#[0-9a-f]{6}$")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


class ResourceDef(BaseModel):
    """A resource definition as it appears in a setting YAML."""

    id: str
    name: str
    icon: str
    starting: int
    max: int
    critical_threshold: int

    @field_validator("id")
    @classmethod
    def id_snake_case(cls, v: str) -> str:
        if not _SNAKE_CASE.match(v):
            raise ValueError(f"resource id {v!r} must be snake_case matching ^[a-z][a-z0-9_]*$")
        return v

    @model_validator(mode="after")
    def _value_constraints(self) -> ResourceDef:
        if self.starting > self.max:
            raise ValueError(f"starting ({self.starting}) must be <= max ({self.max})")
        if not (0 <= self.critical_threshold <= self.max):
            raise ValueError(
                f"critical_threshold ({self.critical_threshold}) must be in [0, {self.max}]"
            )
        return self


class AdvisorArchetype(BaseModel):
    """One of four archetype slots in a setting."""

    quadrant: Quadrant
    role_name: str
    name_pool: Annotated[list[str], Field(min_length=3)]
    description_template: str
    verbal_tic: Annotated[str, Field(max_length=200)]
    portrait_options: dict[str, list[str]]

    @field_validator("verbal_tic")
    @classmethod
    def _verbal_tic_is_single_sentence(cls, v: str) -> str:
        if len(_SENTENCE_SPLIT.split(v.strip())) > 1:
            raise ValueError("verbal_tic must be a single sentence")
        return v

    @field_validator("name_pool")
    @classmethod
    def name_pool_nonempty_strings(cls, v: list[str]) -> list[str]:
        for name in v:
            if not name.strip():
                raise ValueError("name_pool entries must be non-empty strings")
        return v


class EventCategory(BaseModel):
    """A template for a class of events the generator can instantiate."""

    id: str
    weight: Annotated[float, Field(ge=0.1, le=10.0)]
    tags: Annotated[list[str], Field(min_length=1, max_length=3)]
    severity: Annotated[int, Field(ge=1, le=5)]
    min_turn: Annotated[int, Field(ge=1)]
    prompt_template: str
    choice_templates: Annotated[list[str], Field(min_length=3)]
    can_kill_agent: bool = False

    @field_validator("id")
    @classmethod
    def id_snake_case(cls, v: str) -> str:
        if not _SNAKE_CASE.match(v):
            raise ValueError(f"event id {v!r} must be snake_case matching ^[a-z][a-z0-9_]*$")
        return v

    @model_validator(mode="after")
    def _kill_min_turn(self) -> EventCategory:
        if self.can_kill_agent and self.min_turn < 4:
            raise ValueError(
                f"event with can_kill_agent=True must have min_turn >= 4 "
                f"(got min_turn={self.min_turn})"
            )
        return self


class Setting(BaseModel):
    """Validated static definition of a game setting, loaded from YAML."""

    id: str
    name: Annotated[str, Field(min_length=1, max_length=60)]
    lore: Annotated[str, Field(min_length=100, max_length=1500)]
    palette_accent: str
    narrative_voice: str
    resources: Annotated[list[ResourceDef], Field(min_length=5, max_length=5)]
    advisor_archetypes: Annotated[list[AdvisorArchetype], Field(min_length=4, max_length=4)]
    event_categories: Annotated[list[EventCategory], Field(min_length=12, max_length=30)]

    @field_validator("id")
    @classmethod
    def id_snake_case(cls, v: str) -> str:
        if not _SNAKE_CASE.match(v):
            raise ValueError(f"id {v!r} must be snake_case matching ^[a-z][a-z0-9_]*$")
        return v

    @field_validator("palette_accent")
    @classmethod
    def palette_accent_hex(cls, v: str) -> str:
        if not _HEX_COLOR.match(v):
            raise ValueError(f"palette_accent {v!r} must be lowercase 6-digit hex like #4a6c8c")
        return v

    @model_validator(mode="after")
    def _archetypes_cover_all_quadrants(self) -> Setting:
        found = {a.quadrant for a in self.advisor_archetypes}
        required = set(Quadrant)
        if found != required:
            missing = required - found
            duplicated = found - required  # shouldn't happen with 4 items, but belt-and-suspenders
            parts = []
            if missing:
                parts.append(f"missing quadrants: {missing}")
            if duplicated:
                parts.append(f"unexpected quadrants: {duplicated}")
            raise ValueError(
                f"advisor_archetypes must cover all four quadrants. {'; '.join(parts)}"
            )
        return self


# ---------------------------------------------------------------------------
# Loader API
# ---------------------------------------------------------------------------


def load_setting(setting_id: str) -> Setting:
    """Load and validate the setting YAML for the given id.

    Raises:
        FileNotFoundError: no YAML file matches setting_id.
        pydantic.ValidationError: YAML parses but fails schema validation.
    """
    path = SETTINGS_DIR / f"{setting_id}.yml"
    if not path.exists():
        raise FileNotFoundError(f"No setting file found for id {setting_id!r}. Expected: {path}")
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return Setting.model_validate(raw)


def list_available_settings() -> list[str]:
    """Return the list of setting ids that have a YAML in the settings dir."""
    return sorted(p.stem for p in SETTINGS_DIR.glob("*.yml"))
