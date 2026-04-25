# Outpost backend

FastAPI + LangGraph + raw Anthropic SDK. See `../CLAUDE.md` (root) and
`./CLAUDE.md` (backend) for stack, invariants, and conventions.

Daily commands and migration discipline live in `./CLAUDE.md`. This README
covers what isn't already in the CLAUDE files.

## Phase 5 — Observability + Eval

The eval pipeline scores a finished run against five metrics (tool-call
validity, personality consistency, decision coherence, narrative repetition,
end-state distribution) and gates merges per the thresholds in
`docs/ARCHITECTURE.md` §12.

### Running the eval pipeline locally

Mocked-LLM (fast, free, hermetic):

```bash
uv run pytest tests/eval/ tests/fixtures/test_run_simulator.py -m eval -v
```

Real-LLM (live Anthropic, ~13–20¢ per run):

```bash
OUTPOST_EVAL_REAL_LLM=1 \
  ANTHROPIC_API_KEY=sk-... \
  uv run pytest tests/eval/ -m eval -v -s
```

The mocked path stubs Anthropic via `respx`; the real path doesn't intercept
the network. Both require local Postgres (`supabase start`) — the driver
reads `game_sessions` + `turn_logs` to reconstruct the canonical tool-call
record (SSE drops resolve-phase rejected calls).

### CI workflow

`.github/workflows/eval.yml` runs the eval pipeline on every PR that touches
`backend/**`, `docs/PLAN.md`, or the workflow itself. The job uses a
Postgres service container, applies Alembic migrations, pre-warms the
MiniLM-L6-v2 cache, then runs the real-LLM pytest path under
`OUTPOST_EVAL_REAL_LLM=1`.

### Per-run budget ceiling

`OUTPOST_EVAL_BUDGET_CENTS` (default 50¢) is enforced as a hard pytest
assertion in `tests/eval/test_eval_pipeline.py::test_real_llm_simulated_run`.
Any single eval run exceeding the ceiling fails the CI job (red ❌). The
job also has a 25-minute wall-clock cap. Calibrated runs are 13–16¢, so the
ceiling leaves headroom for caching slips without masking real regressions.

### Langfuse traces

Every eval run streams traces to Langfuse Cloud under
`session_id = outpost-run-<game_id>`. Filter the dashboard by that session
id to inspect token usage, cache hit rate, and per-call latency. Threshold
catalogue lives in `docs/ARCHITECTURE.md` §12.
