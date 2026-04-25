"""Post-mortem aggregation.

Reads completed run rows (game_sessions, agents, turn_logs, consequence_seeds)
and projects them into a `PostMortem` payload.

Aggregation lives here, not in the router, so the same function can drive
both the player-private endpoint (GET /game/{id}/post-mortem) and the
public `/share/{token}` endpoint that lands later in Phase 8.

No mutation, no LLM calls. Pure read + project. The router enforces
authorization; this module assumes the caller has already gated access.
"""

from __future__ import annotations

import re
import secrets
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.game import models as game_models
from app.game.exceptions import GameNotEnded, GameNotFound
from app.game.schemas import EndState, Quadrant
from app.game.schemas_io import (
    PostMortem,
    PostMortemAdvisorLine,
    PostMortemAgent,
    PostMortemCounterfactuals,
    PostMortemEvent,
    PostMortemNearMissCoup,
    PostMortemProposal,
    PostMortemSeedRef,
    PostMortemTurn,
)
from app.game.setting_loader import load_setting

# Faction formation kicks in at ≥7 (SPEC §5.1); coup eligibility at ≥9.
NEAR_MISS_COUP_THRESHOLD = 7


async def ensure_share_token(*, game_id: UUID, db: AsyncSession) -> str:
    """Lazily mint a share token for a completed run.

    Idempotent: returns the existing token if one is already set. Caller is
    responsible for committing the surrounding transaction.

    Raises `GameNotFound` if the row is missing, `GameNotEnded` if the run
    is still active — public sharing of a live run is not supported.
    """
    session = (
        await db.execute(
            select(game_models.GameSession).where(game_models.GameSession.id == game_id)
        )
    ).scalar_one_or_none()
    if session is None:
        raise GameNotFound()
    if session.end_state is None:
        raise GameNotEnded()
    if session.share_token:
        return session.share_token
    token = secrets.token_urlsafe(8)
    await db.execute(
        update(game_models.GameSession)
        .where(game_models.GameSession.id == game_id)
        .values(share_token=token)
    )
    return token


async def build_post_mortem_by_token(*, token: str, db: AsyncSession) -> PostMortem:
    """Resolve a share token to the underlying game and build its post-mortem.

    Raises `GameNotFound` for an unknown token. The endpoint that calls
    this is unauthenticated, so we deliberately surface the same error as
    "wrong owner" — no existence-leak.
    """
    session = (
        await db.execute(
            select(game_models.GameSession).where(game_models.GameSession.share_token == token)
        )
    ).scalar_one_or_none()
    if session is None:
        raise GameNotFound()
    return await build_post_mortem(game_id=session.id, db=db)


async def build_post_mortem(*, game_id: UUID, db: AsyncSession) -> PostMortem:
    """Aggregate post-mortem data for a completed run.

    Caller must already have authorized access (user_id check OR share_token
    lookup). Raises:
      * `GameNotFound` if no game_sessions row exists
      * `GameNotEnded` if `end_state` is null
    """
    session = (
        await db.execute(
            select(game_models.GameSession).where(game_models.GameSession.id == game_id)
        )
    ).scalar_one_or_none()
    if session is None:
        raise GameNotFound()
    if session.end_state is None:
        raise GameNotEnded()

    agents = (
        (await db.execute(select(game_models.Agent).where(game_models.Agent.game_id == game_id)))
        .scalars()
        .all()
    )
    turn_logs = (
        (
            await db.execute(
                select(game_models.TurnLog)
                .where(game_models.TurnLog.game_id == game_id)
                .order_by(game_models.TurnLog.turn_number)
            )
        )
        .scalars()
        .all()
    )
    seeds = (
        (
            await db.execute(
                select(game_models.ConsequenceSeed).where(
                    game_models.ConsequenceSeed.game_id == game_id
                )
            )
        )
        .scalars()
        .all()
    )

    setting = load_setting(session.setting_id)

    # Build seed lookup by id and by fires_at_turn for fast joins.
    seeds_by_id: dict[UUID, game_models.ConsequenceSeed] = {s.id: s for s in seeds}
    seeds_fired_by_turn: dict[int, list[game_models.ConsequenceSeed]] = {}
    seeds_written_by_turn: dict[int, list[game_models.ConsequenceSeed]] = {}
    for s in seeds:
        if s.fired:
            seeds_fired_by_turn.setdefault(s.fires_at_turn, []).append(s)
        seeds_written_by_turn.setdefault(s.written_at_turn, []).append(s)

    # Map turn_logs into PostMortemTurn objects.
    turn_models: list[PostMortemTurn] = []
    final_grievance_by_agent: dict[UUID, int] = {a.id: 0 for a in agents}
    final_alive_by_agent: dict[UUID, bool] = {a.id: True for a in agents}
    grievance_history: list[dict[UUID, int]] = []  # per turn

    for tl in turn_logs:
        evt_raw = tl.event or {}
        # `origin_seed_id` may be present on SEED_FIRED events, pointing at
        # the consequence_seed row that scheduled this event.
        origin_seed_id = _coerce_uuid(evt_raw.get("origin_seed_id"))
        event_model = PostMortemEvent(
            id=str(evt_raw.get("id", "")),
            title=str(evt_raw.get("title", "")),
            body=str(evt_raw.get("body", "")),
            category=str(evt_raw.get("category", "")),
            severity=int(evt_raw.get("severity", 1)),
            origin=str(evt_raw.get("origin", "NATURAL")),
            origin_seed_id=origin_seed_id,
        )

        proposals = [
            PostMortemProposal(
                id=str(p.get("id", "")),
                name=str(p.get("name", "")),
                description=str(p.get("description", "")),
                flavor=p.get("flavor", "DOMINANT"),
                proposed_by_agent_id=_coerce_uuid(p.get("proposed_by_agent_id")),
                effects_preview=list(p.get("effects_preview", []) or []),
            )
            for p in (tl.proposals or [])
        ]

        chosen_id = str((tl.chosen or {}).get("chosen_proposal_id", ""))

        advisor_lines = [
            PostMortemAdvisorLine(
                agent_id=_coerce_uuid(r.get("agent_id")) or _ZERO_UUID,
                text=_to_one_line(str(r.get("text", ""))),
            )
            for r in (tl.agent_responses or [])
        ]

        snapshot = tl.state_snapshot or {}
        legitimacy_after = int(snapshot.get("legitimacy", session.legitimacy))
        player_health_after = int(snapshot.get("player_health", session.player_health))
        resources_after: dict[str, int] = {}
        for rid, rdata in (snapshot.get("resources") or {}).items():
            if isinstance(rdata, dict) and "value" in rdata:
                resources_after[rid] = int(rdata["value"])
            else:
                resources_after[rid] = int(rdata or 0)

        agent_grievance: dict[UUID, int] = {}
        for a in snapshot.get("agents") or []:
            aid = _coerce_uuid(a.get("id"))
            if aid is None:
                continue
            agent_grievance[aid] = int(a.get("grievance", 0))
            final_grievance_by_agent[aid] = agent_grievance[aid]
            final_alive_by_agent[aid] = bool(a.get("is_alive", True))
        grievance_history.append(agent_grievance)

        # Seeds firing this turn: prefer seed-table membership where the
        # event references an originating seed; fall back to firing-turn join.
        fired_refs: list[PostMortemSeedRef] = []
        for s in seeds_fired_by_turn.get(tl.turn_number, []):
            fired_refs.append(_seed_to_ref(s, agents))
        # Some runs lose their seed rows after firing (Phase 4 quirk: the
        # consequence_seeds row gets removed when the seed event fires). Fall
        # back to the embedded `origin_seed_id` so the post-mortem still
        # surfaces "this turn's event came from a prior decision."
        if not fired_refs and origin_seed_id is not None:
            seed = seeds_by_id.get(origin_seed_id)
            if seed is not None:
                fired_refs.append(_seed_to_ref(seed, agents))

        written_refs = [
            _seed_to_ref(s, agents) for s in seeds_written_by_turn.get(tl.turn_number, [])
        ]

        turn_models.append(
            PostMortemTurn(
                turn_number=tl.turn_number,
                event=event_model,
                proposals=proposals,
                chosen_proposal_id=chosen_id,
                advisor_lines=advisor_lines,
                narration=tl.narration,
                cost_cents=int(tl.turn_cost_cents),
                legitimacy=legitimacy_after,
                player_health=player_health_after,
                resources=resources_after,
                agent_grievance=agent_grievance,
                seeds_fired=fired_refs,
                seeds_written=written_refs,
            )
        )

    agent_models = [
        PostMortemAgent(
            id=a.id,
            name=a.name,
            role=a.role,
            archetype_quadrant=Quadrant(a.archetype_quadrant),
            portrait_seed=a.portrait_seed,
            is_alive=final_alive_by_agent.get(a.id, True),
            final_grievance=final_grievance_by_agent.get(a.id, 0),
            final_health=int((a.state or {}).get("health", 100)),
            transformed=(a.state or {}).get("transformation") is not None,
        )
        for a in agents
    ]

    counterfactuals = _build_counterfactuals(
        seeds=seeds,
        agents=agents,
        grievance_history=grievance_history,
    )

    opening_line = _first_sentence(turn_models[0].narration) if turn_models else None
    closing_line = _last_sentence(turn_models[-1].narration) if turn_models else None

    return PostMortem(
        game_id=session.id,
        setting_id=session.setting_id,
        setting_name=setting.name,
        end_state=EndState(session.end_state),
        total_turns=len(turn_models),
        max_turns=session.max_turns,
        cost_cents=int(session.cost_cents),
        started_at=session.started_at,
        ended_at=session.ended_at,
        share_token=session.share_token,
        final_legitimacy=int(session.legitimacy),
        final_player_health=int(session.player_health),
        opening_line=opening_line,
        closing_line=closing_line,
        agents=agent_models,
        turns=turn_models,
        counterfactuals=counterfactuals,
    )


# ---------------------------------------------------------------------------
# Counterfactuals
# ---------------------------------------------------------------------------


def _build_counterfactuals(
    *,
    seeds: list[game_models.ConsequenceSeed],
    agents: list[game_models.Agent],
    grievance_history: list[dict[UUID, int]],
) -> PostMortemCounterfactuals:
    """Compute the three counterfactuals signals.

    * Dormant seeds: any seed marked `dormant=true` OR `fired=false`. These
      are the "if you'd done X, Y would have happened" hooks.
    * Near-miss coups: any turn where two distinct advisors simultaneously
      held grievance ≥ 7 without ever both crossing 9. We surface the peak
      pair (highest combined grievance across the run).
    * `didnt_happen_transformations` is left empty — the engine doesn't
      track per-axis "near-transform" state, so we'd be inventing data.
    """
    dormant_refs: list[PostMortemSeedRef] = []
    for s in seeds:
        if s.dormant or not s.fired:
            dormant_refs.append(_seed_to_ref(s, agents))

    near_miss = _peak_near_miss(grievance_history)

    return PostMortemCounterfactuals(
        dormant_seeds=dormant_refs,
        near_miss_coups=near_miss,
        didnt_happen_transformations=[],
    )


def _peak_near_miss(
    grievance_history: list[dict[UUID, int]],
) -> list[PostMortemNearMissCoup]:
    """Find the single highest-tension pair across the run.

    A pair qualifies as a near-miss if both advisors simultaneously held
    grievance ≥ NEAR_MISS_COUP_THRESHOLD. We rank by combined grievance and
    surface only the peak — emitting every pair from every turn would drown
    the panel.

    Returns an empty list when no qualifying pair existed.
    """
    best: PostMortemNearMissCoup | None = None
    best_total = -1
    for turn_idx, snapshot in enumerate(grievance_history):
        ids = sorted(snapshot.keys())
        for i, a_id in enumerate(ids):
            ga = snapshot[a_id]
            if ga < NEAR_MISS_COUP_THRESHOLD:
                continue
            for b_id in ids[i + 1 :]:
                gb = snapshot[b_id]
                if gb < NEAR_MISS_COUP_THRESHOLD:
                    continue
                total = ga + gb
                if total > best_total:
                    best_total = total
                    best = PostMortemNearMissCoup(
                        turn_number=turn_idx,
                        agent_a_id=a_id,
                        agent_b_id=b_id,
                        grievance_a=ga,
                        grievance_b=gb,
                    )
    return [best] if best is not None else []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_ZERO_UUID = UUID(int=0)


def _coerce_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _seed_to_ref(
    seed: game_models.ConsequenceSeed,
    agents: list[game_models.Agent],
) -> PostMortemSeedRef:
    """Project a ConsequenceSeed row into the public ref shape.

    `payload_summary` and `origin_event_title` are derived from the JSONB
    columns; both have many possible shapes across engine versions, so we
    defensively `.get()` rather than assume.
    """
    payload = seed.payload or {}
    origin = seed.origin_decision or {}
    payload_summary = (
        payload.get("title") or payload.get("summary") or payload.get("category") or "consequence"
    )
    origin_title = origin.get("event_title") or origin.get("event", {}).get("title")
    return PostMortemSeedRef(
        id=seed.id,
        written_at_turn=seed.written_at_turn,
        fires_at_turn=seed.fires_at_turn,
        foreshadowed=seed.foreshadowed,
        fired=seed.fired,
        dormant=seed.dormant,
        payload_summary=str(payload_summary),
        origin_event_title=str(origin_title) if origin_title else None,
    )


def _to_one_line(text: str) -> str:
    """Collapse advisor response text to a single line.

    SPEC §11.1 calls for "advisor reactions in one-line summaries." The
    debate output is sometimes multi-sentence — we keep the first sentence
    and drop the rest. This is lossy but presentation-only; the full text
    remains in turn_logs.agent_responses for any future deep-dive view.
    """
    cleaned = " ".join(text.split())
    if not cleaned:
        return ""
    # Split on the first sentence-terminator that isn't preceded by an
    # abbreviation. Quick-and-dirty regex; we don't need parser-grade.
    match = re.search(r"[.!?](?:\s|$)", cleaned)
    if match:
        return cleaned[: match.end()].strip()
    return cleaned


def _first_sentence(text: str | None) -> str | None:
    if not text:
        return None
    return _to_one_line(text)


def _last_sentence(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = " ".join(text.split())
    # Find the start of the final sentence: split on terminators followed by
    # whitespace + capital, falling back to the whole string.
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z“\"'])", cleaned)
    if not parts:
        return cleaned
    return parts[-1].strip()
