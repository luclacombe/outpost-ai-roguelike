"""Agent HTTP routes.

Two routes, both streaming SSE:

  * `POST /game/{game_id}/turn`    — start / resume the pre-pause flow.
  * `POST /game/{game_id}/decide`  — apply a player choice, stream post-pause.

This layer is intentionally thin: parse inputs, run the pre-stream
validations (`prepare_turn` / `prepare_decide`), hand off to the streaming
generator. All validation that needs to surface as a plain JSON error
(404/409/400) happens BEFORE `StreamingResponse` is returned — once the
streaming body starts iterating, it's too late to write a status code.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.schemas_io import DecideRequest
from app.agent.service import prepare_decide, prepare_turn, stream_decide, stream_turn
from app.dependencies import get_current_user, get_db

router = APIRouter(prefix="/game", tags=["agent"])

# Proxy-safe SSE headers: Cache-Control=no-cache prevents stale caching,
# X-Accel-Buffering=no tells nginx/Railway's proxy not to buffer the stream.
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


@router.post("/{game_id}/turn")
async def post_turn(
    game_id: UUID,
    request: Request,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    user_id = UUID(user["sub"])
    prepared = await prepare_turn(game_id=game_id, user_id=user_id, db=db, request=request)
    return StreamingResponse(
        stream_turn(prepared=prepared),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post("/{game_id}/decide")
async def post_decide(
    game_id: UUID,
    request: Request,
    body: DecideRequest = Body(...),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    user_id = UUID(user["sub"])
    prepared = await prepare_decide(
        game_id=game_id,
        user_id=user_id,
        chosen_proposal_id=body.chosen_proposal_id,
        db=db,
        request=request,
    )
    return StreamingResponse(
        stream_decide(
            chosen_proposal_id=body.chosen_proposal_id,
            prepared=prepared,
        ),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
