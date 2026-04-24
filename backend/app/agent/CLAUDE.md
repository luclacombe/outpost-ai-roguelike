# CLAUDE.md — backend/app/agent/

Agent-system conventions on top of root + `backend/CLAUDE.md`. All LLM calls
live in this package. Engine functions (app/game/) stay LLM-free.

## Node pattern (LangGraph)

- Every node is `async def node(state: GameTurnState) -> GameTurnState` and
  returns a **partial** dict. LangGraph merges the returned dict into the
  carried state; unset keys are left alone.
- `GameTurnState` is a `TypedDict` with `total=False`. Read with `state.get(k)`;
  never assume a key is present unless you wrote it in an upstream node this turn.
- `generate_event` is the sole node that **resets** per-turn keys (debate_*,
  applied/rejected_tool_calls, proposals, player_choice, autonomous_tool_calls,
  narration, cost_cents_this_turn). Don't duplicate its clearing logic elsewhere.
- `advance_turn` is the **sole DB-write site**. No other node mutates
  `game_sessions` / `turn_logs`. The graph checkpointer owns mid-turn state.

## Invariant #11 — mutation through validator

Every state-changing tool call MUST route through `engine.apply_tool_calls` so
`validator.validate_tool_call` stamps `rejection_reason` on invalid ones and
`engine._apply_valid_effect` applies valid ones. Nodes never call
`state.colony_state.model_copy(update={"resources": ...})` directly.

**One documented exception:** `resolve` applies a flavor-based legitimacy shift
(`DOMINANT=0, MORAL=+2, WILDCARD=-3`) via `model_copy` because
`modify_legitimacy` is not a tool in our surface. The shift is engine-canonical
(not agent-proposed) and bounded; see the comment in `nodes.resolve`.

## Debate response parsing

`prompts.parse_debate_response(response, alive_agents)` splits an Anthropic
response into `(per_agent_text, tool_calls)`.

- `response.content` mixes `text` blocks (XML reactions) with `tool_use` blocks
  (native Anthropic tool calls). The parser handles both.
- `<agent name="X">...</agent>` sections map name → agent UUID via
  `alive_agents`. Dead agents must NOT be passed in — the parser will log WARNING
  and skip them otherwise.
- `tool_use.input["caller_agent_id"]` is **popped** into `ToolCall.caller_id` (as
  a UUID). The field never reaches `ToolCall.args`.
- Unknown agent names, malformed XML, malformed JSON, invalid UUIDs → logged at
  WARNING, skipped. The parser never raises on bad model output.

## Cache layout (3 breakpoints)

Three `cache_control: {"type": "ephemeral"}` blocks per request; user message is
not cached.

| Block | What | Invalidates | TTL |
|---|---|---|---|
| preamble | game rules + tool rules | never (build from constants) | 5 min rolling |
| personas | 4 advisors: id, name, role, axes, verbal_tic | never within a run | 5 min rolling |
| colony_state | resources, legitimacy, player_health, per-agent memories/grievance/relationships/health | every turn | 5 min rolling |

**Memories live in the colony-state block, not the persona block.** Putting
per-turn memories inside personas would invalidate the persona cache every
turn, dropping hit rate from ~85% to ~40%. See
`prompts.build_colony_state_block` for placement and ARCHITECTURE §3.7 for
rationale.

Anthropic caps cache_control at 4 breakpoints per request; we use 3 with room to
spare. Expected hit rate after turn 2: 85–90% on input tokens.

## Cost accounting

- Only `anthropic_client.cost_cents_of(response, model)` computes cost. It
  splits `usage.input_tokens` across fresh / cache_read / cache_creation
  buckets at their respective rates. `getattr(u, "cache_*_input_tokens", 0) or 0`
  — those fields are optional on the SDK's `usage` object.
- Three accumulation layers:
  - `GameTurnState.cost_cents_this_turn` — float, reset at `generate_event`,
    added to by debate + autonomous + narrate.
  - `ColonyState.turn_cost_cents` — run-cumulative integer cents, rounded from
    the float at `advance_turn`.
  - `GameSession.cost_cents` (DB) — mirror of `ColonyState.turn_cost_cents`.
  - `TurnLog.turn_cost_cents` (DB) — per-turn integer cents.
- Budget gate: `narrate_turn` skips the Sonnet call when
  `cost_cents_this_turn >= MAX_COST_CENTS_PER_TURN` and falls back to a
  templated single-line narration.

## Anthropic SDK quirks

- **Patch target for tests:** `cost_cents_of` is imported by name into
  `nodes.py` at module load. Monkeypatching
  `app.agent.anthropic_client.cost_cents_of` does NOT affect the reference
  inside nodes.py. Patch `app.agent.nodes.cost_cents_of` (the consumer).
  See `tests/integration/test_full_run.py` for the pattern.
- **Optional usage fields:** `cache_read_input_tokens` and
  `cache_creation_input_tokens` may be absent when caching is off. Use
  `getattr(..., 0) or 0` — this also handles the "present but None" case.
- **Sonnet 4.6 dated ID:** context7 has not surfaced the dated form yet; we use
  the alias `claude-sonnet-4-6`. Narration is optional and budget-gated, so the
  alias is safe. TODO in `constants.py` flags the need to resolve via
  `client.models.list()` at first run.
- **Exception catch scope:** in `narrate_turn`, catch `anthropic.APIError` only,
  not bare `Exception`. Bare catches mask programming bugs in `call_sonnet` that
  should fail fast in development.
- **Tool whitelist assertion:** `AUTONOMOUS_TOOL_NAMES` is guarded at import by
  a subset check against `AGENT_TOOL_SCHEMAS`. If a tool is renamed or removed
  in `app/game/tools.py`, startup fails loudly rather than silently dropping
  the tool from the autonomous phase.

## Graph pattern

- One compiled graph per app instance. Built in `app.main.lifespan` via
  `build_graph(checkpointer)` and attached to `request.app.state.graph`.
- Pause point: `interrupt_before=["resolve"]`. The first invocation returns
  after `apply_tool_calls`; `/decide` resumes from `resolve`.
- **Thread-id discipline (invariant #12):** every `ainvoke` / `astream` /
  `aget_state` / `aupdate_state` call site passes
  `config=thread_config(str(game_id))`. `service._require_thread_id` asserts
  this at the top of each streaming function.
- `stream_mode="updates"` yields `{node_name: partial_state}` dicts in node
  order. Pattern-match on node name in the SSE translator; LangGraph's
  internal keys (`__interrupt__`, etc.) are filtered defensively.

## Pause / resume semantics

- First `/turn` for a game: builds initial `GameTurnState`, runs until pause,
  emits `proposals_ready`.
- Repeat `/turn` while paused: short-circuits — re-emits the paused proposals
  without touching the graph. Supports client reconnect.
- `/decide`: validates the proposal id against the paused snapshot, calls
  `await graph.aupdate_state(config, {"player_choice": id})`, then
  `graph.astream(None, config=..., stream_mode="updates")` to resume.
- `player_choice` in the *initial* state dict is meaningless — `generate_event`
  clears it. The correct path is always `aupdate_state` after pause.

## SSE streaming (service layer)

- **Never raise `HTTPException` inside a `StreamingResponse` body.** Starlette
  flushes status + headers before iterating the generator, so raised exceptions
  surface as an `ExceptionGroup` / 500. Pattern:
  `router → prepare_<verb>(...)` (synchronous, raises) → returns dict →
  `StreamingResponse(stream_<verb>(...))`. See `service.prepare_turn` /
  `service.prepare_decide`.
- SSE frame format: `event: <name>\ndata: <json>\n\n`. Final sentinel is
  `event: done\ndata: [DONE]\n\n` — the `[DONE]` literal is not JSON-encoded.
  `_sse_frame` is the only place frames get constructed.
- `httpx.ASGITransport` does NOT run the FastAPI `lifespan`, so
  `app.state.graph` is unset in tests. Use `tests/test_agent_router.py`'s
  `graph_memory` / `graph_postgres` fixtures to inject the graph directly.

## Known deferrals

- **Abandoned paused checkpoints** accumulate in the LangGraph
  `checkpoints*` tables when a player starts a turn but never POSTs `/decide`.
  No cleanup job yet; revisit in a later phase.
- **Relationship UUIDs in the colony-state block** aren't name-resolved. The
  model parses them fine; readability is poor. Swap to names if eval shows
  comprehension regressions.
- **Sonnet 4.6 dated model ID** TODO in `constants.py` — resolve before Phase 5.
