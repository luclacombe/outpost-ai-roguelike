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

## Cache layout (2 breakpoints + colony state in user message)

Two `cache_control: {"type": "ephemeral"}` blocks per Haiku request, both in
`system`. Colony state goes in the user message, not a system block.

| Position | What | Invalidates |
|---|---|---|
| system[0] | preamble: game rules, tool contract, voice | never (module constant) |
| system[1] | personas: 4 advisors' id/name/role/axes/verbal_tic | never within a run |
| user message | colony_state (resources, legitimacy, player_health, per-agent memories/grievance/relationships) + current event + instructions | every turn |

**The last system block MUST be run-stable and MUST carry `cache_control`.**
Anthropic only persists a cache entry at the LAST cache_control breakpoint in
the request — earlier markers don't create their own durable entries. If the
last cache_control sits on a per-turn block (what we tried first), every turn
writes a fresh cache entry and nothing ever reads. Keeping the cache_control
on personas (run-stable) and pushing volatile content into the user message
is what unlocks cross-turn cache hits.

**Minimum cacheable prefix (Haiku 4.5): 4096 tokens cumulative through the
last cache_control.** The preamble + personas + tool schemas clear this with
headroom (~4600 tokens). If you trim the preamble, re-measure via
`client.messages.count_tokens(...)` and confirm cumulative tokens through
the last cc are still >4096. Dropping below silently skips caching and
returns 0 in both `cache_creation_input_tokens` and `cache_read_input_tokens`.

**Memories live in the rendered colony-state text (user message), not the
personas block.** Putting per-turn memories inside personas would mutate the
personas block's content every turn and break cache stability even if it sat
at the right cache_control position.

Expected hit rate after turn 2: 85–90% on input tokens. Verified by
`tests/integration/test_full_run.py`'s wrapped `cost_cents_of` that records
`cache_read_input_tokens` per call.

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
- **Sonnet 4.6 has no dated snapshot.** Per Anthropic's models overview
  (platform.claude.com/docs/en/about-claude/models/overview, verified
  2026-04-24), `claude-sonnet-4-6` IS the canonical Claude API ID — same
  situation for Opus 4.7. Only Haiku 4.5 publishes a dated form
  (`claude-haiku-4-5-20251001`). If Anthropic publishes a dated Sonnet 4.6
  later, swap it in `constants.py`. Until then, the alias is all we have.
- **Exception catch scope:** in `narrate_turn`, catch `anthropic.APIError` only,
  not bare `Exception`. Bare catches mask programming bugs in `call_sonnet` that
  should fail fast in development.
- **Tool whitelist assertion:** `AUTONOMOUS_TOOL_NAMES` is guarded at import by
  a subset check against `AGENT_TOOL_SCHEMAS`. If a tool is renamed or removed
  in `app/game/tools.py`, startup fails loudly rather than silently dropping
  the tool from the autonomous phase.
- **Autonomous sends the FULL tool schema, then filters on the response.**
  Sending only the autonomous subset to the SDK is tempting (it's what we
  did originally) but it shrinks the cached prefix below Haiku 4.5's
  4096-token minimum, silently disabling caching for the entire autonomous
  phase. Cost impact: ~4¢/run wasted on uncached fresh input. Current
  pattern: pass `tools=AGENT_TOOL_SCHEMAS`, instruct the model in the
  preamble to restrict itself to autonomous-appropriate tools, then drop
  any off-list `ToolCall` from `parse_debate_response` BEFORE handing the
  list to `engine.apply_tool_calls`. The runtime filter is the safety net;
  the prompt does the heavy lifting. Side benefit: autonomous shares the
  exact cached prefix as debate (same tools, same preamble, same personas),
  so its first call of each turn cache-reads from debate's earlier write.

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
  AND the cached `agent_spoke` frames (see invariant below) without touching
  the graph. Supports client reconnect.
- `/decide`: validates the proposal id against the paused snapshot, calls
  `await graph.aupdate_state(config, {"player_choice": id})`, then
  `graph.astream(None, config=..., stream_mode="updates")` to resume.
- `player_choice` in the *initial* state dict is meaningless — `generate_event`
  clears it. The correct path is always `aupdate_state` after pause.

**Load-bearing invariant: `/decide` runs the next turn's pre-pause half.**
The graph's conditional edge after `advance_turn` routes back to
`generate_event`, so a single `/decide` invocation runs:
`resolve → autonomous → check_deaths → narrate → advance_turn →
generate_event(N+1) → agent_debate(N+1) → apply_tool_calls(N+1) → [pause]`.
Two consequences a future session would otherwise re-discover the hard way:
  1. The next turn's `agent_spoke` frames are emitted on the previous
     turn's `/decide` SSE stream — not on its own `/turn`. The next
     `/turn` arrives at an already-paused graph and short-circuits.
  2. `service.stream_turn`'s short-circuit branch MUST re-emit the
     cached `debate_per_agent_text` as `agent_spoke` frames. Skipping
     it makes any consumer that builds its dashboard from `/turn`'s
     SSE alone (the playtest harness, the frontend on a fresh tab)
     render every advisor as `(silent)`. Caught the hard way during
     the 2026-04-29 smoke (24 false-positive `(silent)` allegations
     across 10 games). See `test_turn_after_decide_emits_cached_agent_spoke`.

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
