"""Auth-facing HTTP routes.

Right now just GET /me. The endpoint is deliberately thin — it pulls the
user identity from the JWT dependency, reads the profile, and asks the
gate whether a new run is allowed. Any business logic (creating the
session row, decrementing cost caps) belongs in the game router, not
here.
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.gate import can_start_run, next_run_at
from app.auth.schemas import UserInfo
from app.config import settings
from app.dependencies import get_current_user, get_db
from app.models import Profile

router = APIRouter(tags=["auth"])


@router.get("/me", response_model=UserInfo)
async def me(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserInfo:
    user_id = UUID(user["sub"])
    profile = await db.get(Profile, user_id)
    last = profile.last_run_started_at if profile else None
    eligible = can_start_run(last, bypass=settings.DEV_BYPASS_DAILY_GATE)
    return UserInfo(
        id=user["sub"],
        email=user.get("email"),
        display_name=profile.display_name if profile else None,
        next_run_at=None if eligible else next_run_at(last).isoformat(),
    )
