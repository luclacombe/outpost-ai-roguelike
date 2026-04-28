# CLAUDE.md — backend/app/eval/playtest/

LLM persona play-test harness. Drives ~42 simulated games where 7 LLM
personas (Sonnet 4.6 via `claude --print`) play through the existing engine.
Spec: `docs/superpowers/specs/2026-04-26-llm-playtest-harness-design.md`.

## Module map

- `schemas.py` — Pydantic v2 contracts for per-turn output and run-level verdict
- `personas.py` — 7 persona instances + `get_persona`. Picking rules are
  decision-policy strings (not narrative bios) per arXiv 2512.06867
- `persona_session.py` — wraps `claude --print/--resume` for ONE game's persona
- `subprocess_pool.py` — concurrency cap + retry/degrade per spec §10
- `llm_player.py` — drop-in for `tests/fixtures/simulated_player.py::SimulatedPlayer`
- `oracles.py` — wraps 5 existing `app/eval/` modules into per-run + corpus computations
- `output.py` — emits markdown + JSON archives + `runs.json` + `report.md` skeleton

Orchestrator lives at `backend/scripts/play_test.py` (NOT under `app/`).
Layering: it imports from `tests/fixtures/run_simulator.py`, so it must live
in the top-level `scripts/` tier — application code can't depend on test fixtures.

## Load-bearing conventions

- **Personas are stateless across runs.** Each `claude --print` invocation is a
  fresh session. No memory threading, no cross-run fatigue. Cross-run patterns
  are the meta-aggregator's job, not the persona's.
- **`shell_dir = /tmp/outpost-playtest-shell/`** for `claude` subprocess cwd. This
  is the load-bearing isolation: project `CLAUDE.md` autoload is keyed to cwd, so
  running from a tmp dir means no project context leaks into personas. The user's
  global `~/.claude/CLAUDE.md` (preference-only, low-bias) still loads — that's
  acceptable and documented in the spec's honest-limits section.
- **Typed-exception dispatch in SubprocessPool.** Pool catches `PersonaTimeout` →
  `subprocess_hangs`; `PersonaSubprocessError` → `subprocess_crashes`;
  `PersonaParseError` → `malformed_json_count`. Do NOT replace with string-matching.
- **Per-turn shuffle of `displayed_order`** — defeats LLM position bias (Shi et al.
  2025: ~0.67 position consistency in 3-option list-wise judging). Shuffle is
  deterministic per `seed` so the harness is reproducible.
- **Effort: persona side `--effort high`, aggregator `--effort max`.** G-Eval
  pattern requires CoT before scoring; that needs thinking budget. Don't downgrade
  personas to `medium` — verdicts get noisy.

## Daily-gate handling (resolved)

The daily-run gate (`app/game/service.py::create_game_session` enforces "one run
per account per day") would otherwise reject games 2..N when a single user_id is
reused. `_drive_one_game` provisions a fresh synthetic user per game via
`_provision_synthetic_user` (mirrors `tests/eval/conftest.py::_ensure_auth_user`):
fresh `uuid.uuid4()` + insert into `auth.users` + `profiles`. Synthetic emails
are namespaced as `playtest-<uuid>@outpost.local`.

`PlaytestConfig.cleanup_after_run` (default `False`) controls whether the
synthetic user is deleted post-run via `_cleanup_synthetic_user`. Default keeps
the data for forensic re-analysis; smoke tests set it `True` to keep the test
DB clean. The `auth.users` row's cascade FK removes `profiles`, `game_sessions`,
`agents`, and `turn_logs` on delete.

## Output artifacts

`docs/playtest/<YYYY-MM-DD>/` per invocation. Same-day re-run gets `-2` suffix.

- `report.md` — honest-limits boilerplate above `<!-- META_AGGREGATOR_OUTPUT_BELOW -->`,
  body filled in by the aggregator slash command (see `.claude/commands/playtest-aggregate.md`).
- `runs.json` — structured aggregate. **Known schema gaps vs spec §9:** missing
  `aggregate_axis_means` (per axis × by_persona × by_setting), missing per-run
  `position_bias` Spearman, `flag_aggregates` is `{flag: total}` not
  `{flag: {total, by_persona, by_setting}}`. The aggregator currently derives
  these from `raw/*.json` instead. Fix in `output.py::write_corpus_artifacts` if
  delta-tooling needs precomputed slices.
- `raw/<persona>-<setting>-seed<n>.{md,json}` — dual-format per-run archives. The
  markdown is LLM-friendly (the meta-aggregator reads it); the JSON is
  schema-validated audit trail.

## Real-LLM smoke gate

`OUTPOST_PLAYTEST_REAL_LLM=1` (distinct from `OUTPOST_EVAL_REAL_LLM` — that
gates `tests/eval/test_eval_pipeline.py::test_real_llm_simulated_run`). Without
the flag, `tests/eval/playtest/test_real_llm_smoke.py` SKIPs. Manual run:

```bash
cd backend && OUTPOST_PLAYTEST_REAL_LLM=1 ANTHROPIC_API_KEY=... \
  DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:54322/postgres" \
  uv run pytest tests/eval/playtest/test_real_llm_smoke.py -v -m eval
```

Cost: ~10¢ for one 3-turn game. The 42-game invocation runs via
`uv run python -m scripts.play_test` (not pytest). Each game provisions its
own synthetic user, so multi-game runs no longer trip the daily-gate.

## Known follow-ups (deferred)

- **Materialize `runs.json` aggregates** to match spec §9 (axis means, by_persona/
  by_setting flag splits, per-run position-bias Spearman). Currently aggregator
  recomputes from raw archives.
- **Traceback files** under `raw/<run-id>-traceback.txt` on engine raise. Spec §10
  calls for this; current code only logs to stderr.
- **CI workflow.** No `.github/workflows/playtest.yml` exists. Manual invocation
  only by design (cost + Max-billing scope), but if nightly runs are wanted later,
  add the workflow + secrets.
