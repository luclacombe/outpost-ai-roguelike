"""Pydantic request/response schemas for the game HTTP API.

Distinct from `app.game.schemas` (internal state types) because the wire
shape is a deliberately narrowed, player-facing projection:

  - Agent axes (ethics/temperament/loyalty), multi-turn memory,
    relationships, and transformation are hidden — they would leak the
    "how the sausage is made" layer that keeps advisor personalities
    feeling alive.
  - Proposal.tool_calls_on_pick is hidden — the player sees the
    effects_preview bullets only.
  - Consequence seeds are never exposed during a run.

Response shape is stable; adding fields is fine, renaming is a breaking
API change.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.game.schemas import Event, ProposalFlavor, Quadrant


class GameSessionCreateRequest(BaseModel):
    """POST /game/session body. Empty for MVP.

    `extra="forbid"` guards against clients sending e.g. a manually-picked
    setting_id — setting selection is server-side for MVP so the anti-
    abuse model stays simple.
    """

    model_config = ConfigDict(extra="forbid")


class AgentPublic(BaseModel):
    """Player-facing view of an advisor. Drops private personality axes,
    multi-turn memory, relationships, and transformation state."""

    id: UUID
    name: str
    role: str
    archetype_quadrant: Quadrant
    portrait_seed: str
    verbal_tic: str
    health: int
    morale: int
    grievance: int
    is_alive: bool


class ResourcePublic(BaseModel):
    """Mirrors the engine Resource 1:1 — nothing in Resource is private."""

    id: str
    name: str
    icon: str
    value: int
    max: int
    critical_threshold: int


class ProposalPublic(BaseModel):
    """Player-facing view of a Proposal. Drops `tool_calls_on_pick` — the
    player sees `effects_preview` bullets only."""

    id: str
    proposed_by_agent_id: UUID | None
    flavor: ProposalFlavor
    name: str
    description: str
    effects_preview: list[str]


class GameSessionResponse(BaseModel):
    """Shared response for POST /game/session and GET /game/{id}.

    `current_event` and `turn_proposals` are null/empty right after
    creation and only populated once the turn endpoint runs.
    """

    game_id: UUID
    setting_id: str
    setting_name: str
    max_turns: int
    current_turn: int
    legitimacy: int
    player_health: int
    agents: list[AgentPublic]
    resources: list[ResourcePublic]
    started_at: datetime
    current_event: Event | None = None
    turn_proposals: list[ProposalPublic] = []
