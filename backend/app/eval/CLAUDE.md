# CLAUDE.md — backend/app/eval/

Eval-pipeline conventions on top of root + `backend/CLAUDE.md`. This package
hosts metrics computed offline (post-run or in CI), not in the live `/agent`
hot path.

## Where the SPEC lives

- Metric catalogue + thresholds: `docs/ARCHITECTURE.md` §12.
- Metric design rationale + Haiku-judge prompt template:
  `docs/research/05-eval-observability.md`.
- The merge gate (`personality_consistency >= 0.72`, `tool_validity >= 85%`):
  root CLAUDE.md invariant #10.

## Module map

- `personality.py` — embedding + Haiku-judge personality consistency.
- `coherence.py` — Haiku-judge decision coherence (action vs reasoning + personality). `score_run_coherence` caps concurrency at 4 via `asyncio.Semaphore` — never use a bare `asyncio.gather` for judge fan-out.
- `_judge_utils.py` — private helpers shared by Haiku-judge metrics (text extraction, code-fence stripping, JSON parsing). Import `_extract_text` / `_parse_judge_json` from here in any new judge module.
- `tool_validity.py` — `tool_validity_rate` + `tool_validity_breakdown` (engine-only).
- `repetition.py` — pairwise BLEU-2 narrative repetition (NLTK, no LLM).
- `balance.py` — aggregated survival/resource/forced-choice metrics across runs.
- `end_state.py` — observed end-state distribution vs SPEC target bands.
- `inter_run_similarity.py` — mean pairwise cosine on per-run narration; reuses `personality._get_model` and `_cosine`. Verdict band `[0.55, 0.80]`.
- `choice_spread.py` — pure oracle: applies each offered proposal to a copy of the paused colony via the resolve path; per-option deltas + max pairwise L1 spread. Outcomes come from `app.game.preview.simulate_option` and exclude end-of-turn decay. The simulator calls it at every pause (`RunRecord.choice_spread`).
- `paired.py` — per (setting, seed) cell comparison across players (persona harness + sweep): do end states / final numbers differ?
- `sweep_summary.py` — `scripts/sweep.py` summary.json + summary.md.
- `types.py` — `RunRecord`, the canonical Pydantic snapshot of one simulated run. Single source of truth — do not shadow with per-module variants.

## Naming convention

- `score_<metric>(...)` returns either `float` (single signal) or `dict[str, Any]`
  (compound — see `score_personality_consistency`). Async only when an LLM
  call is on the path; pure-numeric metrics stay sync.
- Threshold values are module-level constants (e.g. `PERSONALITY_THRESHOLD =
  0.72`). The eval pipeline imports the constant; tests import the constant.
  Never duplicate the number into a config file or another module.

## Lazy model loading

`personality.py::_get_model` lazy-loads `SentenceTransformer(EMBEDDING_MODEL_NAME)`
into a module-level `_model` and caches it for the process lifetime. The first
call downloads ~80MB, subsequent calls are free.

- **Never** load the model at module import — pytest collection would block on
  network and double CI time. Tests inject a fake encoder via the `model=`
  kwarg or monkeypatch `_get_model`.
- The Dockerfile pre-warms the cache at build time so production never pays
  the download. `Dockerfile.dev` does NOT — devs running `docker compose up`
  shouldn't pay 80MB on every rebuild; the lazy first-use path is fine.

## Anti-patterns

- Loading sentence-transformers (or any heavy ML model) at module import.
  Always lazy.
- Hardcoding `"all-MiniLM-L6-v2"` in two places. The string lives in
  `personality.EMBEDDING_MODEL_NAME` and is referenced from there. The
  Dockerfile is the documented exception (single literal, with a comment
  pointing back to this file).
- Hitting the real Anthropic API in tests. Inject a stub client via the
  `anthropic_client=` kwarg on judge functions; assert on the stub's
  `messages.create` call args.
- Using prompt caching for per-call judges. The prompt is small and varies
  per response — cache-write overhead would dominate.
- Reading tool calls from `turn_logs.state_snapshot` to compute validity. `engine.advance_turn` clears `turn_tool_calls` BEFORE the snapshot is dumped, so the snapshot is empty. Read `turn_logs.tool_calls` instead (the full phase-tagged record). `tool_validity_rate` counts LLM-originated phases (`debate`, `prepare`, `autonomy`, plus untagged legacy calls); `resolve` and `close` are engine-derived and excluded. `tool_validity_by_phase` gives the split.

## Test layout

- Eval-package unit tests live next to the modules: `tests/test_eval_*.py`. They import the metric directly and are part of the default `pytest tests/` run (no marker).
- Pipeline-level tests live under `tests/eval/` with their own `conftest.py` (Postgres-backed checkpointer + auth-user seeding). They are gated by `pytest.mark.eval` and excluded by `addopts = "-m 'not eval and not integration'"` in `pyproject.toml`. Run explicitly: `uv run pytest tests/eval/ -m eval`.
- The pipeline test `test_simulated_run_meets_thresholds` requires a reachable Postgres — it SKIPs (not fails) when unreachable. Use the standard local override: `DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:54322/postgres uv run pytest tests/eval/ -m eval`.
- Shared respx body factories (Haiku debate / autonomous / Sonnet narration) live in `tests/fixtures/_factories.py::make_haiku_debate_side_effect`. New eval tests reuse this rather than redefining call-routing logic per file.
