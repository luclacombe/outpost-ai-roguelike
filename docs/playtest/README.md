# docs/playtest/

Output directory for the LLM play-test harness. Each invocation writes a
fresh dated subdirectory `docs/playtest/<YYYY-MM-DD>/`.

## What this is

Drives N simulated games through the live FastAPI surface (`/game/session`,
`/game/{id}/turn`, `/game/{id}/decide`) with seven Sonnet-4.6 personas as
players. Surfaces issues the unit test suite can't reach: tone breaks,
advisor distinctness, dramatic pacing, balance, dashboard legibility,
retention friction.

Spec: `docs/superpowers/specs/2026-04-26-llm-playtest-harness-design.md`.
Code: `backend/scripts/play_test.py` (CLI entry), `backend/app/eval/playtest/`
(harness library), `backend/tests/fixtures/run_simulator.py` (HTTP driver).

## Prerequisites

- `supabase start` — local Postgres on `localhost:54322`. Required for game
  persistence and the LangGraph Postgres checkpointer (the only saver that
  survives the `/turn → /decide` pause).
- `claude` CLI on PATH — the persona side runs through `claude --print` /
  `claude --resume` subprocesses against the user's Claude Code Max plan.
- `backend/.env` with `ANTHROPIC_API_KEY` set — used for the engine LLM
  (Haiku debate + Sonnet narration).
- An empty `/tmp/outpost-playtest-shell/` — persona subprocess `cwd`. Project
  CLAUDE.md autoload would leak project context into the persona; the
  isolation is load-bearing per spec §11.

## Run (production: 42 games)

```bash
cd backend && DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:54322/postgres" \
  uv run python -m scripts.play_test
```

Default config: 7 personas × 6 runs × 3 settings = 42 games, max_turns=12,
max_concurrency=5. Output anchored at the repo root: `docs/playtest/<today>/`.

CLI flags: `--out`, `--max-concurrency`, `--runs-per-persona`. To run a
non-default persona/settings list, drive `run_harness` directly with a
custom `PlaytestConfig` (see existing v6 smoke driver for the pattern).

## Run (smoke)

For a fast pre-flight before scaling:
- `tests/eval/playtest/test_real_llm_smoke.py` runs 1 persona × 1 setting × 3
  turns under `OUTPOST_PLAYTEST_REAL_LLM=1`. Cost ≤ ~10¢, wall ≤ 2 min.
- For 5-10 game smokes, drive `run_harness` from a one-off script with
  `runs_per_persona=2`, `max_turns=3`. Wall 5-15 min depending on cache.

## Output

```
docs/playtest/<YYYY-MM-DD>/
├── runs.json        # structured aggregate (per_run_summaries, flag_aggregates,
│                    # corpus_oracles, harness_findings, harness_version, git_sha)
├── report.md        # honest-limits boilerplate + <!-- AGGREGATOR_MARKER --> + body
└── raw/
    ├── <persona>-<setting>-seed<n>.md    # LLM-friendly narrative archive
    └── <persona>-<setting>-seed<n>.json  # schema-validated machine archive
```

The aggregator replaces everything below the marker in `report.md` with the
final synthesis. Boilerplate above the marker is canonical honest-limits text
(spec §0) and must stay verbatim.

## Validation checklist (before aggregating)

- `runs.json` parses; `len(per_run_summaries) == expected count` (42 for prod).
- `harness_findings.aborted_runs` empty (or ≤ 2; investigate beyond that).
- DB ground truth: every `run_id` has rows in `turn_logs`.
- Zero `(silent)` substring across `raw/*.md`. Non-zero indicates the engine
  fix at `app/agent/service.py::stream_turn` regressed — see
  `backend/app/agent/CLAUDE.md` §"Pause / resume semantics" for the invariant.

## Aggregator

```
/playtest-aggregate docs/playtest/<YYYY-MM-DD>
```

Slash command defined at `.claude/commands/playtest-aggregate.md`. Reads every
`raw/*.md` plus `runs.json`, writes the synthesis section in place. Designed
to run as an Agent subagent inside Claude Code with `--effort max`.

## Failure modes

| Symptom | Cause | Where to look |
|---|---|---|
| `'State' object has no attribute 'graph'` on first /turn | Driver didn't wrap the loop in `harness_app_state()` | `app/eval/playtest/app_state.py` |
| 429/404/401 cascades at `concurrency > 1` | Auth ContextVar not set — concurrent games overwriting `app.dependency_overrides` | `tests/fixtures/run_simulator.py::_eval_user_id_ctx` |
| Daily-run gate rejects games 2..N | Synthetic user provisioning broken — each game needs its own `auth.users` row | `scripts/play_test.py::_provision_synthetic_user` |
| All advisors render `(silent)` from turn 2+ | `/turn` short-circuit not re-emitting cached `agent_spoke` frames | `app/agent/service.py::stream_turn` |
| Persona fabricates `(silent)` quotes against advisors who spoke | Defense-in-depth catches it; runtime validator drops the flag | `app/eval/playtest/persona_session.py::_validate_quotes_against_dashboard` |
| `aborted_runs` non-empty | Per-game exception caught by orchestrator | `/tmp/playtest-*.log` for the traceback |
| `malformed_json_count` > ~5% | Persona output schema drifted or system prompt regressed | `app/eval/playtest/persona_session.py::_build_system_prompt` |

## Cost / time (production 42-game run)

- Wall clock: ~30-50 min at concurrency=5, max_turns=12. Repeat runs benefit
  from prompt-cache warmth.
- Engine LLM: ~$15-30 (Haiku debate + Sonnet narration; 504 max turns at
  ~5¢/turn cache-warm).
- Persona LLM: billed against the user's Claude Code Max plan, no per-run
  charge.

## References

- Design spec — `docs/superpowers/specs/2026-04-26-llm-playtest-harness-design.md`
- Implementation plan — `docs/superpowers/plans/2026-04-26-llm-playtest-harness.md`
- Eval pipeline thresholds — `docs/ARCHITECTURE.md` §12
- Per-directory conventions — `backend/app/eval/playtest/CLAUDE.md`
- Aggregator command — `.claude/commands/playtest-aggregate.md`
- Honest-limits boilerplate (verbatim) — design spec §0
