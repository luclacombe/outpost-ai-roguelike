"""LangGraph nodes for the Outpost turn graph.

Each node is an `async def` over `GameTurnState` and returns a partial
update that LangGraph merges into the carried state. The graph pauses
via `interrupt_before=["resolve"]` (wired in Task 4.6); nodes above
`resolve` run pre-pause, nodes from `resolve` onward run post-pause.

Invariants enforced here:
  * #5 — prompt caching is mandatory: system blocks come from `prompts`
    builders which stamp `cache_control: ephemeral` on each block.
  * #6 — one batched LLM call per debate. `agent_debate` makes one
    `call_fast` regardless of how many advisors are alive, plus at most one
    retry (same system blocks + tools) when it yields < 3 valid proposals.
  * #11 — state mutations go through `engine.apply_tool_calls`. Nodes
    never poke fields on `ColonyState` directly for gameplay effects.
    `resolve` derives consequence tool calls (modify_legitimacy,
    adjust_grievance) from the picked proposal's payload via
    `engine.derive_resolution_consequences` and folds them through the
    same validated pipeline as the proposal's own nested calls.
"""

from __future__ import annotations

import logging
import random
from datetime import UTC, datetime
from uuid import UUID

import anthropic
from sqlalchemy import select

from app.agent.anthropic_client import call_fast, call_narration, cost_cents_of
from app.agent.constants import MAX_COST_CENTS_PER_TURN
from app.agent.prompts import (
    build_autonomous_user_message,
    build_colony_state_block,
    build_debate_user_message,
    build_personas_block,
    build_preamble_block,
    parse_debate_response,
)
from app.agent.state import GameTurnState
from app.config import settings
from app.database import async_session_factory
from app.game import consequence, engine, events, setting_loader
from app.game.models import GameSession, TurnLog
from app.game.schemas import (
    AdvisorStatus,
    Agent,
    ColonyState,
    DecisionRef,
    ResolvedEvent,
    SeedPayload,
    ToolCall,
)
from app.game.tools import AGENT_TOOL_SCHEMAS

logger = logging.getLogger(__name__)

# Whitelist of tools the autonomous (post-pause, introspective) phase offers
# to the model. `propose_action`, `modify_resource`, and `modify_health` are
# deliberately excluded — they belong to the pre-pause debate where the player
# has visibility and control. Keeping this list in one place makes the scope
# of off-turn agent behaviour auditable from the filter alone.
AUTONOMOUS_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "adjust_own_grievance",
        "remember",
        "speak_to_agent",
        "adjust_relationship",
        "initiate_coup",
        "back_leader",
    }
)

# Fail fast at import if a name in the whitelist isn't a real tool — catches
# a tool rename or deletion in tools.py that would otherwise silently drop
# the tool from the autonomous phase.
_known_tool_names = {t["name"] for t in AGENT_TOOL_SCHEMAS}
_unknown = AUTONOMOUS_TOOL_NAMES - _known_tool_names
if _unknown:
    raise RuntimeError(
        f"AUTONOMOUS_TOOL_NAMES references unknown tools: {sorted(_unknown)}. "
        "Update nodes.py or tools.py."
    )
del _known_tool_names, _unknown

# Within-run event-template cooldown: the last N resolved events' category
# ids are excluded from the next pick. Wide enough to kill the within-run
# recycling reported in playtest §1.9 (recycler event repeating across
# turns 1-4) without starving the early-game pool.
_COOLDOWN_WINDOW = 4


def filter_cast(agents: list[Agent]) -> list[Agent]:
    """Authors of proposals / debate participants: alive AND ACTIVE only.

    DETAINED and DISMISSED advisors do not author. Status transitions
    invalidate the personas cache (one miss at enter, one at leave for
    DETAINED; permanent for DISMISSED). The colony state block still
    renders their status so the model has narrative continuity.
    """
    return [a for a in agents if a.is_alive and a.status == AdvisorStatus.ACTIVE]


async def generate_event(state: GameTurnState) -> GameTurnState:
    """Turn-start node: fire due seeds, pick the event, reset per-turn keys.

    Pure Python — no LLM call. Cost for this turn is reset to 0.0 here;
    downstream nodes accumulate into `cost_cents_this_turn`.
    """
    colony_state = state["colony_state"]

    # 1. Fire any consequence seeds that match this turn's number.
    new_state, fired_events = consequence.check_and_fire_seeds(colony_state)

    # 2. Load the setting YAML (validated at load time).
    setting = setting_loader.load_setting(new_state.setting_id)

    # 3. Deterministic per-(run, turn) RNG. XOR-mixing run seed, turn, and a
    #    module-local salt keeps replays identical without coupling to any
    #    library-specific seeding scheme.
    rng = random.Random(new_state.seed ^ (new_state.turn_number + 1) ^ 0x4C3D5E6F)

    # 4. Build the per-template cooldown set: category ids of the last N
    #    resolved events in this run. Cross-run cooldown would need a DB
    #    query threaded into the node; we scoped to within-run because the
    #    LangGraph nodes don't have a checkpointable db_session. This still
    #    kills the within-run recycling cited in playtest report §1.9
    #    (recycler event repeating verbatim across turns 1-4).
    recently_fired = {
        resolved.event.category for resolved in new_state.event_log[-_COOLDOWN_WINDOW:]
    }

    # 5. Select event — seed-fired events take priority when present.
    event = events.select_event(
        new_state,
        setting,
        rng,
        seeded_events=(fired_events or None),
        recently_fired_event_ids=recently_fired,
    )

    # 6. Surface the event both on ColonyState (persistent) and on the
    #    node-level key (convenience for downstream nodes and the API
    #    layer). Reset every per-turn field so a replay/resume starts
    #    from a clean slate.
    return {
        "colony_state": new_state.model_copy(update={"current_event": event}),
        "current_event": event,
        "cost_cents_this_turn": 0.0,
        "debate_per_agent_text": {},
        "debate_tool_calls": [],
        "applied_tool_calls": [],
        "rejected_tool_calls": [],
        "proposals": [],
        "player_choice": None,
        "autonomous_tool_calls": [],
        "narration": None,
    }


# Proposals the player must see each turn (one per flavor).
DEBATE_TARGET_PROPOSALS = 3

# Appended to the user message on the single debate retry. User-side only,
# so the cached system prefix (preamble + personas) and tools stay identical.
_DEBATE_RETRY_NOTE = (
    "\n\n<retry_note>Your previous reply had {valid} valid propose_action "
    "call(s). Call propose_action exactly 3 times (one DOMINANT, one MORAL, "
    "one WILDCARD), each with a non-empty name, description and "
    "tool_calls_on_pick.</retry_note>"
)


def count_valid_proposals(colony_state: ColonyState, tool_calls: list[ToolCall]) -> int:
    """Proposals the engine would accept from these debate tool calls.

    Runs the exact path the `apply_tool_calls` node runs
    (`engine.apply_tool_calls` -> `validator.validate_tool_call`) on a
    throwaway copy and counts the new `turn_proposals`. Pure; nothing is kept.
    """
    applied = engine.apply_tool_calls(colony_state, tool_calls)
    return len(applied.turn_proposals) - len(colony_state.turn_proposals)


async def agent_debate(state: GameTurnState) -> GameTurnState:
    """Batched debate: one call, XML-delimited per-agent reactions.

    Invariant #6 — one batched LLM call per debate regardless of alive count.
    If that call yields fewer than 3 engine-valid proposals, the same batched
    request is retried ONCE with a short corrective note in the user message
    (system blocks untouched, so the cache still hits). The response with
    more valid proposals wins (ties keep the first); both calls are costed.
    No retry once the turn's cost has reached MAX_COST_CENTS_PER_TURN.
    """
    colony_state = state["colony_state"]
    event = state["current_event"]
    alive_agents = filter_cast(colony_state.agents)

    # Cache layout: preamble + personas in system with cache_control on the
    # LAST block (personas). Colony state moves into the user message — it
    # changes every turn, so keeping it out of the cached prefix is what
    # lets Anthropic actually hit the cache on turn 2+. See Phase 4 learnings
    # in docs/PLAN.md for the empirical finding that drove this.
    system_blocks = [
        build_preamble_block(),
        build_personas_block(alive_agents),
    ]
    colony_state_text = build_colony_state_block(colony_state)["text"]
    user_message = f"{colony_state_text}\n\n{build_debate_user_message(event)}"

    response = await call_fast(
        system_blocks,
        user_message,
        tools=AGENT_TOOL_SCHEMAS,
        max_tokens=2000,
    )
    turn_cost = state.get("cost_cents_this_turn", 0.0)
    turn_cost += cost_cents_of(response, settings.LLM_MODEL_FAST)
    parsed = parse_debate_response(response, alive_agents)
    valid = count_valid_proposals(colony_state, parsed.tool_calls)

    if valid < DEBATE_TARGET_PROPOSALS and turn_cost < MAX_COST_CENTS_PER_TURN:
        retry_response = await call_fast(
            system_blocks,
            user_message + _DEBATE_RETRY_NOTE.format(valid=valid),
            tools=AGENT_TOOL_SCHEMAS,
            max_tokens=2000,
        )
        turn_cost += cost_cents_of(retry_response, settings.LLM_MODEL_FAST)
        retry_parsed = parse_debate_response(retry_response, alive_agents)
        retry_valid = count_valid_proposals(colony_state, retry_parsed.tool_calls)
        logger.warning(
            "agent_debate: %d/%d valid proposals for game %s turn %s; retried once, "
            "retry had %d; keeping %s",
            valid,
            DEBATE_TARGET_PROPOSALS,
            colony_state.game_id,
            colony_state.turn_number,
            retry_valid,
            "retry" if retry_valid > valid else "first",
        )
        if retry_valid > valid:
            parsed = retry_parsed

    return {
        "debate_per_agent_text": parsed.per_agent_text,
        "debate_tool_calls": parsed.tool_calls,
        "cost_cents_this_turn": turn_cost,
    }


async def apply_tool_calls(state: GameTurnState) -> GameTurnState:
    """Fold validated tool calls through the engine; split applied vs rejected.

    Pure Python. Uses `engine.apply_tool_calls`, which is invariant #11's
    single mutation pathway: it runs each tool call through the validator
    and stamps `rejection_reason` on rejects. We diff the pre/post
    `turn_tool_calls` list to recover the node-level split.
    """
    colony_state = state["colony_state"]
    pre_tc_len = len(colony_state.turn_tool_calls)

    new_colony_state = engine.apply_tool_calls(colony_state, state["debate_tool_calls"])

    newly_logged = new_colony_state.turn_tool_calls[pre_tc_len:]
    applied = [tc for tc in newly_logged if tc.rejection_reason is None]
    rejected = [tc for tc in newly_logged if tc.rejection_reason is not None]

    # Safety cap: UI only surfaces three choices. The engine does not
    # enforce the cap in Phase 3, so truncate here.
    proposals = new_colony_state.turn_proposals[:3]

    return {
        "colony_state": new_colony_state,
        "applied_tool_calls": applied,
        "rejected_tool_calls": rejected,
        "proposals": proposals,
    }


# ---------------------------------------------------------------------------
# Post-pause nodes (Task 4.5)
# ---------------------------------------------------------------------------


async def resolve(state: GameTurnState) -> GameTurnState:
    """Apply the player's chosen proposal, log it, schedule consequence.

    Pure Python — no LLM call. The node fires AFTER the graph's
    `interrupt_before=["resolve"]` pause, so `state["player_choice"]` is
    expected to be set by the `/decide` handler before resume.
    """
    colony_state = state["colony_state"]
    proposals = state["proposals"]
    chosen_id = state["player_choice"]

    picked = next((p for p in proposals if p.id == chosen_id), None)
    if picked is None:
        raise ValueError("player_choice did not match any surfaced proposal")

    # Apply the picked proposal's nested effects + the engine's derived
    # consequences (legitimacy, grievance overrules) — all through the
    # single validated mutation pipeline. SPEC §6.7 / invariant #11.
    derived = engine.derive_resolution_consequences(colony_state, picked, proposals)
    new_colony = engine.apply_tool_calls(
        colony_state,
        [*picked.tool_calls_on_pick, *derived],
    )

    # Append the resolved event to the colony's log.
    current_event = state["current_event"]
    resolved = ResolvedEvent(
        turn_number=new_colony.turn_number,
        event=current_event,
        chosen_proposal_id=picked.id,
        summary=picked.description,
    )
    new_colony = new_colony.model_copy(update={"event_log": [*new_colony.event_log, resolved]})

    # Consequence-seed scheduling — severity >= 3 events leave a trace.
    if current_event.severity >= 3:
        rng = random.Random(new_colony.seed ^ new_colony.turn_number ^ 0xD34DBEEF)
        foreshadow = consequence.decide_foreshadow(rng)
        fires_at = min(new_colony.turn_number + 2, new_colony.max_turns)
        # Schema requires fires_at_turn > written_at_turn. If we're close
        # to max_turns the clamp may equal turn_number — skip silently
        # rather than raise a ValidationError.
        if fires_at > new_colony.turn_number:
            # Strip any leading `Seeded: ` / `Aftermath of ` from the source
            # title before re-wrapping. Without this, an aftermath firing on
            # top of an aftermath produces titles like
            # "Aftermath of Seeded: Aftermath of X" — see report §1.3-1.4
            # and tests/test_consequence_titles.py.
            cleaned_title = consequence._clean_title_for_aftermath(current_event.title)
            seed = consequence.build_seed(
                game_id=new_colony.game_id,
                written_at_turn=new_colony.turn_number,
                fires_at_turn=fires_at,
                payload=SeedPayload(
                    event_id=f"aftermath_{current_event.id}",
                    severity=max(1, current_event.severity - 1),
                    category=current_event.category,
                    description=f"Aftermath of {cleaned_title}",
                ),
                origin_decision=DecisionRef(
                    turn_number=new_colony.turn_number,
                    event_id=current_event.id,
                    chosen_proposal_id=picked.id,
                ),
                foreshadow=foreshadow,
            )
            new_colony = consequence.schedule_seed(new_colony, seed)

    return {"colony_state": new_colony}


async def autonomous_actions(state: GameTurnState) -> GameTurnState:
    """Off-turn introspective Haiku call — one batched request.

    Skipped entirely when no advisors are alive. Uses the 6-tool
    autonomous whitelist (AUTONOMOUS_TOOL_NAMES) so the model cannot
    propose new actions or move resources behind the player's back.
    """
    colony_state = state["colony_state"]
    alive_agents = filter_cast(colony_state.agents)

    if not alive_agents:
        return {"autonomous_tool_calls": []}

    # Same cache layout as agent_debate — see that node's comment. Colony
    # state sits in the user message so the last cache_control in system
    # lands on the run-stable personas block.
    system_blocks = [
        build_preamble_block(),
        build_personas_block(alive_agents),
    ]

    # Prefer the Sonnet narration summary if the narrate node already ran;
    # otherwise synthesize one from the most recent event_log entry (which
    # `resolve` just appended). Cap at 300 chars to keep token cost bounded.
    #
    # IMPORTANT: keep "turn N" out of the synthesized prose. Advisors that
    # see "Turn 5: chose ..." in their off-turn summary echo the index back
    # into `remember(...)` calls, and the leaked turn counter then resurfaces
    # in the next turn's persona context — the §1.7 4th-wall leak caught in
    # the 2026-04-29 playtest. Structured turn_number lives in
    # <colony_state>; this prose stays index-free.
    summary = state.get("narration")
    if not summary:
        last_resolved = colony_state.event_log[-1] if colony_state.event_log else None
        if last_resolved is not None:
            summary = f"The colony chose '{last_resolved.summary}' for {last_resolved.event.title}."
        else:
            summary = "The decision has been resolved."
    summary = summary[:300]

    colony_state_text = build_colony_state_block(colony_state)["text"]
    user_message = f"{colony_state_text}\n\n{build_autonomous_user_message(summary)}"

    # Pass the FULL tool list — passing a smaller subset drops the cached
    # prefix below Haiku 4.5's 4096-token cache minimum and silently
    # disables caching for this node, costing ~9¢ per 10-turn run. The
    # preamble's "OFF-TURN (AUTONOMOUS) PHASE" section instructs the model
    # to restrict itself to introspective tools; the filter on parsed
    # tool calls below is the runtime safety net that drops anything
    # off-list before the engine sees it.
    response = await call_fast(
        system_blocks,
        user_message,
        tools=AGENT_TOOL_SCHEMAS,
        max_tokens=1500,
    )
    cost = cost_cents_of(response, settings.LLM_MODEL_FAST)
    parsed = parse_debate_response(response, alive_agents)

    # Drop any tool call the model emitted that isn't on the autonomous
    # whitelist (propose_action, modify_resource, modify_health,
    # adjust_grievance — all debate-phase only). Drops happen silently
    # before the engine; they never appear in turn_tool_calls.
    autonomous_tool_calls = [tc for tc in parsed.tool_calls if tc.name in AUTONOMOUS_TOOL_NAMES]

    pre_tc_len = len(colony_state.turn_tool_calls)
    new_colony = engine.apply_tool_calls(colony_state, autonomous_tool_calls)
    newly_logged = new_colony.turn_tool_calls[pre_tc_len:]
    applied_autonomous = [tc for tc in newly_logged if tc.rejection_reason is None]

    return {
        "colony_state": new_colony,
        "autonomous_tool_calls": applied_autonomous,
        "cost_cents_this_turn": state.get("cost_cents_this_turn", 0.0) + cost,
    }


async def check_deaths(state: GameTurnState) -> GameTurnState:
    """Flip `is_alive=False` for agents at 0 health and pin death memories.

    Thin wrapper: `engine.check_deaths` already handles memory pinning for
    both the dying agent and survivors, and sets `end_state=KILLED` when
    the player's health hits zero.
    """
    new_colony = engine.check_deaths(state["colony_state"])
    return {"colony_state": new_colony}


async def narrate_turn(state: GameTurnState) -> GameTurnState:
    """Optional Sonnet narration. Skipped if the run is over turn budget.

    Templated fallback is used when (a) we're over the per-turn cost cap
    or (b) the Sonnet call itself fails. The fallback is intentionally
    spare — the turn number and a bare colony status line. Not an attempt
    at literary prose, just a placeholder the UI can render.
    """
    colony_state = state["colony_state"]
    cost_so_far = state.get("cost_cents_this_turn", 0.0)

    fallback = f"Turn {colony_state.turn_number} ended. Colony stands."

    if cost_so_far >= MAX_COST_CENTS_PER_TURN:
        return {"narration": fallback}

    # One cache breakpoint (preamble). Personas + colony-state caches are
    # reserved for the debate path; narration is infrequent enough that
    # partial cache hit on the preamble is the right trade-off.
    system_blocks = [build_preamble_block()]

    picked_summary = (
        colony_state.event_log[-1].summary if colony_state.event_log else "(no decision)"
    )
    event_title = (
        colony_state.event_log[-1].event.title
        if colony_state.event_log
        else (state["current_event"].title if state.get("current_event") else "")
    )
    dead_names = [a.name for a in colony_state.agents if not a.is_alive]
    user_message = (
        f"<turn>{colony_state.turn_number}</turn>\n"
        f"<event>{event_title}</event>\n"
        f"<choice>{picked_summary}</choice>\n"
        f"<dead>{', '.join(dead_names) if dead_names else '(none)'}</dead>\n\n"
        "Write a 2-3 sentence narrator-voice recap of this turn. "
        "No meta-commentary, no bullet points. Grim-not-nihilistic tone."
    )

    try:
        response = await call_narration(system_blocks, user_message, max_tokens=500)
    except anthropic.APIError as exc:
        logger.warning("narrate_turn: narration call failed (%s); using fallback", exc)
        return {"narration": fallback}

    cost = cost_cents_of(response, settings.LLM_MODEL_NARRATION)

    # Sonnet returns a single text block for prose-only requests. Defensive
    # extraction — if something unexpected comes back, fall back gracefully.
    narration_text = fallback
    content = getattr(response, "content", None) or []
    for block in content:
        if getattr(block, "type", None) == "text":
            narration_text = getattr(block, "text", fallback) or fallback
            break

    return {
        "narration": narration_text,
        "cost_cents_this_turn": cost_so_far + cost,
    }


async def advance_turn(state: GameTurnState) -> GameTurnState:
    """Advance the run and persist — the sole DB-write site in the graph.

    Delegates to `engine.advance_turn` for the pure bits (turn++, decay,
    end-state evaluation, per-turn field clearing), marks dormant seeds
    on transition to a terminal end state, accumulates the turn's cost,
    writes both the `game_sessions` snapshot and the immutable `turn_logs`
    row, and surfaces `is_run_over` for the graph's terminal edge.
    """
    colony_state = state["colony_state"]
    prev_end_state = colony_state.end_state

    new_colony = engine.advance_turn(colony_state)

    if new_colony.end_state is not None and prev_end_state is None:
        new_colony = consequence.mark_dormant_seeds(new_colony)

    turn_cost_int = round(state.get("cost_cents_this_turn", 0.0))
    new_colony = new_colony.model_copy(
        update={"turn_cost_cents": new_colony.turn_cost_cents + turn_cost_int}
    )

    # DB persistence. Single session/transaction — either both the
    # GameSession mirror and the TurnLog land or neither does. The
    # turn_logs unique (game_id, turn_number) constraint means re-running
    # this node for the same pre-advance turn is a write error by design.
    game_id_uuid = UUID(state["game_id"])
    async with async_session_factory() as db:
        gs = (
            await db.execute(select(GameSession).where(GameSession.id == game_id_uuid))
        ).scalar_one()
        gs.state = new_colony.model_dump(mode="json")
        gs.current_turn = new_colony.turn_number
        gs.legitimacy = new_colony.legitimacy
        gs.player_health = new_colony.player_health
        gs.end_state = new_colony.end_state.value if new_colony.end_state is not None else None
        gs.cost_cents = new_colony.turn_cost_cents
        if new_colony.end_state is not None and gs.ended_at is None:
            gs.ended_at = datetime.now(UTC)

        current_event = state["current_event"]
        log = TurnLog(
            game_id=game_id_uuid,
            # Pre-advance turn — the turn that JUST completed.
            turn_number=colony_state.turn_number,
            event=current_event.model_dump(mode="json") if current_event else {},
            proposals=[p.model_dump(mode="json") for p in state.get("proposals", [])],
            chosen={"chosen_proposal_id": state.get("player_choice")},
            agent_responses=[
                {"agent_id": str(aid), "text": txt}
                for aid, txt in (state.get("debate_per_agent_text") or {}).items()
            ],
            autonomous_actions=[
                tc.model_dump(mode="json") for tc in (state.get("autonomous_tool_calls") or [])
            ],
            narration=state.get("narration"),
            state_snapshot=new_colony.model_dump(mode="json"),
            turn_cost_cents=turn_cost_int,
        )
        db.add(log)
        await db.commit()

    return {
        "colony_state": new_colony,
        "is_run_over": new_colony.end_state is not None,
    }
