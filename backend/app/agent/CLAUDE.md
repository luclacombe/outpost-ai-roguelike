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
  narration, turn_meta, cost_cents_this_turn). Don't duplicate its clearing
  logic elsewhere.
- `turn_meta` is the per-turn diagnostics dict. Each node returns
  `{**(state.get("turn_meta") or {}), ...}` so earlier keys survive;
  `advance_turn` persists it to `turn_logs.turn_meta`.
- `advance_turn` is the **sole DB-write site**. No other node mutates
  `game_sessions` / `turn_logs`. The graph checkpointer owns mid-turn state.

## Invariant #11 — mutation through validator

Every state-changing tool call MUST route through `engine.apply_tool_calls` so
`validator.validate_tool_call` stamps `rejection_reason` on invalid ones and
`engine._apply_valid_effect` applies valid ones. Nodes never call
`state.colony_state.model_copy(update={"resources": ...})` directly.

**No exceptions.** Every state mutation, including legitimacy shifts, flows
through `engine.apply_tool_calls`. The `resolve` node calls
`engine.derive_resolution_consequences(state, picked, all_proposals)` to
turn the picked proposal's payload + cross-proposal context into a list of
validated tool calls (`modify_legitimacy`, `adjust_grievance`), then folds
them through `engine.apply_tool_calls` alongside `picked.tool_calls_on_pick`.
The previous flavor → legitimacy table (`_FLAVOR_LEGITIMACY_SHIFT`) was
removed in favour of payload-driven dispatch — see `resolution.py`.

## Structured calls (debate and off-turn)

Debate and autonomous each make ONE forced tool call (`nodes._call_structured`):
`submit_debate` / `submit_offturn`, schemas and parsers in `debate_format.py`.
Why: the old format needed free text plus three parallel tool calls, and
cheap models made one call and stopped (bake-off 2026-10-22).

- **Forced single tool.** Only that tool is sent, with `tool_choice` set to it.
  If the host rejects `tool_choice` (error text matches `_TOOL_CHOICE_REJECTIONS`),
  the call is repeated with `auto` and the model id goes into
  `nodes._FORCED_REJECTED`, so later calls for it skip the probe (the set is
  per process; `tests/conftest.py` clears it between tests). Any other error
  propagates. `turn_meta["debate_tool_choice"]` and
  `turn_meta["autonomous_tool_choice"]` are `"forced"` or `"auto"`.
- **One request builder.** `nodes.build_debate_request` assembles the debate
  system blocks, user message, tool and allowed terms; `agent_debate` and
  `tests/eval/test_axis_proposals.py` both use it, so they cannot drift.
- **Per-game enums.** The schema pins advisor names and resource ids to this
  colony, so it is stable within a game and differs between games. Build it
  per call from the alive cast; never cache one across games.
- **Parsers never raise.** `parse_submit_debate` returns
  `DebateParse(per_agent_text, tool_calls)` (options become `propose_action`
  `ToolCall`s with nested `tool_calls_on_pick`; adapter-supplied reasons; no
  UUIDs in model output). Non-list containers, bad names and a missing tool
  call degrade to empty or skipped items. `parse_submit_offturn` does the same.
- **Non-Latin filter.** Reactions, options and off-turn text are dropped with
  a warning only when they hold a non-Latin word that is not in the allowed
  terms (`nodes._allowed_terms`: `build_allowed_terms` plus the setting's lore
  and narrative voice and this turn's event title and body), passed to the
  parsers as `allowed_terms=`. So arctic's own "СЕКРЕТНО" is kept and a stray
  CJK word is not (`narration_guard.non_latin_words`, `has_unknown_non_latin`).
  The parsers' default is no allowed terms, which drops all non-Latin letters.
- **Observability.** `turn_meta["llm_calls"]` has one `usage_record` per call
  (purpose, model, tokens, `cache_read_tokens`, `cost_cents`, `billed`).
- `prompts.parse_debate_response` still exists but no node calls it.

## Debate retry (< 3 valid proposals)

`agent_debate` counts proposals with `count_valid_proposals`, which runs
`prepare.prepare_proposals` on a throwaway result and counts proposals
classified `valid` (nothing stripped, a net non-zero applied change: `+5`/`-5`
or a clamp that cancels counts as none). A missing or all-stripped
`tool_calls_on_pick` is `no_effect`, not valid. The
count runs on the PRE-debate state, so a discarded first attempt never blocks
the retry's duplicate-name check. Under 3, it retries the same call ONCE
(forced again, or `auto` when the first call fell back to `auto`) with
`_DEBATE_RETRY_NOTE` appended to the user message; system blocks and tools are
unchanged, so the cached prefix still hits. The response with more
valid proposals wins (ties keep the first; reactions follow the kept
response); `turn_meta["debate_retried"]` records it (alongside
`debate_tool_choice` and `llm_calls`). Both calls go into
`cost_cents_this_turn`. No retry once the turn has reached
`MAX_COST_CENTS_PER_TURN`. Test debate bodies must give every proposal a
nested effect or the turn retries.

## Debate phase applies proposals only

`prepare.DEBATE_TOOL_NAMES = {"propose_action"}`. Any other debate call is
recorded `not_allowed_in_debate` and never applied. `prepare_proposals`
(run by the `apply_tool_calls` node):

- strips rejected nested calls (record `phase="prepare"`,
  `rejection_reason="stripped:<reason>"`);
- classifies each proposal `valid`, `degraded` or `no_effect` and surfaces up
  to 3 in that order;
- renders `effects_preview` from the engine (`preview.simulate_option` +
  `render_effects`) and sanitizes `risk_note`;
- writes the rendered triple to both `state["proposals"]` and
  `colony_state.turn_proposals`.

The LLM never writes a number the player sees (root anti-pattern).

## Prompt examples use placeholders

Prompt examples never use proper nouns or a real resource id (placeholders
only): a worked example with `resource_id: "fuel"` made models invent fuel in
settings that have none.
`tests/test_prompts.py::test_no_prompt_text_contains_a_name_pool_name`
enforces that no `name_pool` name appears in a prompt constant.

## Narration (guarded, own system block)

`narrate_turn` does not use the debate preamble (it taught `<agent>` markup
that leaked into prose). `narration.build_narration_system_block` is a single
run-stable block with `cache_control`: the setting's `narrative_voice` verbatim,
the roster, and a rules line ("Latin script only: write every name in Latin
letters. No timestamps, turn numbers or other scaffolding."). The user message
carries the full event, not the 60-char title. Output goes through
`narration_guard.validate_narration`, which splits hard from soft violations;
one retry with `RETRY_NOTE` on any violation (skipped once the turn cost cap is
reached). Keep the attempt with no hard
violation and fewest soft ones, else `narration.fallback_line` (`You backed
{proposer}: {name}.`). Outcome lands in `turn_meta["narration"]` (`source`:
llm/retry/fallback/cost_cap). `cost_cap` means the turn had hit
`MAX_COST_CENTS_PER_TURN`, so no narration call was made and the fallback line
was used.

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

**Caching caveat (controller ruling, 2026-10-22).** The cached prefix is now
about 2.9K tokens (preamble + personas + one small tool). That is below Claude
Haiku 4.5's 4096-token minimum, so on the Claude path (the `LLM_*` defaults)
debate and autonomous calls would not cache (0 in both
`cache_creation_input_tokens` and `cache_read_input_tokens`). Accepted: the game
runs on OpenRouter models whose caching has no such minimum (DeepSeek and MiMo
cache prefixes automatically, OpenAI from 1024 tokens). Measure per call with
`turn_meta["llm_calls"][*].cache_read_tokens`. If a Claude model is chosen
again, re-measure with `client.messages.count_tokens(...)` or pad the stable
preamble. Keep the stable-prefix-first layout and `cache_control` markers
(invariant #5).

Debate and autonomous send different tools, so they no longer share a cached
prefix with each other (each caches its own prefix across turns).

**Memories live in the rendered colony-state text (user message), not the
personas block.** Putting per-turn memories inside personas would mutate the
personas block's content every turn and break cache stability even if it sat
at the right cache_control position.

**`filter_cast` excludes DETAINED + DISMISSED, so the personas block
changes on advisor status transitions** (one cache miss when entering or
leaving DETAINED, permanent for DISMISSED). This is an intentional cost
of the acceptance gate "detained advisor doesn't author next turn"; do
not reintroduce a DETAINED-stays-in-cast optimization without a hard
look at whether the gate has changed. Status transitions are rare: the flat
`submit_debate` schema has no status effect, so the LLM cannot author one
today (see `backend/app/game/CLAUDE.md`).

Measure the hit rate from `turn_meta["llm_calls"][*].cache_read_tokens`;
`tests/integration/test_full_run.py` checks that some later call reads from
the cache (it traces `nodes.usage_record`).

## Provider config

- All calls go through `anthropic_client.call_fast` (debate, autonomous;
  model `settings.LLM_MODEL_FAST`) or `call_narration`
  (`settings.LLM_MODEL_NARRATION`). Eval judges call `client.messages.create`
  with `settings.LLM_MODEL_FAST` and `**request_extras()`. Never hardcode a
  model id at a call site; `constants.MODEL_HAIKU`/`MODEL_SONNET` are just
  the defaults and pricing keys.
- `build_client(settings)`: `LLM_AUTH_SCHEME=x-api-key` (Anthropic) sends
  `X-Api-Key`; `bearer` (OpenRouter) sends `Authorization: Bearer`. The other
  header is set to `Omit()` on purpose: the SDK otherwise reads
  `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` from the OS env and sends both.
- OpenRouter: `LLM_BASE_URL=https://openrouter.ai/api` (SDK appends
  `/v1/messages`). Auth is Bearer per the OpenAPI spec at
  openrouter.ai/docs/api/api-reference/anthropic-messages/create-a-message.
- `request_extras()` is the only place provider-specific body fields are
  added. Empty under defaults so Anthropic requests are unchanged.
  `LLM_DISABLE_THINKING=true` adds `thinking: {"type": "disabled"}`, which
  OpenRouter maps to `reasoning.enabled=false`
  (openrouter.ai/docs/guides/best-practices/reasoning-tokens, "Reasoning with
  the Anthropic Messages API"). DeepSeek V4.1 Flash reasons by default.
- `LLM_PROVIDER_ORDER` (comma list, e.g. `StreamLake,Novita`) pins OpenRouter
  hosts for `call_fast` ONLY (`request_extras(fast=True)` →
  `extra_body={"provider": {"order": [...], "allow_fallbacks": False}}`):
  listed hosts in order, nothing else. Narration and eval judges stay
  unpinned. `Settings` refuses a non-empty order unless `LLM_BASE_URL` is on
  openrouter.ai. It was added for V4 Pro (StreamLake, Novita) under the old
  parallel-tool-call format. The flat format made V4.1 Flash reliable, and the
  chosen model (`deepseek/deepseek-v4.1-flash-1017`) runs unpinned. A pin is
  per model: check the listed hosts serve the model before setting one, since
  `allow_fallbacks: False` turns a missing host into a failed call.
- Narration/thinking blocks: parsers only read `text` and `tool_use` blocks,
  so any `thinking` block a provider returns is skipped harmlessly.

## Cost accounting

- Only `anthropic_client.cost_cents_of(response, model)` computes cost. The
  provider's billed cost (`usage.cost` in USD, read from
  `response.usage.model_extra`) wins over the price table; `usage_record` is the
  per-call record (`billed` says which source). Without it, it
  splits `usage.input_tokens` across fresh / cache_read / cache_creation
  buckets at their respective rates. `getattr(u, "cache_*_input_tokens", 0) or 0`
  — those fields are optional on the SDK's `usage` object.
- Rates: `pricing_for(model)` reads `settings.LLM_PRICING_OVERRIDES` first,
  then `constants.MODEL_PRICING`. Unknown model → ValueError, and the
  configured fast/narration models are checked at import so a typo fails
  startup instead of zeroing the budget rails. Pass the configured model id
  (`settings.LLM_MODEL_*`), not `response.model`: OpenRouter echoes a dated
  canonical slug (e.g. `deepseek/deepseek-v4.1-flash-20260910`).
- Three accumulation layers:
  - `GameTurnState.cost_cents_this_turn` — float, reset at `generate_event`,
    added to by debate + autonomous + narrate.
  - `ColonyState.turn_cost_cents` — run-cumulative integer cents, rounded from
    the float at `advance_turn`.
  - `GameSession.cost_cents` (DB) — mirror of `ColonyState.turn_cost_cents`.
  - `TurnLog.turn_cost_cents` (DB) — per-turn integer cents.
- Budget gate: `narrate_turn` skips the narration call when
  `cost_cents_this_turn >= MAX_COST_CENTS_PER_TURN` and falls back to a
  templated single-line narration.

## Anthropic SDK quirks (apply to every provider via the SDK)

- **Patch target for tests:** `call_fast`, `call_narration` and `usage_record`
  are imported by name into `nodes.py` at module load. Monkeypatching
  `app.agent.anthropic_client.<name>` does NOT affect the reference inside
  nodes.py. Patch `app.agent.nodes.<name>` (the consumer). `nodes.py` no
  longer imports `cost_cents_of`; it reaches it through `usage_record`. See
  `tests/integration/test_full_run.py` (`usage_record`) and
  `tests/test_nodes.py` (`call_fast`).
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
  not bare `Exception`. Bare catches mask programming bugs in `call_narration` that
  should fail fast in development.
- **Tool whitelist assertion:** `AUTONOMOUS_TOOL_NAMES` is guarded at import by
  a subset check against `AGENT_TOOL_SCHEMAS`. If a tool is renamed or removed
  in `app/game/tools.py`, startup fails loudly rather than silently dropping
  the tool from the autonomous phase.
- **Autonomous sends only `submit_offturn`.** The old rule (send the full
  `AGENT_TOOL_SCHEMAS` for Haiku 4.5's cache minimum) is gone: the model sees
  only the `submit_offturn` kinds. The `AUTONOMOUS_TOOL_NAMES` runtime filter
  in `autonomous_actions` remains as a second line of defence, and the import
  subset check above still guards it. See the caching caveat above.

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
- **Checkpointer allowlist:** `app/agent/checkpointer.py::_ALLOWED_MSGPACK_TYPES`
  pins the exact set of `app.game.schemas` types the LangGraph serializer
  will round-trip. Adding a new schema enum or class that ends up inside
  `ColonyState` requires extending this tuple, otherwise the saver logs
  `Blocked deserialization of ...` and refuses the read. Test:
  `tests/test_checkpointer.py::test_round_trip_emits_no_unregistered_warnings`.

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
