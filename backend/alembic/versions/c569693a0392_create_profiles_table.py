"""create profiles table

Revision ID: c569693a0392
Revises:
Create Date: 2026-04-23 23:03:57.826150

Profiles mirror `auth.users` 1:1 via a CASCADE FK. The bootstrap trigger
that seeds rows on sign-up lives in a Supabase CLI migration under
`supabase/migrations/`, not here — that keeps RLS/auth wiring separate
from ORM schema (see backend/CLAUDE.md).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c569693a0392"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("last_run_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "total_runs",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["id"],
            ["auth.users.id"],
            ondelete="CASCADE",
            name="profiles_id_fkey",
        ),
    )


def downgrade() -> None:
    op.drop_table("profiles")
