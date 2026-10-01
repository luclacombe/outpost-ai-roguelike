"""Pure-Pydantic record of a single simulated run, used by engine-only metrics.

This is intentionally decoupled from the SQLAlchemy ORM (`app.game.models`)
and the live `ColonyState`. The eval pipeline (Task 5.3a/c) constructs
`RunRecord` instances from finished simulated games and feeds them into the
metrics in `app.eval.balance` / `app.eval.end_state`. Keeping it minimal
means the metrics never have to touch the DB or YAML loader.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.game.schemas import Agent, EndState, ToolCall


class RunRecord(BaseModel):
    """Compact snapshot of one simulated run.

    Fields are exactly what the engine-only metrics need; do not add LLM
    judge fields here — those go on a separate scoring artifact in 5.3c.
    """

    setting_id: str
    policy: str  # e.g. "moral", "pragmatic", "random"
    seed: int

    end_state: EndState | None = None
    turns_played: int
    final_player_health: int

    agents_alive_at_end: int
    total_agents: int

    cost_cents: int

    tool_calls: list[ToolCall] = Field(default_factory=list)
    """Every tool call applied across the run, in chronological order."""

    agent_responses_per_agent: dict[UUID, list[str]] = Field(default_factory=dict)
    """Agent UUID -> the freeform reasoning strings it produced this run."""

    resource_history: dict[str, list[int]] = Field(default_factory=dict)
    """Resource id -> per-turn integer values."""

    forced_choice_turns: int = 0
    """Number of turns where only one viable proposal existed."""

    narration_per_turn: list[str] = Field(default_factory=list)
    """Narration string per turn — used by inter-run similarity in 5.3c."""

    game_id: UUID | None = None
    """game_sessions.id of the driven game (None for hand-built records)."""

    final_legitimacy: int | None = None
    """Legitimacy at the end of the run; paired comparisons diff it."""

    choice_spread: list[dict[str, Any]] = Field(default_factory=list)
    """Per paused turn: `app.eval.choice_spread.compute_choice_spread` output
    plus `turn` and `picked_proposal_id`. Empty for hand-built records."""


class TurnJudgeInput(BaseModel):
    """One (agent, turn, reasoning, action) tuple to feed the coherence judge."""

    agent_id: UUID
    turn_number: int
    reasoning: str
    action: str


class RunCoherenceInput(BaseModel):
    """Minimal inputs needed by the decision-coherence judge.

    Decoupled from `RunRecord` because the simulated runner emits per-turn
    `(agent, reasoning, action)` tuples that aren't on the standard record.
    Keeping this separate avoids bloating `RunRecord` with judge-only fields.
    """

    agents_by_id: dict[UUID, Agent]
    turns: list[TurnJudgeInput] = Field(default_factory=list)
