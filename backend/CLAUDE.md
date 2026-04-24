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
- Raising `HTTPException` inside a `StreamingResponse` body generator. Once iteration starts, status + headers are already flushed — the exception surfaces as a task-group error, not a clean 4xx JSON response. Split services into a `prepare_<verb>(...)` that runs pre-stream checks (and raises) and a streaming generator that assumes preconditions are met. Router calls `prepare_` synchronously, then hands the resulting dict to the generator. Pattern: `app/agent/service.py::prepare_turn`/`prepare_decide`.
- Relying on FastAPI lifespan in tests. `httpx.ASGITransport` does NOT run `lifespan`, so anything the `lifespan` hook attaches to `app.state` (e.g. `app.state.graph` in `main.py`) is unset in-process. Tests that hit routes depending on `app.state.X` need a fixture that sets it directly (see `tests/test_agent_router.py::graph_memory`/`graph_postgres`).
- Adding a new Pydantic v1 `@validator` — use `@field_validator` (v2 semantics).
- Using the Supabase client for game-state queries — SQLAlchemy async + psycopg3 only. `supabase-py` is reserved for auth admin.
- `pytest.raises(Exception)` — use the specific class.
- Assuming tokens are HS256. `decode_supabase_jwt` dispatches on the header `alg`: HS256 uses the symmetric secret, ES256/RS256 resolves via the project's JWKS endpoint (cached 5 min). Do not widen the algorithm whitelist beyond `{HS256, ES256, RS256}` without a threat-model review — the per-branch algorithm restriction is what blocks alg-confusion attacks.
- Pre-warming the PyJWKClient eagerly at import. The JWKS fetch is lazy; constructing `settings` at import must not depend on network reachability. If startup latency on the first asymmetric token becomes an issue, pre-warm inside the FastAPI `lifespan` hook, not at module import.

## Auth & dependency composition

- `app.dependencies.get_current_user` is the only auth gate. It returns the raw Supabase JWT claim dict — feature routers pull `user["sub"]` and wrap with `UUID(...)` themselves. Do not introduce a `CurrentUser` Pydantic model unless more than one router needs extra derived fields; the claims dict keeps the dependency honest.
- `decode_supabase_jwt` accepts HS256 (legacy symmetric secret) *and* ES256/RS256 (JWKS). `app/auth/jwks.py` owns the PyJWKClient singleton pointed at `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`. Tests can monkeypatch `PyJWKClient.get_signing_key_from_jwt`; see `test_auth_service.py::test_decode_es256_via_jwks` for the pattern. The real-Supabase integration test (`test_integration_supabase_jwks.py`) is marked `integration` and skipped unless `SUPABASE_ANON_KEY` is in the env.
- `app.dependencies.get_db` yields a short-lived `AsyncSession`. Routers are responsible for `await db.commit()` when they mutate. Never re-use a session across requests.
- `bearer_scheme = HTTPBearer(auto_error=True)` returns 403 on missing/malformed `Authorization`; bad-but-present tokens surface 401 from `decode_supabase_jwt`. Tests assert `status_code in (401, 403)` to cover both.
- Tests stub auth by overriding `get_current_user` in the `client` fixture. The stub `sub` is a real UUID (`00000000-0000-0000-0000-000000000001`) because routers call `UUID(sub)` — a placeholder string breaks the route before it reaches the assertion.
- DB-touching router tests must call `_require_db()` (see `tests/test_me_endpoint.py`) so contributors without local Postgres get SKIP, not failure.

## Migrations

- Python ORM changes → Alembic. `target_metadata = Base.metadata` is wired in `alembic/env.py` with an `include_object` filter that ignores non-public schemas and any reflected table absent from our ORM — this keeps autogenerate from fighting Supabase-CLI-managed tables. Do not drop the filter when adding new models; extend ORM metadata instead.
- Alembic's ConfigParser breaks on URL-encoded passwords. `env.py` calls `settings.DATABASE_URL.replace("%", "%%")` before `set_main_option`; preserve that on any future edit.
- Cross-schema FKs (e.g. `profiles.id -> auth.users.id`) don't survive `--autogenerate` round-trips — hand-edit the generated migration to add them. See `alembic/versions/c569693a0392_create_profiles_table.py`.
- **Split discipline:** Alembic owns `public.*` DDL and RLS for pure public-schema tables. Supabase CLI owns anything in `auth.*` (triggers on `auth.users`, etc.), cross-schema FKs that Alembic can't round-trip (e.g., `profiles(id) → auth.users(id)`), and the one "bootstrap" DDL migration for `profiles` that has to exist before the profile-RLS CLI migration can run at `supabase start` time. The goal remains disjoint surface — a change to a table's shape should live in one migration tree only, not both.
- **Fresh-clone workflow:** `supabase start` applies CLI migrations in timestamp order before Alembic runs. Because `profiles` is created by a CLI bootstrap migration, a fresh DB will already have that table when Alembic first runs. Run `alembic stamp c569693a0392` after `supabase start` and before `alembic upgrade head` so Alembic doesn't try to re-create it. Game tables have no such concern — Alembic owns them end-to-end (DDL + RLS).
- Remote Supabase direct-connect (5432) is unreachable from some dev networks over IPv6. Workflow: apply Alembic locally against `localhost:54322`, mirror to the remote project via the `supabase` MCP `apply_migration` tool, and seed `public.alembic_version` with the revision id so future runs against remote see the head.
- **LangGraph checkpointer tables** (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`) are created by `saver.setup()` at app startup (see `app/agent/checkpointer.py`), NOT by Alembic. `alembic/env.py`'s `include_object` filter already skips them because they're reflected-not-in-our-ORM; don't try to autogenerate or stamp them. They live in the same public schema as the game tables but are managed entirely by the LangGraph package.

## Docker

`.dockerignore` excludes `.venv/`, `.env`, `supabase/`, `tests/`, `.git/`. Don't add files to these paths expecting them inside the image. The dev compose mounts `./app`, `./alembic`, and `./tests` as volumes, so edits to those dirs are hot-reloaded without rebuilding.

## Errors & learnings

Log non-obvious failures in `docs/PLAN.md` under "Errors & Learnings" with symptom, root cause, fix, file. Cross-reference here only if a pattern becomes a permanent convention.
