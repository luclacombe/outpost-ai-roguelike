"""turn_logs tool_calls and turn_meta

Revision ID: a7c3e9d41f20
Revises: 3ba6ba8568f0
Create Date: 2026-10-15

Additive and nullable. Must run on prod BEFORE the backend that maps these
columns deploys: every select(TurnLog) selects them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a7c3e9d41f20"
down_revision: str | Sequence[str] | None = "3ba6ba8568f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("turn_logs", sa.Column("tool_calls", postgresql.JSONB(), nullable=True))
    op.add_column("turn_logs", sa.Column("turn_meta", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("turn_logs", "turn_meta")
    op.drop_column("turn_logs", "tool_calls")
