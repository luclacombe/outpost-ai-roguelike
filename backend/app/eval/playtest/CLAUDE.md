# CLAUDE.md — backend/app/eval/playtest/

LLM persona play-test harness. Drives ~42 simulated games where 7 LLM
personas (`claude --print`, model alias `sonnet`) play through the existing
engine. Companion: the scripted balance sweep (`scripts/sweep.py`).
Spec: `docs/superpowers/specs/2026-04-26-llm-playtest-harness-design.md`.
Run guide: `docs/playtest/README.md`.

## Module map

- `schemas.py` — Pydantic v2 contracts for per-turn output and run-level verdict
- `personas.py` — 7 persona instances + `get_persona`. Picking rules are
  decision-policy strings (not narrative bios) per arXiv 2512.06867
- `persona_session.py` — wraps `claude --print/--resume` for ONE game's persona
- `prompts.py` — system prompt, verdict prompt, count-retry nudge (worked
  examples are schema-validated in `test_turn_contract.py`)
- `turn_view.py` — `TurnViewTracker`: SSE frames + `game_sessions.state` →
  the UI-parity view the persona sees (field-to-component table in docstring)
- `turn_message.py` — renders that view as the markdown user message
- `turn_contract.py` — runtime checks on parsed output vs the rendered turn
- `subprocess_pool.py` — concurrency cap + retry/degrade per spec §10
- `llm_player.py` — drop-in for `tests/fixtures/simulated_player.py::SimulatedPlayer`
- `seeds.py` — `stable_seed` / `paired_cells` (shared with the sweep)
- `tracing.py` — `tracing_disabled()`: Langfuse off for harness runs
- `oracles.py` — wraps existing `app/eval/` modules into per-run + corpus computations
- `summaries.py` — per-run rows + corpus aggregates for `runs.json`
- `output.py` / `archive_markdown.py` — JSON archives + `runs.json` + `report.md`
  skeleton / LLM-friendly markdown archive
- `app_state.py` — `harness_app_state()` async CM that attaches `app.state.graph` + `app.state.checkpointer`. `httpx.ASGITransport` skips FastAPI lifespan, so both CLIs and `tests/eval/conftest.py::graph_postgres` MUST wrap their driving loop in this helper or the first `/turn` POST raises `'State' object has no attribute 'graph'`.

Orchestrators live at `backend/scripts/{play_test,sweep}.py` with shared
plumbing in `scripts/_harness.py` (NOT under `app/`). Layering: they import
`tests/fixtures/run_simulator.py`, so they must live in the top-level
`scripts/` tier — application code can't depend on test fixtures.

## Load-bearing conventions

- **Paired seeds.** `seeds.paired_cells` derives seeds from
  `sha256(setting, run_idx)`; every persona/policy plays the same cells.
  The seed only reaches the engine because `run_simulator` overrides
  `app.dependencies.get_run_seed_override` from a per-task ContextVar, and
  `generate_run` rolls content from `(setting_id, seed)` only. Never use
  `hash()` for seeds (salted per process). Changing `seeds._NAMESPACE`
  breaks comparisons with earlier runs.
- **Persona view = dashboard view.** Add a field to `turn_view.py` only if a
  `frontend/components/dashboard/*` component renders it. Player health,
  raw grievance/morale, autonomous actions and rejected calls stay hidden.
  Engine ground truth goes in the archive (oracle lines), never the prompt.
- **`(silent)` is reserved** for a living advisor with no reaction line;
  the fabricated-silence guard keys off it. No other section may print it.
- **Downgrade, don't raise, on contract violations.** `turn_contract` drops
  a flag whose quote is fabricated/paraphrased and nulls
  `outcome_matched_expectation` without a `## Last turn` section; raising
  would cost a retry and possibly the pick.
- **`difficulty` lives outside `VerdictAxes`.** It is centred (3 = well
  tuned), so it must not be averaged with the higher-is-better axes.
- **Choice-spread oracle reads the checkpoint, never forks it.**
  `advance_turn` is the sole DB-write node; forking the graph would
  clobber the real game row. The hook never raises (records `error`).
- **Personas are stateless across runs.** Each game is a fresh `claude`
  session. Cross-run patterns are the meta-aggregator's job.
- **`shell_dir = /tmp/outpost-playtest-shell/`** for `claude` subprocess cwd so
  project `CLAUDE.md` autoload can't leak into personas. The user's global
  `~/.claude/CLAUDE.md` still loads (documented in the spec's honest limits).
- **`--model` on every `claude` call.** It applies to "the current session"
  only, so resume / count-retry / verdict calls re-pin it.
  `--effort high` for personas, `max` for the aggregator; don't downgrade.
- **Langfuse off by default** in both CLIs (`--trace` opts in). The override
  mutates the shared `app.config.settings`; a blank env var would not work
  (config treats blank as unset and falls back to `.env`).
- **Local DB only.** `scripts/_harness.require_local_database` refuses a
  non-local `DATABASE_URL` (`--allow-remote-db` to override).
- **Typed-exception dispatch in SubprocessPool.** `PersonaTimeout` →
  `subprocess_hangs`; `PersonaSubprocessError` → `subprocess_crashes`;
  `PersonaParseError` → `malformed_json_count`. No string matching.
- **Per-turn shuffle of `displayed_order`** — defeats LLM position bias; the
  shuffle is deterministic per `seed`.
- **Pydantic models with UUID fields → `model_dump(mode="json")` before
  `json.dumps`** (`PostMortem` has UUID dict keys). See PLAN.md "Errors &
  Learnings → Playtest harness — UUID-in-PostMortem JSON serialization".

## Daily-gate handling

Each game provisions a fresh synthetic user (`scripts/_harness.py::_provision_synthetic_user`,
emails `playtest-<uuid>@outpost.local` / `sweep-<uuid>@outpost.local`), so
the one-run-per-day gate never rejects games 2..N. `_uuid_for_setting`
brute-forces a uuid whose `_pick_setting_id` returns the requested setting.
`cleanup_after_run` (default False; sweep `--cleanup`) deletes the user;
the FK cascade removes profiles, sessions, agents, turn_logs.

## Output artifacts

See `docs/playtest/README.md` for layout and every `runs.json` key. Same-day
reruns get a `-2` suffix (`_harness.next_free_dir`). **Remaining gap vs spec
§9:** no per-run `position_bias` Spearman; the aggregator derives it from
`raw/*.json`.

## Real-LLM smoke gate

`OUTPOST_PLAYTEST_REAL_LLM=1` (distinct from `OUTPOST_EVAL_REAL_LLM`). The
test reads the key via settings (`LLM_API_KEY`, else `ANTHROPIC_API_KEY`):

```bash
cd backend && OUTPOST_PLAYTEST_REAL_LLM=1 \
  DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:54322/postgres" \
  uv run pytest tests/eval/playtest/test_real_llm_smoke.py -v -m eval
```

`-m eval` skips `tests/_settings_isolation.py`, so the mocked eval-marked
smokes (`test_orchestrator_smoke.py`, `test_sweep_smoke.py`) only pass with
provider-default settings; with an OpenRouter `.env` their respx mocks miss
and fail closed.

## Reliability rails (sub-bug fixes)

- **`displayed_order` count-mismatch re-prompt.** `PersonaSession._parse_per_turn_with_count_retry`
  catches the count-only mismatch (via `_displayed_order_count_mismatch`),
  re-prompts ONCE via `--resume` with the three ids echoed, and only raises
  `PersonaParseError` if the second attempt also fails.
- **Defensive end-state resolution.** The simulator loop can exit with
  `game_sessions.end_state = NULL` (max_turns exhausted, defensive break).
  `scripts._harness.resolve_unfinished_end_state` reuses
  `engine.compute_end_state` with `turn_number = max_turns`. play_test
  persists it (post-mortem needs it); the sweep only records it
  (`end_state_resolved_by_harness`).
- **`VerdictResult` + `verdict_error`.** A twice-failed verdict surfaces as
  `PerRunArchive.verdict_error` / `verdict_failed` in `runs.json`.

## Known follow-ups (deferred)

- **Traceback files** under `raw/<run-id>-traceback.txt` on engine raise
  (spec §10); currently logged only.
- **CI workflow.** Manual invocation only by design (cost + subscription scope).
