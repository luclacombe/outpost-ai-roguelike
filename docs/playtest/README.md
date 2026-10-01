# docs/playtest/

Output directory for the two in-process playtest harnesses:

- **Persona harness** (`scripts/play_test.py`): seven LLM personas play
  through the live FastAPI surface (`/game/session`, `/game/{id}/turn`,
  `/game/{id}/decide`). Writes `docs/playtest/<YYYY-MM-DD>[-N]/`.
- **Balance sweep** (`scripts/sweep.py`): scripted `SimulatedPlayer`
  policies (`moral`, `pragmatic`, `random`), no persona LLM. Writes
  `docs/playtest/<YYYY-MM-DD>-sweep[-N]/`.

Both answer the baseline questions: how difficult is the game, do decisions
matter, can you win and lose, is there strategy, does the loop make sense,
are there bugs, does the engine work.

Spec: `docs/superpowers/specs/2026-04-26-llm-playtest-harness-design.md`.
Code: `backend/scripts/{play_test,sweep,_harness}.py` (CLIs),
`backend/app/eval/playtest/` (persona harness library),
`backend/app/eval/{choice_spread,paired,sweep_summary}.py` (oracles and
summaries), `backend/tests/fixtures/run_simulator.py` (HTTP driver).

## How a game is measured

- **Paired seeds.** Seeds come from `sha256(setting, run_idx)`
  (`app/eval/playtest/seeds.py`), never `hash()`. Every persona (or policy)
  plays the same `(setting, seed)` cells, and the seed reaches the engine
  through the `get_run_seed_override` dependency, so a cell is the same
  starting colony (advisors, turn count) and the same event RNG for every
  player. Engine-LLM output is still sampled: paired games share starting
  conditions, not identical debates. `runs.json.paired` / `summary.json.paired`
  report, per cell, whether players reached different end states.
- **Choice-spread oracle** (`app/eval/choice_spread.py`). At every pause the
  driver reads the paused checkpoint (read-only `aget_state`) and applies
  each of the three proposals to its own copy of the colony through the
  resolve path (`derive_resolution_consequences` + `apply_tool_calls`, then
  `check_deaths`). Per option: resource / legitimacy / player-health /
  grievance deltas, status changes, rejected calls, whether it ends the run.
  `spread_l1_max` is the largest pairwise L1 distance between option vectors
  `[Δresource/max ..., Δlegitimacy/100, Δplayer_health/100]`: 0 means the
  choice changes nothing mechanically. Immediate resolution only (no
  autonomous phase, no decay). No LLM, no DB writes, no checkpoint fork.
- **UI parity for personas** (`app/eval/playtest/turn_view.py`). The persona
  sees what the dashboard shows: day N / max, legitimacy and resource bars
  with their warning tones, advisor status pills (incl. KIA), the event with
  category / severity / CONSEQUENCE tag, the event log, a "Last turn" block
  (pick, effects preview, narration, visible deltas, deaths), and each
  proposal's `effects_preview`. Not shown, because the UI never renders
  them: player health, raw grievance/morale, autonomous actions, rejected
  calls. The raw archives add the oracle's engine result per option so the
  aggregator can check previews against ground truth.
- **Persona output.** Per turn: pick, reaction,
  `outcome_matched_expectation` (previous pick vs its preview; null on turn
  1), flags incl. `confused` and `suspected_bug` (need a verbatim quote, else
  dropped). Per run: 7 axes (adds `meaningful_choices`, `strategic_depth`,
  `loop_clarity`), `difficulty` (1 trivially easy, 3 well tuned, 5 unfairly
  hard), `bugs_noticed` (≤5).

## Prerequisites

- **Local Supabase only.** `supabase start` (Postgres on `localhost:54322`),
  then `alembic upgrade head` if the DB is fresh (see `backend/CLAUDE.md`
  "Fresh-clone workflow"). Both CLIs insert synthetic `auth.users` rows and
  refuse a non-local `DATABASE_URL`. `.env` usually points at the remote
  project, so always prefix the commands below with the local override.
  Never run them against the remote project.
- **Engine LLM key.** `backend/.env` with `LLM_API_KEY` (OpenRouter, plus
  `LLM_BASE_URL` / `LLM_AUTH_SCHEME` / `LLM_MODEL_*` per `.env.example`).
  `ANTHROPIC_API_KEY` is only the fallback for Anthropic-direct setups.
- **Persona harness only:** `claude` CLI on PATH (personas run through
  `claude --print` / `--resume`, `--model sonnet --effort high`, billed to the
  Claude Code subscription). Subprocess cwd is `/tmp/outpost-playtest-shell/`
  (created if missing) so project `CLAUDE.md` doesn't leak into personas.
- **Langfuse is off by default** in both CLIs (would burn the free 50K
  obs/month). Pass `--trace` to opt in.
- **Long runs:** wrap in `caffeinate -i` so macOS doesn't sleep mid-run.

## Run

All from `backend/`. `LOCAL` below stands for
`DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:54322/postgres"`.

```bash
# Mocked smoke (no LLM spend): orchestrator + sweep + simulator on local DB.
# Caveat: `-m eval` skips tests/_settings_isolation.py, so settings come from
# .env. With an OpenRouter .env the api.anthropic.com respx mocks don't match
# and respx fails the requests closed (no spend, but the tests fail). Run it
# from a shell/.env with the provider defaults, as CI does.
DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:54322/postgres" \
  uv run pytest -m eval tests/eval/playtest/test_orchestrator_smoke.py \
  tests/eval/playtest/test_sweep_smoke.py tests/fixtures/test_run_simulator.py

# Real-LLM smoke: 2 personas x 1 cell x 3 turns (engine spend ~cents)
DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:54322/postgres" \
  uv run python -m scripts.play_test --personas optimizer,pessimist \
  --runs-per-persona 1 --max-turns 3 --max-concurrency 2

# Balance sweep: 3 policies x 10 seeds x 3 settings = 90 games
DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:54322/postgres" \
  caffeinate -i uv run python -m scripts.sweep

# Persona baseline: 7 personas x 6 paired cells = 42 games
DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:54322/postgres" \
  caffeinate -i uv run python -m scripts.play_test
```

`play_test` flags: `--out`, `--max-concurrency` (5), `--runs-per-persona`
(6 = 3 settings x 2 seeds), `--personas`, `--max-turns` (12),
`--persona-model` (`sonnet`), `--trace`, `--allow-remote-db`.
`sweep` flags: `--seeds` (10 per setting), `--concurrency` (5),
`--max-turns` (12), `--policies`, `--settings`, `--out`, `--cleanup`,
`--trace`, `--allow-remote-db`.

A smaller run is a prefix of a larger one (cells interleave settings), so
smoke cells reappear in the full run.

## Output

```
docs/playtest/<YYYY-MM-DD>[-N]/            # persona harness
├── runs.json   # per_run_summaries + corpus aggregates (see below)
├── report.md   # honest-limits boilerplate + <!-- META_AGGREGATOR_OUTPUT_BELOW -->
└── raw/
    ├── <persona>-<setting>-seed<n>.md     # turns + verdict + engine oracle lines
    └── <persona>-<setting>-seed<n>.json   # full archive incl. choice_spread records

docs/playtest/<YYYY-MM-DD>-sweep[-N]/      # balance sweep
├── summary.json  # overall / by_policy / by_policy_setting / paired / aborted
├── summary.md    # tables vs SPEC §10 end-state bands
└── raw/<policy>-<setting>-seed<n>.json
```

`runs.json` corpus keys: `flag_aggregates`, `verdict_axis_means`,
`difficulty`, `continue_tomorrow_rate`, `outcome_expectation`,
`flagged_quotes`, `bugs_noticed`, `end_states`, `choice_spread`, `paired`,
`corpus_oracles`, `harness_findings`.

Sweep stats per group: end-state counts/shares (out-of-band vs SPEC §10
marked), turns survived, final legitimacy / player health / resource
fraction, `picked_rejected_rate` (resolve-time, from the oracle),
`turn_log_rejected_rate` (persisted calls only, undercounts), choice-spread
stats, cost cents per game (`game_sessions.cost_cents`).

## Validation checklist (before aggregating)

- `runs.json` parses; `len(per_run_summaries) == expected count` (42 for prod).
- `harness_findings.aborted_runs` empty (or ≤ 2; investigate beyond that).
- `choice_spread.oracle_errors == 0`.
- DB ground truth: every `run_id` has rows in `turn_logs`.
- Zero `(silent)` substring across `raw/*.md`. Non-zero indicates the engine
  fix at `app/agent/service.py::stream_turn` regressed — see
  `backend/app/agent/CLAUDE.md` §"Pause / resume semantics" for the invariant.

## Aggregator

```
/playtest-aggregate docs/playtest/<YYYY-MM-DD>
```

Slash command at `.claude/commands/playtest-aggregate.md`. Reads every
`raw/*.md`, `runs.json`, and a same-commit sweep `summary.json` if present.
Writes the synthesis below the marker, opening with direct answers to the
seven baseline questions.

## Failure modes

| Symptom | Cause | Where to look |
|---|---|---|
| `Refusing to run against non-local database host` | `DATABASE_URL` from `.env` points at remote | prefix the local override |
| `relation "game_sessions" does not exist` | fresh local DB | `alembic stamp c569693a0392 && alembic upgrade head` |
| `'State' object has no attribute 'graph'` on first /turn | Driver didn't wrap the loop in `harness_app_state()` | `app/eval/playtest/app_state.py` |
| 429/404/401 cascades at `concurrency > 1` | Auth ContextVar not set — concurrent games overwriting `app.dependency_overrides` | `tests/fixtures/run_simulator.py::_eval_user_id_ctx` |
| Daily-run gate rejects games 2..N | Synthetic user provisioning broken — each game needs its own `auth.users` row | `scripts/_harness.py::_provision_synthetic_user` |
| /turn replays an old run | game_id reused as LangGraph thread_id | `generate_run(run_key=...)` from `create_game_session` |
| All advisors render `(silent)` from turn 2+ | `/turn` short-circuit not re-emitting cached `agent_spoke` frames | `app/agent/service.py::stream_turn` |
| Persona fabricates `(silent)` quotes | Runtime guard drops the flag | `app/eval/playtest/turn_contract.py` |
| `aborted_runs` / sweep `aborted` non-empty | Per-game exception caught by orchestrator | the CLI's log output for the traceback |
| `malformed_json_count` > ~5% | Persona output schema drifted or prompt regressed | `app/eval/playtest/prompts.py` |

## Cost / time

- Persona harness (42 games, concurrency 5, max_turns 12): ~30-50 min wall.
  Persona side billed to the Claude Code subscription.
- Engine LLM cost depends on the configured provider (DeepSeek V4.1 Flash on
  OpenRouter since 2026-10-01). Read actual spend from
  `game_sessions.cost_cents` (sweep `cost_cents` column) after a smoke
  before scaling; the per-turn budget rail (`MAX_COST_CENTS_PER_TURN`)
  caps narration.

## References

- Design spec — `docs/superpowers/specs/2026-04-26-llm-playtest-harness-design.md`
- Implementation plan — `docs/superpowers/plans/2026-04-26-llm-playtest-harness.md`
- Eval pipeline thresholds — `docs/ARCHITECTURE.md` §12
- Per-directory conventions — `backend/app/eval/playtest/CLAUDE.md`
- Aggregator command — `.claude/commands/playtest-aggregate.md`
- Honest-limits boilerplate (verbatim) — design spec §0
