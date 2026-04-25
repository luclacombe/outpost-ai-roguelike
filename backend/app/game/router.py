"""Game session HTTP routes.

Thin translation layer: parse inputs, call service, marshal response.
Business logic (daily-gate enforcement, persistence, state generation)
lives in `app.game.service`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db
from app.game.exceptions import (
    DailyGateExceeded,
    GameError,
    GameNotEnded,
    GameNotFound,
    NoSettingsAvailable,
)
from app.game.post_mortem import (
    build_post_mortem,
    build_post_mortem_by_token,
    ensure_share_token,
)
from app.game.schemas import ColonyState
from app.game.schemas_io import (
    AgentPublic,
    GameSessionCreateRequest,
    GameSessionResponse,
    PostMortem,
    ProposalPublic,
    ResourcePublic,
)
from app.game.service import create_game_session, get_game_session
from app.game.setting_loader import load_setting

router = APIRouter(prefix="/game", tags=["game"])


def _to_http_exception(exc: GameError) -> HTTPException:
    """Map domain errors to FastAPI HTTPException with the error envelope
    shape from root CLAUDE.md."""
    if isinstance(exc, DailyGateExceeded):
        http_status = status.HTTP_429_TOO_MANY_REQUESTS
    elif isinstance(exc, GameNotFound):
        http_status = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, GameNotEnded):
        http_status = status.HTTP_409_CONFLICT
    elif isinstance(exc, NoSettingsAvailable):
        http_status = status.HTTP_503_SERVICE_UNAVAILABLE
    else:  # pragma: no cover — exhaustive over known subclasses
        http_status = status.HTTP_500_INTERNAL_SERVER_ERROR
    return HTTPException(status_code=http_status, detail={"error": exc.to_dict()})


def _shape_response(state: ColonyState, *, setting_name: str) -> GameSessionResponse:
    """Project ColonyState into the player-facing response shape.

    Hides agent axes (ethics/temperament/loyalty), memory, relationships,
    transformation, and proposal `tool_calls_on_pick` — those stay
    engine-side.

    `started_at` is synthesized from the current UTC clock because
    ColonyState itself doesn't carry it. The scalar column on
    game_sessions is the canonical source, but for MVP the sub-second
    difference between "row created" and "response rendered" is
    irrelevant to the player-facing view.
    """
    resources = [
        ResourcePublic(
            id=r.id,
            name=r.name,
            icon=r.icon,
            value=r.value,
            max=r.max,
            critical_threshold=r.critical_threshold,
        )
        for r in state.resources.values()
    ]
    agents = [
        AgentPublic(
            id=a.id,
            name=a.name,
            role=a.role,
            archetype_quadrant=a.archetype_quadrant,
            portrait_seed=a.portrait_seed,
            verbal_tic=a.verbal_tic,
            health=a.health,
            morale=a.morale,
            grievance=a.grievance,
            is_alive=a.is_alive,
        )
        for a in state.agents
    ]
    proposals = [
        ProposalPublic(
            id=p.id,
            proposed_by_agent_id=p.proposed_by_agent_id,
            flavor=p.flavor,
            name=p.name,
            description=p.description,
            effects_preview=p.effects_preview,
        )
        for p in state.turn_proposals
    ]
    return GameSessionResponse(
        game_id=state.game_id,
        setting_id=state.setting_id,
        setting_name=setting_name,
        max_turns=state.max_turns,
        current_turn=state.turn_number,
        legitimacy=state.legitimacy,
        player_health=state.player_health,
        agents=agents,
        resources=resources,
        started_at=datetime.now(UTC),
        current_event=state.current_event,
        turn_proposals=proposals,
        end_state=state.end_state,
    )


@router.post(
    "/session",
    response_model=GameSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    _body: GameSessionCreateRequest = Body(default_factory=GameSessionCreateRequest),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GameSessionResponse:
    user_id = UUID(user["sub"])
    try:
        state = await create_game_session(user_id=user_id, db=db)
    except GameError as exc:
        raise _to_http_exception(exc) from exc
    setting = load_setting(state.setting_id)
    return _shape_response(state, setting_name=setting.name)


@router.get("/{game_id}", response_model=GameSessionResponse)
async def get_session(
    game_id: UUID,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GameSessionResponse:
    user_id = UUID(user["sub"])
    try:
        state = await get_game_session(user_id=user_id, game_id=game_id, db=db)
    except GameError as exc:
        raise _to_http_exception(exc) from exc
    setting = load_setting(state.setting_id)
    return _shape_response(state, setting_name=setting.name)


@router.get("/{game_id}/post-mortem", response_model=PostMortem)
async def get_post_mortem(
    game_id: UUID,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PostMortem:
    """Aggregated post-mortem for a completed run.

    Authorization: scoped to `user_id` like the rest of /game. Calling for
    a still-running game returns 409 (`game_not_ended`); calling for a run
    that doesn't belong to this user returns 404 (`game_not_found`) — same
    response as a missing run, to avoid leaking existence.
    """
    user_id = UUID(user["sub"])
    # Ownership check uses the same query path as get_session — raises
    # GameNotFound if the row is missing or belongs to another user.
    try:
        await get_game_session(user_id=user_id, game_id=game_id, db=db)
        # Lazily mint share_token on first post-mortem view so the share
        # button always has a non-null token to render. Idempotent.
        await ensure_share_token(game_id=game_id, db=db)
        post_mortem = await build_post_mortem(game_id=game_id, db=db)
    except GameError as exc:
        raise _to_http_exception(exc) from exc
    await db.commit()
    return post_mortem


@router.get("/share/{token}", response_model=PostMortem)
async def get_shared_post_mortem(
    token: str,
    db: AsyncSession = Depends(get_db),
) -> PostMortem:
    """Public, unauthenticated view of a shared post-mortem.

    Auth is the share token — anyone holding the URL gets the run. Token
    minting is one-way (`ensure_share_token` is called the first time the
    owner views their post-mortem); no rotation, no revocation in MVP.
    Returns 404 for unknown tokens — same shape as a wrong-owner request,
    avoiding existence leaks.
    """
    try:
        return await build_post_mortem_by_token(token=token, db=db)
    except GameError as exc:
        raise _to_http_exception(exc) from exc
