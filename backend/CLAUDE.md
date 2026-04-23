# CLAUDE.md — backend/

Backend-specific conventions on top of the root `CLAUDE.md`. Keep lean — only
load-bearing patterns a future session would otherwise re-derive.

## Directory layout (convention)

- `app/main.py` — FastAPI factory + middleware registration. Routers added here.
- `app/config.py` — one `Settings` instance, read at import.
- `app/database.py` — one async engine, one `async_session_factory`, module-level.
- `app/middleware/` — cross-cutting HTTP middleware only (logging, rate limit).
- `app/<feature>/{router,service,schemas,models,dependencies,exceptions}.py` — added per phase. Routers are thin (parse → service → return); services own business logic; models = SQLAlchemy ORM; schemas = Pydantic I/O.
- `tests/` mirrors `app/` one-to-one. `conftest.py` holds shared fixtures.
- `alembic/versions/` — Python ORM migrations. RLS/trigger migrations go in `supabase/migrations/` (CLI-managed).

## Running things

All backend commands assume `cd backend/` first — the repo root has no `pyproject.toml`.

```bash
# Dev loop
supabase start                  # local Postgres on 54322
docker compose up -d            # FastAPI on 8000 with reload

# Fast tests (no Postgres, no LLM)
uv run pytest tests/ -v

# DB-touching tests against local Supabase (override needed because .env
# points at remote Supabase; OS env wins over .env in pydantic-settings)
DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:54322/postgres" \
  uv run pytest tests/ -v

# Migrations (same DATABASE_URL override rule applies outside Docker)
uv run alembic revision --autogenerate -m "describe change"
uv run alembic upgrade head

# Lint / format
uv run ruff check app/
uv run ruff format app/
```

Inside the dev container `DATABASE_URL` is overridden via `docker-compose.yml`
to `host.docker.internal:54322`, so `docker compose` never needs the env prefix.

## Configuration

`app.config.Settings` is constructed at import as `settings`. Any new required
field must be added _both_ to `Settings` _and_ to `.env.example`, _and_ the
user's local `.env` needs to be topped up — pydantic-settings does not fall
back to `.env.example`. Optional settings get `= None` or a sensible default.

Use `settings.FOO` directly; do not re-read `os.environ` in feature code.

## Tests

- `asyncio_mode = "auto"` is set in `pyproject.toml` — write plain `async def test_*` without decorators.
- HTTP tests use `httpx.AsyncClient` over `ASGITransport(app=app)` — in-process, no port binding. Fixture: `client` in `tests/conftest.py`.
- DB-touching tests should skip (not fail) when Postgres is unreachable so a `uv run pytest tests/` without Docker still runs clean.
- Mock Anthropic with `respx`. Real-LLM integration tests live under the `integration` marker, real-LLM eval games under `eval`. Default runs exclude neither, so gate them with explicit `-m integration` when you want them.
- Assert specific exception types (`pytest.raises(ValidationError)`, not `Exception`) — ruff B017 is on.

## Async pitfalls

- Never call blocking I/O inside `async def` handlers. Use `await` or `asyncio.to_thread()`.
- Never mix sync and async SQLAlchemy sessions.
- Always use `await asyncio.sleep()`, never `time.sleep()` — and don't use either to wait for state.
- Engine is created once at module import. Don't re-instantiate per request; use the shared `async_session_factory`.

## Anti-patterns

- Writing business logic in route handlers. Handlers parse inputs, call a service, return.
- Adding a new Pydantic v1 `@validator` — use `@field_validator` (v2 semantics).
- Using the Supabase client for game-state queries — SQLAlchemy async + psycopg3 only. `supabase-py` is reserved for auth admin.
- `pytest.raises(Exception)` — use the specific class.

## Migrations

- Python ORM changes → Alembic. Autogenerate needs `target_metadata = Base.metadata` in `alembic/env.py` once Phase 3 introduces `app.game.models`. A comment in `env.py` marks the line to update.
- RLS policies, auth-schema triggers, storage config → Supabase CLI migrations under `supabase/migrations/`. Alembic and Supabase CLI do not conflict — they manage disjoint schema surface.

## Docker

`.dockerignore` excludes `.venv/`, `.env`, `supabase/`, `tests/`, `.git/`. Don't add files to these paths expecting them inside the image. The dev compose mounts `./app`, `./alembic`, and `./tests` as volumes, so edits to those dirs are hot-reloaded without rebuilding.

## Errors & learnings

Log non-obvious failures in `docs/PLAN.md` under "Errors & Learnings" with symptom, root cause, fix, file. Cross-reference here only if a pattern becomes a permanent convention.
