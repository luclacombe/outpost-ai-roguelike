"""Pydantic I/O schemas for the auth router."""

from pydantic import BaseModel


class UserInfo(BaseModel):
    """Response shape for GET /me."""

    id: str
    email: str | None = None
    display_name: str | None = None
    # ISO-8601 UTC string of the next 00:00 the user may start a new run,
    # or None when they are eligible right now.
    next_run_at: str | None = None
