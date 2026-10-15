# CLAUDE.md — backend/app/game/

Engine-specific conventions on top of root + `backend/CLAUDE.md`. Pure
functions, no LLM calls, no HTTP I/O outside `router.py`/`service.py`.

## Pure-function discipline

- Every function takes `ColonyState` in, returns a new `ColonyState` out. No in-place mutation of Pydantic models.
- No `.append()` on list fields — use `[*existing, new]`. No `.pop()`. `model_copy(update={...})` is the single mutation idiom.
- `model_copy(update=...)` does **not** re-run field validators. If you need the updated instance revalidated (rare — usually only when adjusting nested dicts round-tripped from JSONB), dump + `model_validate` instead.
- Pydantic `StrEnum` serializes to its `.value`; JSONB in Postgres sees the string, not the enum object. Round-trip through `model_validate` when reading back.

## Tool-call → validator → apply pipeline (invariant #11)

The single mutation pathway for all state changes:

1. `apply_tool_call(state, tc)` in `engine.py` calls `validate_tool_call(tc, state)` from `validator.py`.
2. Invalid → record `rejection_reason` on the tool call, append to `turn_tool_calls`, return state unchanged.
3. Valid → `_apply_valid_effect(state, tc)` mutates; tool call appended with `rejection_reason=None` and
   `applied_delta` (what actually changed after clamping).

Every node stamps `phase` on its calls (`engine.stamp_phase`): `debate`,
`prepare`, `resolve`, `autonomy`, `close`. End-of-turn decay is
`engine.close_turn` (`modify_legitimacy`, phase `close`). The full record
persists to `turn_logs.tool_calls`.

Validation: resource overflow clamps to max (no `would_overflow`); underflow
rejects (`would_underflow`).

Engine-emitted calls (axis heuristics, e.g. Collective agent gains
grievance when the group is harmed) use `caller_id=None` and go through
the **same** pipeline — so the Tool-Call Validity metric counts every
state change. No side-channel mutations.

## Resolution rules live in resolution.py

`derive_resolution_consequences` + its rule constants live in
`resolution.py` (the former `engine.py` Section 7, re-exported from `engine`).
It emits one `modify_legitimacy` call per triggered rule, each with a named
reason, so the preview can show every part. Reasons name the advisor and say
"you" for the player. `resolution.py` must not import `engine`.

## Engine-only tools

`tools.ENGINE_TOOL_NAMES` lists tools that do NOT appear in
`AGENT_TOOL_SCHEMAS` and that the LLM never authors. Validator rejects any
instance with `caller_id is not None` (reason: `engine_only_tool`). Current
members:

- `modify_legitimacy(delta, reason)` — emitted by
  `resolution.derive_resolution_consequences` when a picked proposal's
  payload or cross-proposal context warrants a legitimacy shift, and by
  `engine.close_turn` for the end-of-turn decay.
- `set_advisor_status(target_id, new_status, reason)` — emitted by the
  engine at pick time when an advisor-status transition rides in
  `tool_calls_on_pick`. Allowed transitions: `ACTIVE↔DETAINED`,
  `{ACTIVE,DETAINED}→DISMISSED`. Dismiss requires ≥3 other ACTIVE-or-
  DETAINED advisors remaining, so SPEC §7.3's three-action triple stays
  one-author-per-proposal.

`set_advisor_status` is in `_NESTED_TOOL_WHITELIST` (LLM may include it in
a proposal's `tool_calls_on_pick`). `modify_legitimacy` is NOT — only
`derive_resolution_consequences` emits it.

## propose_action: two-stage validation

`tool_calls_on_pick` is the proposal's effect list — nested tool calls
that fire at pick time. Validation is deliberately split:

- **Debate time** (`_validate_propose_action`): shape-only — each nested
  item must be `{name: str (in `_NESTED_TOOL_WHITELIST`), args: dict}`.
  Rejects on bad shape (`nested_tool_malformed`) or off-whitelist tool
  (`nested_tool_not_allowed`). Whitelist: `modify_resource`,
  `modify_health`, `adjust_grievance`, `set_advisor_status`. At most ONE
  `set_advisor_status` per `tool_calls_on_pick`
  (`multiple_status_mutations`) — mass-DETAIN would otherwise leave next
  turn with one ACTIVE author and break SPEC §7.3's three-author triple.
- **Pick time** (`nodes.resolve` → `engine.apply_tool_calls`): each
  nested call re-validates against current state. State-dependent
  rejects (e.g. `would_underflow` when resources have shifted between
  propose and pick) fire here and land in `turn_tool_calls` with
  `rejection_reason`, not in `_validate_propose_action`.

Nested calls are constructed as `ToolCall(name=..., caller_id=None,
args=...)` — engine-emitted at pick. The proposing agent's UUID lives
on `Proposal.proposed_by_agent_id`, not on the nested calls.

Don't re-introduce a hardcoded `tool_calls_on_pick=[]` in
`_effect_propose_action`. Phase 4 deferred this wiring; the 2026-04-30
playtest showed the resource dashboard freezes without it (PLAN.md
"Frozen-dashboard fix" entry). The chosen proposal is the player's
mechanical lever — without nested calls, choice is cosmetic.

## Setting YAMLs are procedural

- `app/game/settings/*.yml` is the only place setting content lives. Adding a setting is one YAML file + one test; no Python changes.
- `setting_loader.load_setting(id)` validates at load time against the `Setting` Pydantic schema. `FileNotFoundError` on missing id.
- `difficulty.validate_event_pool_for_setting(setting)` is the second-layer check — catches programmatically-constructed `Setting` instances that bypass the YAML loader (the YAML-level `EventCategory` model validator already enforces `can_kill_agent + min_turn >= 4`; this runs the full SPEC §7.2 suite).

## Difficulty rails are code, not TODOs

`difficulty.py` encodes SPEC §7.2 as pure functions:

- `apply_early_turn_floors(state, effect)` — per-tool-call clamp in turns 1-3 (legitimacy floor 20, no resource drop > 30% of max).
- `wealth_scale_factor(state) -> float` — 0.5 to 2.0, Rimworld-style threat budget from resource stability + agent health average.

Adjusting a rail means editing this file, not leaving a comment.

## Memory helpers

- `MEMORY_CAP = 10`. All agents cap at ten entries.
- `_pin_memory(agent, entry)` — prepends at head, dedupes by `(turn, text)` tuple (not `id()` — string interning makes `id` unreliable under JSONB round-trip).
- `_append_memory(agent, entry)` — FIFO; evicts oldest non-pinned first. If every slot is pinned, the new entry replaces the oldest pinned by turn number (silent drop was the Task 3.6 review bug).

## Coup resolution is rule-based

`engine.resolve_coup(state, initiator_id)` is the only place `end_state` becomes `OUSTED` or `KILLED` (apart from player-environment death → `KILLED` via `check_deaths`).

Four KILLED conditions, **all conjunctive** (any miss → OUSTED):

- `initiator.grievance >= 9`
- `max(accomplice.grievance) >= 9`
- `sum(boldness) > 0.5` strict
- `legitimacy < 15`

No LLM calls. All branches tested in `test_engine.py`.

## Gotchas

- `generate_run`: content (max_turns, advisor names/axes, portrait seeds) is a function of `(setting_id, seed)` only, via sha256 — never `hash(str)` (salted per process). Identity is separate: `game_id` folds in `user_id` + `run_key`, agent ids fold in `game_id`. `create_game_session` passes a fresh `run_key` because `game_id` is also the LangGraph thread_id; a reused one resumes a stale checkpoint. Paired playtests depend on this split.
- Rejected nested calls are stripped at preparation (`agent/prepare.py`), so `derive_resolution_consequences` only sees calls that will apply. Don't feed it unvalidated `tool_calls_on_pick`.
- Post-mortem reads seeds from `game_sessions.state.consequence_seeds` (`post_mortem.py`); the `consequence_seeds` table is deprecated and never written.

- `MissingGreenlet` after `await db.rollback()`: snapshot `profile.last_run_started_at` *before* rolling back — the async-loaded relationship detaches post-rollback.
- Between `db.add(game_session)` and `db.add_all(agents)` you need `await db.flush()` so the FK resolves; psycopg3 `executemany` batching otherwise blows up.
- Pydantic v2 `@field_validator` only sees fields declared **before** the validated field in `info.data`. If you need later siblings, use `@model_validator(mode="after")`.
- Float precision in multiplicative probability accumulators: `forced_escalation_probability` uses additive bookkeeping rather than product-of-complements to avoid epsilon drift across 5-9 turns.
- `_pin_memory` using `id()` for dedup fails under Python string interning (identical strings sometimes share `id`, sometimes don't — match by value).
- DB-touching tests must call `_require_db()` (see `test_game_router.py` or `test_me_endpoint.py`) so contributors without local Supabase get SKIP, not fail.

## File map

```
schemas.py          state types (Pydantic v2)
models.py           ORM (SQLAlchemy) — JSONB-backed state
tools.py            Anthropic tool schemas
validator.py        pure (ToolCall, ColonyState) → ValidationResult
engine.py           apply_tool_call, deaths, coup, advance_turn, close_turn
resolution.py       derive_resolution_consequences (pick-time legitimacy/grievance rules)
preview.py          simulate_option + render_effects: engine-rendered effects_preview
post_mortem.py      post-mortem + share data from stored runs
consequence.py      seed scheduler + foreshadow
events.py           select_event (seed > forced > natural priority)
difficulty.py       wealth_scale_factor + apply_early_turn_floors + pool validator
generator.py        procedural run creation
transformations.py  once-per-run axis shift
setting_loader.py   YAML → Setting schema validation
settings/*.yml      three launch settings
schemas_io.py       HTTP request/response shapes
service.py          daily-gate-aware run creation
router.py           POST /game/session + GET /game/{id}
exceptions.py       domain errors (daily gate, not found)
```
