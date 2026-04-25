"""
Game state schemas — Pydantic v2 models for every type the engine touches.

Order: enums → leaf types → composite types → top-level (Agent, ColonyState).
All models use frozen=False (default) so engine can use model_copy(update=...).
Roundtrip (JSON dump → reload) must be lossless.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Quadrant(StrEnum):
    ACCOUNTANT = "ACCOUNTANT"
    OPERATOR = "OPERATOR"
    CONSCIENCE = "CONSCIENCE"
    CRUSADER = "CRUSADER"


class ProposalFlavor(StrEnum):
    DOMINANT = "DOMINANT"
    MORAL = "MORAL"
    WILDCARD = "WILDCARD"


class EventOrigin(StrEnum):
    NATURAL = "NATURAL"
    SEED_FIRED = "SEED_FIRED"
    FORCED_ESCALATION = "FORCED_ESCALATION"


class EndState(StrEnum):
    ENDURED = "ENDURED"
    COLLAPSED = "COLLAPSED"
    OUSTED = "OUSTED"
    KILLED = "KILLED"


# ---------------------------------------------------------------------------
# Leaf / primitive models
# ---------------------------------------------------------------------------


class Resource(BaseModel):
    """A named colony resource with a bounded integer value."""

    id: str
    name: str
    icon: str
    value: int
    max: int
    critical_threshold: int

    @model_validator(mode="after")
    def _value_within_bounds(self) -> Resource:
        if not (0 <= self.value <= self.max):
            raise ValueError(f"value {self.value} must be within [0, {self.max}]")
        return self


class Transformation(BaseModel):
    """A one-time axis shift applied to an agent at a pivotal moment."""

    trigger_turn: int
    description: str
    axis_shift: dict[str, float]  # keys: "ethics" | "temperament" | "loyalty"


# ---------------------------------------------------------------------------
# Composite models
# ---------------------------------------------------------------------------


class ToolCall(BaseModel):
    """A single validated (or pending) engine tool invocation."""

    id: UUID = Field(default_factory=uuid4)
    name: str
    caller_id: UUID | None  # None = engine-emitted or player order
    args: dict[str, Any]
    rejection_reason: str | None = None  # set by engine when invalid


class SeedPayload(BaseModel):
    """Flat descriptor used to inject a consequence event into the pool."""

    event_id: str
    severity: int
    category: str
    description: str


class DecisionRef(BaseModel):
    """Compact back-link embedded in ConsequenceSeed."""

    turn_number: int
    event_id: str
    chosen_proposal_id: str


class Event(BaseModel):
    """A crisis / opportunity / dilemma presented to the player each turn."""

    id: str
    category: str
    title: str
    body: str
    severity: Annotated[int, Field(ge=1, le=5)]
    origin: EventOrigin
    origin_seed_id: UUID | None = None  # populated only when origin == SEED_FIRED


class ResolvedEvent(BaseModel):
    """Compact historical record stored on ColonyState.event_log."""

    turn_number: int
    event: Event
    chosen_proposal_id: str
    summary: str  # short human-readable blurb


class ConsequenceSeed(BaseModel):
    """A scheduled future event written at decision time; may be dormant or fired."""

    id: UUID = Field(default_factory=uuid4)
    game_id: UUID
    written_at_turn: int
    fires_at_turn: int
    foreshadowed: bool
    foreshadow_turn: int | None
    payload: SeedPayload
    origin_decision: DecisionRef
    fired: bool = False
    dormant: bool = False

    @field_validator("fires_at_turn")
    @classmethod
    def fires_after_written(cls, v: int, info: Any) -> int:
        written = info.data.get("written_at_turn")
        if written is not None and v <= written:
            raise ValueError(
                f"fires_at_turn ({v}) must be greater than written_at_turn ({written})"
            )
        return v

    @field_validator("foreshadow_turn")
    @classmethod
    def foreshadow_consistent(cls, v: int | None, info: Any) -> int | None:
        data = info.data
        foreshadowed = data.get("foreshadowed", False)
        if foreshadowed and v is None:
            raise ValueError("foreshadow_turn must be set when foreshadowed is True")
        if not foreshadowed:
            return v
        written = data.get("written_at_turn")
        fires = data.get("fires_at_turn")
        if (
            written is not None
            and fires is not None
            and v is not None
            and not (written <= v < fires)
        ):
            raise ValueError(
                f"foreshadow_turn ({v}) must satisfy written_at_turn <= foreshadow_turn"
                f" < fires_at_turn (range: [{written}, {fires}))"
            )
        return v


class Proposal(BaseModel):
    """One of three structured choices offered to the player each turn."""

    id: str
    proposed_by_agent_id: UUID | None  # None for engine-generated wildcards
    flavor: ProposalFlavor
    name: str
    description: str
    effects_preview: list[str]  # human-readable bullets shown before picking
    tool_calls_on_pick: list[ToolCall]  # applied by engine when this is chosen


# ---------------------------------------------------------------------------
# Top-level models
# ---------------------------------------------------------------------------


class Agent(BaseModel):
    """An AI advisor with stable identity, live state, and multi-turn memory."""

    id: UUID = Field(default_factory=uuid4)
    game_id: UUID

    name: str
    role: str  # setting-specific (e.g. "Quartermaster", "Flight Engineer")
    archetype_quadrant: Quadrant

    # Personality axes (-1.0 = pole A, +1.0 = pole B)
    ethics: Annotated[float, Field(ge=-1.0, le=1.0)]  # - pragmatist, + idealist
    temperament: Annotated[float, Field(ge=-1.0, le=1.0)]  # - cautious, + bold
    loyalty: Annotated[float, Field(ge=-1.0, le=1.0)]  # - individual, + collective

    portrait_seed: str  # stable, drives DiceBear output

    # Live state
    health: Annotated[int, Field(ge=0, le=100)]
    morale: Annotated[int, Field(ge=0, le=100)]
    grievance: Annotated[int, Field(ge=0, le=10)]
    is_alive: bool

    # Multi-turn state
    memory: list[str] = Field(default_factory=list)  # FIFO cap-10 enforced in engine
    relationships: dict[uuid.UUID, int] = Field(default_factory=dict)  # -10..+10
    transformation: Transformation | None = None

    @field_validator("relationships")
    @classmethod
    def relationships_values_in_range(cls, v: dict[uuid.UUID, int]) -> dict[uuid.UUID, int]:
        for agent_id, score in v.items():
            if not (-10 <= score <= 10):
                raise ValueError(
                    f"relationship score for {agent_id} is {score!r}; must be between -10 and 10"
                )
        return v

    # Prompt engineering
    verbal_tic: str = ""  # populated by the generator (Task 3.9); empty until then


class Decision(BaseModel):
    """Full post-mortem record of a single turn's choice and consequences."""

    turn_number: int
    event_id: str
    choices_offered: Annotated[list[Proposal], Field(min_length=3, max_length=3)]
    chosen: DecisionRef
    effects_applied: list[ToolCall]
    seeds_written: list[UUID]
    grievance_shifts: dict[UUID, int]
    legitimacy_cost: int


class ColonyState(BaseModel):
    """Top-level run state. Serialised to JSONB in game_sessions.state."""

    game_id: UUID
    user_id: UUID
    setting_id: str
    seed: int
    turn_number: int
    max_turns: int  # 8-12, chosen at run creation

    resources: dict[str, Resource]  # {resource_id: Resource}
    agents: list[Agent]  # includes dead (is_alive=False)
    legitimacy: Annotated[int, Field(ge=0, le=100)]
    player_health: Annotated[int, Field(ge=0, le=100)]

    consequence_seeds: list[ConsequenceSeed]  # includes fired (.fired=True) and dormant
    event_log: list[ResolvedEvent]  # one entry per completed turn

    current_event: Event | None
    turn_proposals: list[Proposal]
    turn_tool_calls: list[ToolCall]
    turn_cost_cents: int = 0  # cumulative LLM cost for this run

    end_state: EndState | None = None  # None while run is active
