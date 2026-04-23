# CLAUDE.md — Outpost

Daily browser-based narrative roguelike. Four AI advisors run a fragile colony under your command. Permadeath. Post-mortem timeline as shareable artifact.

**Canonical docs — read on demand, not every turn:**
- `docs/SPEC.md` — game design, mechanics, tone
- `docs/ARCHITECTURE.md` — technical architecture
- `docs/PLAN.md` — phased implementation plan (current source of truth for what to build next)
- `docs/research/` — research source material for decisions

---

## Stack (locked)

- Backend: FastAPI (Python 3.12) + LangGraph (orchestration) + raw Anthropic SDK (LLM calls) + Docker
- DB: Supabase Postgres via SQLAlchemy async + psycopg3, direct connection port 5432
- Auth: Supabase Auth (Google OAuth), JWT validated locally in FastAPI via PyJWT (HS256)
- Frontend: Next.js 16 App Router + TypeScript strict + Tailwind v4 + shadcn/ui (pixel-art re-themed) + DiceBear
- Deploy: Railway (backend), Vercel (frontend)
- Observability: Langfuse (cloud Hobby, 50K obs/mo), LangSmith agentevals for CI
- LLM: Claude Haiku (dominant), Sonnet (optional narration), prompt caching mandatory

---

## Architectural invariants (never violate)

1. **Engine is source of truth.** LLMs propose via tool calls; engine validates and applies.
2. **No numbers from LLM freeform text** — every state change goes through a validated tool call.
3. **Dead means dead.** Agents removed on death never reappear.
4. **One run per account per day**, server-authoritative.
5. **Prompt caching on by default.** `cache_control: ephemeral` blocks for personality + colony state.
6. **Debate is one batched LLM call**, XML-delimited, not N calls per agent.
7. **Settings are schema-driven** — no hand-coded settings in app code.
8. **No LLM-generated images in MVP.** Portraits via DiceBear, backgrounds via CSS.
9. **RLS is safety net**, not primary auth. Filter by `user_id` in application code.
10. **Eval thresholds gate merges.** PRs dropping personality consistency < 0.72 or tool validity < 85% do not merge.
11. **All state mutations through validated tool calls — no engine-only side channel.** Axis-heuristic engine code (e.g., "Collective agent gains grievance when decision harms group") emits `adjust_grievance` tool calls rather than mutating state directly, so the Tool-Call Validity metric captures every state change.
12. **Thread-id discipline for LangGraph.** Every `graph.invoke` and `graph.ainvoke` passes `config={"configurable": {"thread_id": str(game_id)}}`. Missing thread_id silently breaks pause/resume between `/agent/turn` and `/decide`.
13. **Keep CLAUDE.md files current.** At phase close, audit new conventions, commands, anti-patterns, and gotchas — write them into the right `CLAUDE.md` (root or per-directory). Undocumented conventions cannot be followed.

---

## Commands

### Backend (`cd backend/`)

```bash
# Local dev
supabase start                  # starts local Postgres on 54322
docker compose up -d            # FastAPI with hot reload
docker compose down

# Direct (no Docker)
uv run uvicorn app.main:app --reload

# Tests
uv run pytest tests/ -v         # unit tests (fast, mocked LLM)
uv run pytest -m integration    # real LLM, costs tokens
uv run pytest -m eval           # eval pipeline, costs tokens

# Lint
uv run ruff check app/
uv run ruff format app/

# Migrations
uv run alembic revision --autogenerate -m "describe change"
uv run alembic upgrade head
supabase migration new <name>   # for RLS/auth schema
supabase db push
```

### Frontend (`cd frontend/`)

```bash
pnpm dev                         # localhost:3000
pnpm build                       # production build
pnpm lint
pnpm test
```

### Deploy

Frontend auto-deploys on push to main via Vercel. Backend auto-deploys via Railway's GitHub integration.

---

## MCP usage rules

**Use these proactively. Do not guess library APIs.**

- **context7** — before writing ANY code using FastAPI, LangGraph, Anthropic SDK, DiceBear, shadcn/ui, Tailwind v4, Next.js 16, or any library first touched in this project. Example:
  ```
  mcp__plugin_context7_context7__resolve-library-id  "fastapi"
  mcp__plugin_context7_context7__query-docs  <id>  "dependency injection async session"
  ```
- **supabase** — for DB operations, schema inspection, RLS policies, auth config, migration verification:
  ```
  mcp__plugin_supabase_supabase__list_tables
  mcp__plugin_supabase_supabase__execute_sql  "..."
  mcp__plugin_supabase_supabase__apply_migration  "..."
  ```
- **playwright** — after every UI phase, screenshot + visual regression check. Use for: viewport testing (1440, 1024, 768), verifying pixel-art aesthetic looks right, catching layout breaks.
- **vercel** — deployment status, build logs, env var management:
  ```
  mcp__plugin_vercel_vercel__list_deployments
  mcp__plugin_vercel_vercel__get_deployment_build_logs
  ```

---

## Superpowers skill triggers

Use the right skill for the task:

- **Before ANY creative work** (new feature, new component) — `superpowers:brainstorming`
- **Before writing implementation code** for a testable unit — `superpowers:test-driven-development`
- **Before claiming a task "done"** — `superpowers:verification-before-completion`
- **When hitting 2+ independent tasks** — `superpowers:dispatching-parallel-agents`
- **When executing `docs/PLAN.md`** — `superpowers:subagent-driven-development` OR `superpowers:executing-plans`
- **When a bug appears** — `superpowers:systematic-debugging` (never propose a fix without understanding the cause)
- **Before merging anything** — `superpowers:requesting-code-review`
- **After editing multiple TSX files** — `vercel:react-best-practices` auto-triggers

---

## Anti-patterns (stop and rethink if doing)

- Calling `time.sleep()` in async code — use `await asyncio.sleep()` and never call it to wait for state
- Mixing `sync` and `async` DB sessions
- Writing a new Pydantic v1 `@validator` — use `@field_validator`
- Parsing numbers out of LLM freeform text — that's what tool calls are for
- Hardcoding a setting's resources/advisors in Python — all settings live in `app/game/settings/*.yml`
- Committing `.env` files — use `.env.example`
- `git add -A` in the monorepo — add explicit paths to avoid slurping unrelated changes
- Using Supabase client for game-state queries — use SQLAlchemy async + psycopg3 (supabase-py only for auth admin)
- Using `ChatAnthropic` from LangChain — call `anthropic.messages.create()` directly for cache/batch control
- Skipping prompt caching "for simplicity" — cache_control is mandatory
- One LLM call per agent in debate — use one batched XML call (see ARCHITECTURE §3.6)
- Writing AI-tell prose: "unfortunately", "delve into", em-dash overuse, rule-of-three lists, "a tapestry of" (run `/humanizer` on user-facing prose)

---

## Code conventions

- **Python:** PEP 8 via ruff, 100-char lines, type hints everywhere, Pydantic for API + state, SQLAlchemy for DB
- **TypeScript:** strict mode, `any` forbidden, Tailwind v4 class-based, shadcn primitives for structural components
- **Tests:** co-located `tests/` directory mirroring `app/` structure. Mock Anthropic with `respx`. Use `integration` marker for real-LLM tests. `asyncio_mode = "auto"` in pyproject.
- **Commits:** Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, `test:`). One logical change per commit. Use the `/commit` skill.
- **File size:** split at 300 lines as a soft target; some modules (engine, graph) may exceed.
- **Error shape:** `{"error": {"code": "snake_case_code", "message": "...", "detail": {...}}}` for all API errors.

---

## Per-directory conventions

- `backend/app/auth/` — JWT validation, daily gate, profile bootstrap.
- `backend/app/game/` — game engine, pure functions, no LLM calls. State transitions are pure; side effects live in routers.
- `backend/app/agent/` — LangGraph + Anthropic SDK. All LLM calls live here. Every node is an `async def` over `GameTurnState`.
- `backend/app/game/settings/*.yml` — setting schemas. Add new settings as YAML files; no Python changes required.
- `frontend/app/(public)` — unauthenticated routes.
- `frontend/app/(authed)` — authenticated routes, layout enforces auth.
- `frontend/components/dashboard/` — dashboard panels. CSS Grid parent in `DashboardShell`.
- `frontend/components/postmortem/` — post-mortem timeline + counterfactuals.

---

## Writing voice (for user-facing text)

Grim-not-nihilistic. Short lines. Concrete nouns. No softeners ("unfortunately", "it seems that"). No AI tells. Dark humor permitted when in character. See `docs/SPEC.md` §14 for full guidance.

When writing advisor lines, events, or UI copy, pass through the `/humanizer` skill before committing.

---

## Current phase

See `docs/PLAN.md`. Update the phase status and checkbox completion in PLAN.md as work progresses. Entry/exit criteria gate phase transitions — do not skip exit verification.

---

## Docs & memory discipline

`CLAUDE.md` files are persistent memory across sessions. What isn't written here, future you (or a subagent) cannot know. Maintain actively:

- **Root `CLAUDE.md`:** stack + invariants + cross-cutting commands + MCP rules. Update when any of these change.
- **Per-directory `CLAUDE.md`:** conventions specific to that directory (e.g., `backend/app/game/CLAUDE.md` for engine patterns, `frontend/CLAUDE.md` for theming rules). Create when a directory develops genuinely non-obvious conventions that a future session would otherwise have to re-discover.
- **At phase close:** audit what new conventions, commands, gotchas, and anti-patterns emerged during the phase. Write them into the right `CLAUDE.md`. This is part of the phase exit — not optional, not deferrable.
- **When a bug root-causes to a non-obvious gotcha:** add it to the relevant `CLAUDE.md`'s anti-patterns section so the same bug doesn't bite next session.
- **When a convention changes:** update the existing line, don't duplicate. Stale conventions are worse than missing ones.
- **Per-directory files stay lean.** If `backend/app/game/CLAUDE.md` exceeds ~100 lines, the directory probably has too many conventions and needs refactoring, not more docs.

Design docs under `docs/` (SPEC, ARCHITECTURE, PLAN, research/) are loaded on demand — they capture what the project IS. `CLAUDE.md` files capture how the project WORKS in practice as it grows. Both must stay accurate.

## Known risks (full catalog in ARCHITECTURE §17)

- Supabase HS256 JWT deprecation trend — keep project on HS256 signing; plan JWKS fallback
- 5-min cache TTL may miss real player pacing — measure in Phase 4; fall back to 1-hour tier if needed
- Turn latency 9-24s from serialized LLM calls — mitigated by SSE streaming; measured in Phase 5
- SSE + token refresh mid-run — frontend must re-fetch token per request, reconnect via `thread_id` on 401
- Railway cold starts on free tier — accepted at MVP; upgrade if user complaints

## Errors & Learnings

Log non-obvious failures in `docs/PLAN.md` "Errors & Learnings" section. Include: symptom, root cause, fix, file changed. Prevents repeating the same mistake next session.
