"""SQLAlchemy declarative base + ORM models.

This module holds the root `Base` so every ORM model in the app registers
against the same metadata. Alembic's env.py imports `Base` so autogenerate
can diff the in-Python schema against Postgres.

Keep this file focused on cross-cutting models (profiles, later game
sessions). Feature-scoped tables should import `Base` from here.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Profile(Base):
    """One row per authenticated user.

    `id` mirrors `auth.users.id` so the FK enforces that profiles can only
    exist for real users. The row is seeded by the
    `on_auth_user_created` trigger, not by application code. See
    `supabase/migrations/*_enable_rls_profiles_and_bootstrap_trigger.sql`.
    """

    __tablename__ = "profiles"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String)
    last_run_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_runs: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


# Import feature models so their classes register against Base.metadata.
# Alembic's env.py imports Base; these side-effect imports ensure all tables
# are visible to --autogenerate without changing env.py. Placed at the end
# to avoid a circular import (game.models imports Base from this module).
from app.game import models as _game_models  # noqa: E402, F401
