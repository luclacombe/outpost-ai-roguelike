"""SQLAlchemy ORM models for game tables.

Use plain class names here (GameSession, Agent, TurnLog, ConsequenceSeed).
Call sites that also import Pydantic schemas from app.game.schemas should
use qualified imports to avoid collisions, e.g.::

    from app.game import models as game_models
    from app.game.schemas import Agent as AgentSchema

All four classes register against the shared Base from app.models so
Alembic's target_metadata picks them up automatically.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import (
    JSONB,
    REAL,  # noqa: F401 — explicit Postgres REAL
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class GameSession(Base):
    """One run per user per day.

    `state` holds the full ColonyState snapshot as JSONB; callers parse it
    back to `app.game.schemas.ColonyState` on read. Scalar columns mirror
    frequently-queried fields in ColonyState so queries don't need JSONB ops.
    """

    __tablename__ = "game_sessions"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    setting_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    seed: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    max_turns: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    current_turn: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    state: Mapped[dict] = mapped_column(JSONB, nullable=False)
    legitimacy: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("100")
    )
    player_health: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("100")
    )
    # NULL while running; set to ENDURED/COLLAPSED/OUSTED/KILLED on completion.
    end_state: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    cost_cents: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    # Populated on completion; used by public_read_shared_runs RLS policy.
    share_token: Mapped[str | None] = mapped_column(sa.Text, nullable=True, unique=True)
    started_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        sa.Index("idx_game_sessions_user", "user_id"),
        sa.Index(
            "idx_game_sessions_end_state",
            "end_state",
            postgresql_where=sa.text("end_state IS NOT NULL"),
        ),
    )


class Agent(Base):
    """An AI advisor row. One row per advisor per run.

    Identity/personality axes are stored as scalar columns for cheap diff
    queries; live state (health, morale, grievance, memory, relationships,
    transformation) is packed into `state` JSONB.
    """

    __tablename__ = "agents"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )
    game_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("game_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    role: Mapped[str] = mapped_column(sa.Text, nullable=False)
    archetype_quadrant: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # Personality axes stored as single-precision floats (REAL).
    ethics: Mapped[float] = mapped_column(sa.Float(precision=24), nullable=False)
    temperament: Mapped[float] = mapped_column(sa.Float(precision=24), nullable=False)
    loyalty: Mapped[float] = mapped_column(sa.Float(precision=24), nullable=False)
    portrait_seed: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # Live state: {health, morale, grievance, is_alive, memory[], relationships{},
    #               transformation?}
    state: Mapped[dict] = mapped_column(JSONB, nullable=False)

    __table_args__ = (sa.Index("idx_agents_game", "game_id"),)


class TurnLog(Base):
    """Immutable record of one completed turn.

    Written once at turn resolution; never updated. JSONB columns hold the
    exact Pydantic model output so callers can reconstruct full history.
    """

    __tablename__ = "turn_logs"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )
    game_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("game_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    turn_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    event: Mapped[dict] = mapped_column(JSONB, nullable=False)
    proposals: Mapped[list] = mapped_column(JSONB, nullable=False)
    chosen: Mapped[dict] = mapped_column(JSONB, nullable=False)
    agent_responses: Mapped[list] = mapped_column(JSONB, nullable=False)
    autonomous_actions: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    narration: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    state_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    turn_cost_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )

    __table_args__ = (
        sa.UniqueConstraint("game_id", "turn_number", name="uq_turn_logs_game_turn"),
    )


class ConsequenceSeed(Base):
    """A scheduled future event written at decision time.

    `fired` and `dormant` are scalar columns for efficient filtering;
    `payload` and `origin_decision` hold the full Pydantic model output.
    """

    __tablename__ = "consequence_seeds"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )
    game_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("game_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    written_at_turn: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    fires_at_turn: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    foreshadowed: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
    foreshadow_turn: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    origin_decision: Mapped[dict] = mapped_column(JSONB, nullable=False)
    fired: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("FALSE")
    )
    dormant: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("FALSE")
    )

    __table_args__ = (
        sa.Index("idx_seeds_game_fires", "game_id", "fires_at_turn"),
    )
