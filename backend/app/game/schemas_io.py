"""Pydantic request/response schemas for the game HTTP API.

Distinct from `app.game.schemas` (internal state types) because the wire
shape is a deliberately narrowed, player-facing projection:

  - Agent axes (ethics/temperament/loyalty), multi-turn memory,
    relationships, and transformation are hidden — they would leak the
    "how the sausage is made" layer that keeps advisor personalities
    feeling alive.
  - Proposal.tool_calls_on_pick is hidden — the player sees the
    effects_preview bullets and an optional risk_note only.
  - Consequence seeds are never exposed during a run.

Response shape is stable; adding fields is fine, renaming is a breaking
API change.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.game.schemas import AdvisorStatus, EndState, Event, ProposalFlavor, Quadrant


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
    status: AdvisorStatus


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
    player sees the engine-rendered `effects_preview` and an optional
    sanitized `risk_note`."""

    id: str
    proposed_by_agent_id: UUID | None
    flavor: ProposalFlavor
    name: str
    description: str
    effects_preview: list[str]
    risk_note: str | None = None


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
    # `end_state` is None while the run is live; one of ENDURED / COLLAPSED /
    # OUSTED / KILLED once the run terminates. The frontend uses this to drive
    # the dashboard's "ended" footer; navigating back to /run/<id> after a run
    # closes should NOT offer a "Begin turn" affordance.
    end_state: EndState | None = None
    # Engine rule lines shown once in the ChoosePanel header (spec §4.1).
    close_preview: list[str] = []


# ---------------------------------------------------------------------------
# Post-mortem
# ---------------------------------------------------------------------------


class PostMortemAgent(BaseModel):
    """Per-advisor end-of-run summary for the post-mortem header."""

    id: UUID
    name: str
    role: str
    archetype_quadrant: Quadrant
    portrait_seed: str
    is_alive: bool
    final_grievance: int
    final_health: int
    final_status: AdvisorStatus
    transformed: bool


class PostMortemEvent(BaseModel):
    id: str
    title: str
    body: str
    category: str
    severity: int
    origin: str  # NATURAL | SEED_FIRED | FORCED_ESCALATION
    origin_seed_id: UUID | None = None


class PostMortemProposal(BaseModel):
    id: str
    name: str
    description: str
    flavor: ProposalFlavor
    proposed_by_agent_id: UUID | None = None
    effects_preview: list[str] = []
    risk_note: str | None = None


class PostMortemAdvisorLine(BaseModel):
    agent_id: UUID
    text: str


class PostMortemSeedRef(BaseModel):
    """Reference to a consequence seed in the post-mortem.

    `payload_summary` is a single-line summary of the seed payload — not the
    full payload, which can be verbose. The frontend uses it for one-line
    "if you'd done X" sentences in the counterfactuals panel.
    """

    id: UUID
    written_at_turn: int
    fires_at_turn: int
    foreshadowed: bool
    fired: bool
    dormant: bool
    payload_summary: str
    origin_event_title: str | None = None


class PostMortemTurn(BaseModel):
    turn_number: int
    event: PostMortemEvent
    proposals: list[PostMortemProposal]
    chosen_proposal_id: str
    advisor_lines: list[PostMortemAdvisorLine]
    narration: str | None
    cost_cents: int
    # Per-turn state-after deltas: integer values keyed by id.
    legitimacy: int
    player_health: int
    resources: dict[str, int]  # resource id → value at end of turn
    agent_grievance: dict[UUID, int]  # agent id → grievance at end of turn
    seeds_fired: list[PostMortemSeedRef] = []  # seeds firing on this turn
    seeds_written: list[PostMortemSeedRef] = []  # seeds written this turn


class PostMortemNearMissCoup(BaseModel):
    """A pair of advisors whose combined grievance approached but did not
    cross the coup threshold. Faction formation triggers at grievance ≥ 7
    for two or more advisors; coup eligibility at ≥ 9. A near-miss is any
    turn where two advisors simultaneously held ≥ 7 without ever both
    reaching ≥ 9."""

    turn_number: int  # turn where the simultaneous peak was hit
    agent_a_id: UUID
    agent_b_id: UUID
    grievance_a: int
    grievance_b: int


class PostMortemCounterfactuals(BaseModel):
    """The "what would have happened" panel.

    Three signals SPEC §11.2 calls for. `didnt_happen_transformations` is
    deliberately empty in MVP — the engine doesn't track "near-transform"
    state, so we'd be guessing. Reserved as a structural slot.
    """

    dormant_seeds: list[PostMortemSeedRef] = []
    near_miss_coups: list[PostMortemNearMissCoup] = []
    didnt_happen_transformations: list[dict] = []


class PostMortem(BaseModel):
    """Full post-mortem payload for GET /game/{id}/post-mortem.

    The frontend renders this directly into the timeline, header, footer,
    and counterfactuals panel. No additional aggregation client-side."""

    game_id: UUID
    setting_id: str
    setting_name: str
    end_state: EndState
    total_turns: int  # number of completed turns
    max_turns: int
    cost_cents: int
    started_at: datetime
    ended_at: datetime | None
    share_token: str | None
    final_legitimacy: int
    final_player_health: int
    # Opening line of turn 0's narration (or null if no narration). The share
    # card uses these two lines to anchor the narrative shape of the run.
    opening_line: str | None
    closing_line: str | None
    agents: list[PostMortemAgent]
    turns: list[PostMortemTurn]
    counterfactuals: PostMortemCounterfactuals
