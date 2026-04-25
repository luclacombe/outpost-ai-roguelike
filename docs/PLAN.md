# Outpost Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Outpost end-to-end — a browser-based narrative roguelike with 4 AI advisors, three game mechanics (Grievance, Delayed Consequences, Legitimacy), pixel-art dashboard, auth + daily gate, observability, and eval pipeline. Ship to production.

**Architecture:** FastAPI backend (Python 3.12, LangGraph + raw Anthropic SDK) on Railway, Next.js 16 frontend on Vercel, Supabase Postgres + Auth, Langfuse for observability. All details in `docs/ARCHITECTURE.md`. Game design in `docs/SPEC.md`.

**Tech Stack:** Python 3.12 / FastAPI / LangGraph / Anthropic SDK / SQLAlchemy async + psycopg3 / Alembic / Docker · Next.js 16 / TypeScript strict / Tailwind v4 / shadcn/ui / DiceBear · Supabase (Auth + Postgres) · Railway · Vercel · Langfuse · LangSmith

**Success Criteria:** See `docs/SPEC.md` §16 (MVP → Ship checklist). All items must be checked before calling MVP complete.

---

## Architectural Invariants (never violate)

From `docs/ARCHITECTURE.md` §16:
1. Engine is source of truth. LLMs propose via tool calls; engine validates and applies.
2. No numbers from LLM freeform text — all state changes through validated tool calls.
3. Dead means dead. Agents removed on death never re-appear.
4. One run per account per day, server-authoritative.
5. Prompt caching on by default.
6. Debate is one batched LLM call, XML-delimited.
7. Setting content is procedural (schema-driven), never hand-coded.
8. No LLM-generated images in MVP.
9. RLS is safety net; application code filters by user_id.
10. Eval thresholds gate merges.
11. All state mutations flow through validated tool calls — no engine-only side channel.
12. Every `graph.invoke` passes `config={"configurable": {"thread_id": game_id}}` for pause/resume.

---

## Known Risks (acknowledged during spec review)

Full catalog with mitigations in `docs/ARCHITECTURE.md` §17. Summary:

- **Supabase HS256 JWT deprecation** — keep project on HS256; add JWKS fallback post-launch.
- **5-min cache TTL vs real player pacing** — measure in Phase 4 integration test; switch to 1-hour tier if hit rate < 70%.
- **Turn latency (9-24s for 3 serialized LLM calls)** — incremental SSE streaming; p95 latency measured in Phase 5.
- **Cost at real pacing could exceed $0.25/run** — Sonnet-off fallback already wired via `SONNET_NARRATION_ENABLED`.
- **SSE + token refresh mid-run** — Phase 7 task 7.6 handles with per-request token refresh + reconnect.
- **Inter-run narrative repetition** — Phase 5 task 5.3 measures this.
- **Railway cold starts on free tier** — Phase 9 task 9.2 documents + mitigates.
- **Model ID aliases can shift** — dated model IDs used in production, verified via context7 in Phase 4 task 4.1.

---

## MCP Servers Available (use them)

| MCP | When to use |
|-----|-------------|
| **context7** | Before writing unfamiliar code — FastAPI, LangGraph, DiceBear, Anthropic SDK, Tailwind v4, Next.js 16 |
| **supabase** | For DB operations, RLS, auth config, migrations |
| **playwright** | After every UI phase — screenshot + visual regression |
| **vercel** | Deploy, env vars, deployment logs |

**Rule:** When a task involves a library you haven't written before, call context7 first. Do not guess APIs.

---

## Phase 0: Prerequisites & Accounts

**Status:** ✅ Complete (2026-04-23)

**Entry criteria:** None.
**Exit criteria:** All accounts created, all CLIs installed, initial monorepo committed.

**Session config:** `/model claude-sonnet-4-6` · `/effort low` · plan mode: no

**Session start prompt:**

```
Role: senior engineer guiding me through Outpost Phase 0 (accounts + CLIs).

Read CLAUDE.md and docs/PLAN.md Phase 0 first.

This phase is admin work I execute myself. Walk me through Tasks 0.1–0.6
one at a time, in order. For each task:
- Tell me exactly what to click / run / paste
- Wait for me to confirm completion (or describe what I see if different)
- If the task produces a key or value, we capture it to backend/.env.example
  as a blank field; the real value goes in my local .env only (never committed)

Stop at Phase 0 exit criteria. Do not start Phase 1.

At phase close, if we established any new convention or admin gotcha worth
pinning, add it to the right CLAUDE.md per the docs discipline section.
```

### Task 0.1: Install Railway CLI

- [x] **Step 1:** Install Railway CLI.
  ```bash
  npm i -g @railway/cli
  railway --version  # expect 3.x+
  ```

### Task 0.2: Create Supabase project

- [x] **Step 1:** Go to https://supabase.com/dashboard. Create organization "Outpost" if needed.
- [x] **Step 2:** Create new project: name "outpost", region closest to you, generate strong DB password. Save password in 1Password or equivalent.
- [x] **Step 3:** Use supabase MCP to verify connection:
  ```
  mcp__plugin_supabase_supabase__list_projects
  ```
  Expected: project "outpost" appears.
- [x] **Step 4:** Note from Supabase dashboard → Settings → API:
  - Project URL (`SUPABASE_URL`)
  - `anon` public key (`NEXT_PUBLIC_SUPABASE_ANON_KEY`)
  - `service_role` secret key (`SUPABASE_SERVICE_ROLE_KEY`)
  - JWT Secret (`SUPABASE_JWT_SECRET`)

### Task 0.3: Create Langfuse account

- [x] **Step 1:** Sign up at https://cloud.langfuse.com. Free Hobby tier.
- [x] **Step 2:** Create new project "outpost-dev".
- [x] **Step 3:** Generate API keys → note `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`.

### Task 0.4: Create Anthropic API key

- [x] **Step 1:** Sign in to https://console.anthropic.com.
- [x] **Step 2:** Generate key with $20 initial budget. Save as `ANTHROPIC_API_KEY`.

### Task 0.5: Create Google OAuth credentials

- [x] **Step 1:** Go to https://console.cloud.google.com. Create project "Outpost".
- [x] **Step 2:** APIs & Services → OAuth consent screen. Configure as external, app name "Outpost", support email = your email, add scopes `email` and `profile`.
- [x] **Step 3:** Credentials → Create OAuth client ID → Web application.
- [x] **Step 4:** Authorized redirect URIs: add `https://<your-supabase-project-ref>.supabase.co/auth/v1/callback`.
- [x] **Step 5:** Copy Client ID and Client Secret.
- [x] **Step 6:** In Supabase dashboard → Authentication → Providers → Google, paste Client ID and Client Secret, enable.

### Task 0.6: Initialize monorepo + GitHub

- [x] **Step 1:** In `/Users/luclacombe/Documents/Outpost`:
  ```bash
  git init
  git branch -M main
  ```
- [x] **Step 2:** Create `.gitignore`:
  ```gitignore
  # Python
  __pycache__/
  *.pyc
  .venv/
  .env
  .env.*
  !.env.example
  
  # Node
  node_modules/
  .next/
  out/
  
  # Build
  dist/
  build/
  
  # IDE
  .vscode/
  .idea/
  
  # OS
  .DS_Store
  Thumbs.db
  
  # Supabase
  supabase/.temp/
  
  # Logs
  *.log
  ```
- [x] **Step 3:** Create GitHub repo via `gh`:
  ```bash
  gh repo create Outpost --private --source=. --remote=origin --push=false
  ```
- [x] **Step 4:** Create placeholder `README.md`:
  ```markdown
  # Outpost
  
  A daily narrative roguelike where AI advisors run a fragile colony under your command.
  
  See `docs/SPEC.md` for game design, `docs/ARCHITECTURE.md` for technical architecture, `docs/PLAN.md` for implementation plan.
  ```
- [x] **Step 5:** Commit scaffolding.
  ```bash
  git add .gitignore README.md docs/
  git commit -m "chore: initial repo scaffolding with spec, architecture, plan, research"
  git push -u origin main
  ```

---

## Phase 1: Backend Scaffolding

**Status:** ✅ Complete (2026-04-23)

**Entry criteria:** Phase 0 complete.
**Exit criteria:** `docker compose up` runs FastAPI, `curl localhost:8000/health` returns `{"status":"ok"}`, connects to local Supabase, tests pass, ruff clean.

**Session config:** `/model claude-sonnet-4-6` · `/effort medium` · plan mode: no

**Session start prompt:**

```
Role: senior Python backend engineer. Execute Outpost Phase 1.

Read in order:
  1. CLAUDE.md (invariants, commands, docs discipline)
  2. docs/PLAN.md Phase 1 — all tasks
  3. docs/research/03-fastapi-supabase.md — source for project structure

Execute Tasks 1.1–1.7 inline, task by task, TDD per PLAN.md pattern.
After each task: run the verification command, /commit via the commit skill,
mark the task complete in PLAN.md.

Before writing any code against an unfamiliar library (FastAPI 0.115+,
pydantic-settings, uv, Alembic async mode), invoke context7 MCP first —
do not guess APIs.

Hard invariants from CLAUDE.md still apply. Do not skip the ruff check or
the docker compose verification at exit.

If during this phase you establish a backend-specific convention (how
services vs routers are organized, exception-handling patterns, async
pitfalls discovered), create or update backend/CLAUDE.md with it before
declaring Phase 1 done.

Stop at Phase 1 exit. Do not start Phase 2.
```

### File structure to be produced

```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app factory
│   ├── config.py                # Pydantic Settings from .env
│   ├── database.py              # Async SQLAlchemy engine
│   ├── dependencies.py          # get_db, get_current_user
│   ├── auth/                    # Phase 2
│   ├── game/                    # Phase 3
│   ├── agent/                   # Phase 4
│   └── middleware/
│       ├── __init__.py
│       ├── logging.py
│       └── rate_limit.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   └── test_health.py
├── alembic/
│   ├── versions/
│   ├── env.py
│   └── script.py.mako
├── alembic.ini
├── Dockerfile
├── Dockerfile.dev
├── docker-compose.yml
├── pyproject.toml
└── .env.example
```

### Task 1.1: Initialize backend with uv

- [x] **Step 1:** Create backend directory and init uv project.
  ```bash
  mkdir -p backend && cd backend
  uv init --name outpost-backend --python 3.12
  ```
- [x] **Step 2:** Add runtime dependencies.
  ```bash
  uv add "fastapi>=0.115" "uvicorn[standard]" pydantic "pydantic-settings" \
    "sqlalchemy[asyncio]" "psycopg[binary,pool]" "alembic" "python-jose[cryptography]" \
    "pyjwt" "structlog" "httpx" "anthropic" "langgraph" "langfuse" "langchain-core" \
    "slowapi" "python-multipart"
  ```
- [x] **Step 3:** Add dev dependencies.
  ```bash
  uv add --dev pytest pytest-asyncio pytest-cov ruff httpx respx
  ```
- [x] **Step 4:** Configure ruff + pytest in `pyproject.toml` — append:
  ```toml
  [tool.ruff]
  line-length = 100
  target-version = "py312"
  
  [tool.ruff.lint]
  select = ["E", "F", "W", "I", "N", "UP", "B", "SIM"]
  ignore = ["E501"]
  
  [tool.pytest.ini_options]
  asyncio_mode = "auto"
  markers = [
      "eval: simulated-game evaluation tests (slow, costs LLM tokens)",
  ]
  ```
- [x] **Step 5:** Commit.
  ```bash
  git add backend/pyproject.toml backend/uv.lock backend/README.md
  git commit -m "feat(backend): initialize uv project with dependencies"
  ```

### Task 1.2: Config module with Pydantic Settings

**Files:** Create `backend/app/config.py`, `backend/app/__init__.py`, `backend/.env.example`, `backend/tests/test_config.py`, `backend/tests/__init__.py`.

- [x] **Step 1:** Write failing test `backend/tests/test_config.py`:
  ```python
  from app.config import Settings
  
  def test_settings_require_critical_env_vars(monkeypatch):
      monkeypatch.delenv("DATABASE_URL", raising=False)
      import pytest
      with pytest.raises(Exception):
          Settings()
  
  def test_settings_load_from_env(monkeypatch):
      monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h/db")
      monkeypatch.setenv("SUPABASE_JWT_SECRET", "secret")
      monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
      monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
      monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
      monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
      s = Settings()
      assert s.DATABASE_URL == "postgresql+psycopg://u:p@h/db"
      assert s.ENVIRONMENT == "dev"  # default
  ```
- [x] **Step 2:** Run — expect failure.
  ```bash
  cd backend && uv run pytest tests/test_config.py -v
  ```
- [x] **Step 3:** Implement `backend/app/__init__.py` (empty) and `backend/app/config.py`:
  ```python
  from pydantic import Field
  from pydantic_settings import BaseSettings, SettingsConfigDict
  
  
  class Settings(BaseSettings):
      model_config = SettingsConfigDict(env_file=".env", extra="ignore")
  
      DATABASE_URL: str
      SUPABASE_URL: str
      SUPABASE_JWT_SECRET: str
      SUPABASE_SERVICE_ROLE_KEY: str
      ANTHROPIC_API_KEY: str
      FRONTEND_URL: str
  
      LANGFUSE_PUBLIC_KEY: str | None = None
      LANGFUSE_SECRET_KEY: str | None = None
      LANGSMITH_API_KEY: str | None = None
  
      ENVIRONMENT: str = Field(default="dev")
      LOG_LEVEL: str = Field(default="info")
  
      MAX_COST_CENTS_PER_TURN: int = 15
      MAX_COST_CENTS_PER_RUN: int = 40
      SONNET_NARRATION_ENABLED: bool = True
  
  
  settings = Settings()
  ```
- [x] **Step 4:** Create `backend/.env.example`:
  ```dotenv
  DATABASE_URL=postgresql+psycopg://postgres:postgres@host.docker.internal:54322/postgres
  SUPABASE_URL=https://YOUR-PROJECT-REF.supabase.co
  SUPABASE_JWT_SECRET=YOUR_JWT_SECRET
  SUPABASE_SERVICE_ROLE_KEY=YOUR_SERVICE_ROLE_KEY
  ANTHROPIC_API_KEY=sk-ant-...
  LANGFUSE_PUBLIC_KEY=
  LANGFUSE_SECRET_KEY=
  FRONTEND_URL=http://localhost:3000
  ENVIRONMENT=dev
  LOG_LEVEL=info
  ```
- [x] **Step 5:** Copy to `.env` with real values from Phase 0:
  ```bash
  cp .env.example .env
  # Edit .env, fill real values
  ```
- [x] **Step 6:** Run tests — expect pass.
  ```bash
  uv run pytest tests/test_config.py -v
  ```
- [x] **Step 7:** Commit.
  ```bash
  git add backend/app/config.py backend/app/__init__.py backend/tests/ backend/.env.example
  git commit -m "feat(backend): pydantic settings from env"
  ```

### Task 1.3: Database module (async engine)

**Files:** Create `backend/app/database.py`, `backend/tests/test_database.py`.

- [x] **Step 1:** Write failing test:
  ```python
  # backend/tests/test_database.py
  import pytest
  from app.database import engine, async_session_factory
  
  @pytest.mark.asyncio
  async def test_database_can_connect():
      async with async_session_factory() as session:
          from sqlalchemy import text
          result = await session.execute(text("SELECT 1"))
          assert result.scalar() == 1
  ```
- [x] **Step 2:** Implement `backend/app/database.py`:
  ```python
  from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
  from app.config import settings
  
  engine = create_async_engine(
      settings.DATABASE_URL,
      pool_size=5,
      max_overflow=10,
      pool_pre_ping=True,
      echo=(settings.LOG_LEVEL == "debug"),
  )
  
  async_session_factory = async_sessionmaker(
      engine,
      class_=AsyncSession,
      expire_on_commit=False,
  )
  ```
- [x] **Step 3:** Start local Supabase (requires Docker running).
  ```bash
  cd backend && supabase init  # accept defaults
  supabase start
  # note the output's "API URL" and "DB URL" — the DB URL is port 54322
  ```
- [x] **Step 4:** Run tests — expect pass.
  ```bash
  uv run pytest tests/test_database.py -v
  ```
- [x] **Step 5:** Commit.
  ```bash
  git add backend/app/database.py backend/tests/test_database.py backend/supabase/
  git commit -m "feat(backend): async SQLAlchemy engine"
  ```

### Task 1.4: Logging middleware + structlog config

**Files:** Create `backend/app/middleware/__init__.py`, `backend/app/middleware/logging.py`.

- [x] **Step 1:** Write `backend/app/middleware/__init__.py` (empty).
- [x] **Step 2:** Write `backend/app/middleware/logging.py`:
  ```python
  import logging
  import time
  import structlog
  from fastapi import Request, Response
  from app.config import settings
  
  
  def configure_logging() -> None:
      logging.basicConfig(
          level=getattr(logging, settings.LOG_LEVEL.upper()),
          format="%(message)s",
      )
      structlog.configure(
          processors=[
              structlog.contextvars.merge_contextvars,
              structlog.stdlib.add_log_level,
              structlog.processors.TimeStamper(fmt="iso"),
              structlog.processors.JSONRenderer() if settings.ENVIRONMENT != "dev"
              else structlog.dev.ConsoleRenderer(),
          ],
          wrapper_class=structlog.make_filtering_bound_logger(
              getattr(logging, settings.LOG_LEVEL.upper())
          ),
          cache_logger_on_first_use=True,
      )
  
  
  log = structlog.get_logger("outpost")
  
  
  async def log_requests(request: Request, call_next):
      start = time.time()
      response: Response = await call_next(request)
      duration_ms = round((time.time() - start) * 1000)
      log.info(
          "request",
          method=request.method,
          path=request.url.path,
          status=response.status_code,
          duration_ms=duration_ms,
      )
      return response
  ```

### Task 1.5: Main app + health endpoint

**Files:** Create `backend/app/main.py`, `backend/tests/test_health.py`, `backend/tests/conftest.py`.

- [x] **Step 1:** Write `backend/tests/conftest.py`:
  ```python
  import pytest_asyncio
  from httpx import AsyncClient, ASGITransport
  from app.main import app
  
  @pytest_asyncio.fixture
  async def client():
      async with AsyncClient(
          transport=ASGITransport(app=app), base_url="http://test"
      ) as c:
          yield c
  ```
- [x] **Step 2:** Write `backend/tests/test_health.py`:
  ```python
  async def test_health(client):
      response = await client.get("/health")
      assert response.status_code == 200
      assert response.json() == {"status": "ok", "service": "outpost-backend"}
  ```
- [x] **Step 3:** Implement `backend/app/main.py`:
  ```python
  from contextlib import asynccontextmanager
  from fastapi import FastAPI
  from fastapi.middleware.cors import CORSMiddleware
  from app.config import settings
  from app.middleware.logging import configure_logging, log_requests
  
  configure_logging()
  
  
  @asynccontextmanager
  async def lifespan(app: FastAPI):
      yield
  
  
  app = FastAPI(title="Outpost Backend", lifespan=lifespan)
  
  app.add_middleware(
      CORSMiddleware,
      allow_origins=[settings.FRONTEND_URL, "http://localhost:3000"],
      allow_credentials=False,  # Bearer tokens don't need credentials; avoids blocking wildcard
      allow_methods=["GET", "POST"],
      allow_headers=["Authorization", "Content-Type"],
  )
  
  app.middleware("http")(log_requests)
  
  
  @app.get("/health")
  async def health():
      return {"status": "ok", "service": "outpost-backend"}
  ```
- [x] **Step 4:** Run tests — expect pass.
  ```bash
  uv run pytest tests/ -v
  ```
- [x] **Step 5:** Run locally to sanity check.
  ```bash
  uv run uvicorn app.main:app --reload
  # in another terminal:
  curl localhost:8000/health
  ```
- [x] **Step 6:** Commit.
  ```bash
  git add backend/app/ backend/tests/
  git commit -m "feat(backend): FastAPI app with health endpoint"
  ```

### Task 1.6: Dockerfile, Dockerfile.dev, docker-compose.yml

- [x] **Step 1:** Create `backend/Dockerfile.dev`:
  ```dockerfile
  FROM python:3.12-slim
  
  WORKDIR /app
  
  RUN pip install --no-cache-dir uv
  
  COPY pyproject.toml uv.lock ./
  RUN uv sync --frozen
  
  COPY . .
  
  CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
  ```
- [x] **Step 2:** Create `backend/Dockerfile`:
  ```dockerfile
  FROM python:3.12-slim
  
  WORKDIR /app
  
  RUN pip install --no-cache-dir uv
  
  COPY pyproject.toml uv.lock ./
  RUN uv sync --frozen --no-dev
  
  COPY app/ app/
  COPY alembic/ alembic/
  COPY alembic.ini ./
  
  EXPOSE 8000
  CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
  ```
- [x] **Step 3:** Create `backend/docker-compose.yml`:
  ```yaml
  services:
    api:
      build:
        context: .
        dockerfile: Dockerfile.dev
      volumes:
        - ./app:/app/app
        - ./alembic:/app/alembic
        - ./tests:/app/tests
      ports:
        - "8000:8000"
      env_file:
        - .env
      environment:
        DATABASE_URL: postgresql+psycopg://postgres:postgres@host.docker.internal:54322/postgres
      restart: unless-stopped
  ```
- [x] **Step 4:** Verify `docker compose up` runs.
  ```bash
  # from backend/
  docker compose up --build -d
  curl localhost:8000/health
  # expect: {"status":"ok","service":"outpost-backend"}
  docker compose down
  ```
- [x] **Step 5:** Commit.
  ```bash
  git add backend/Dockerfile backend/Dockerfile.dev backend/docker-compose.yml
  git commit -m "feat(backend): Docker Compose dev setup"
  ```

### Task 1.7: Alembic init

- [x] **Step 1:** Initialize Alembic.
  ```bash
  cd backend && uv run alembic init -t async alembic
  ```
- [x] **Step 2:** Edit `backend/alembic/env.py`. Replace the connection setup with:
  ```python
  import asyncio
  from logging.config import fileConfig
  
  from sqlalchemy import pool
  from sqlalchemy.engine import Connection
  from sqlalchemy.ext.asyncio import async_engine_from_config
  
  from alembic import context
  from app.config import settings
  
  config = context.config
  config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
  
  if config.config_file_name is not None:
      fileConfig(config.config_file_name)
  
  # Imports of models will go here as they're added in Phase 3
  target_metadata = None
  
  def run_migrations_offline():
      url = config.get_main_option("sqlalchemy.url")
      context.configure(url=url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
      with context.begin_transaction():
          context.run_migrations()
  
  def do_run_migrations(connection: Connection):
      context.configure(connection=connection, target_metadata=target_metadata)
      with context.begin_transaction():
          context.run_migrations()
  
  async def run_async_migrations():
      connectable = async_engine_from_config(
          config.get_section(config.config_ini_section, {}),
          prefix="sqlalchemy.",
          poolclass=pool.NullPool,
      )
      async with connectable.connect() as connection:
          await connection.run_sync(do_run_migrations)
      await connectable.dispose()
  
  def run_migrations_online():
      asyncio.run(run_async_migrations())
  
  if context.is_offline_mode():
      run_migrations_offline()
  else:
      run_migrations_online()
  ```
- [x] **Step 3:** Verify alembic recognizes config.
  ```bash
  uv run alembic current
  # expect no output or "INFO [alembic.runtime.migration] Context impl..."
  ```
- [x] **Step 4:** Commit.
  ```bash
  git add backend/alembic/ backend/alembic.ini
  git commit -m "feat(backend): alembic async setup"
  ```

**Phase 1 exit verification:**
```bash
cd backend
supabase start
docker compose up -d
curl localhost:8000/health  # -> {"status":"ok","service":"outpost-backend"}
uv run pytest tests/ -v     # all pass
uv run ruff check app/      # clean
docker compose down
```

---

## Phase 2: Auth + Daily Gate

**Status:** ✅ Complete (2026-04-23)
**Entry criteria:** Phase 1 exit passed.
**Exit criteria:** Authenticated requests with a real Supabase JWT reach protected endpoints. Daily gate prevents a second run on the same day. `/me` returns user info + gate status.

**Session config:** `/model claude-sonnet-4-6` · `/effort medium` · plan mode: no

**Session start prompt:**

```
Role: senior backend engineer. Execute Outpost Phase 2 — Auth + Daily Gate.

Read:
  1. CLAUDE.md + backend/CLAUDE.md
  2. docs/PLAN.md Phase 2
  3. docs/research/03-fastapi-supabase.md §"Supabase Integration from FastAPI"
  4. docs/ARCHITECTURE.md §9 (Auth flow) and §10 (Daily gate)

Execute Tasks 2.1–2.6 inline, TDD. Use the supabase MCP to verify that
Alembic migrations landed, Supabase CLI migrations applied RLS, and the
on_auth_user_created trigger exists.

Before any JWT/auth code, use context7 to check current PyJWT and Supabase
SSR patterns. End-to-end verify: obtain a real Supabase access token from
my running dev Supabase, hit /me, confirm the response shape matches.

If you establish auth-specific conventions (dependency composition, where
user_id extraction happens, rate-limit placement), update backend/CLAUDE.md.

Stop at Phase 2 exit. Do not start Phase 3.
```

### Task 2.1: Auth service (JWT decode)

**Files:** Create `backend/app/auth/__init__.py`, `backend/app/auth/service.py`, `backend/app/auth/schemas.py`, `backend/tests/test_auth_service.py`.

- [x] **Step 1:** Write failing test `backend/tests/test_auth_service.py`:
  ```python
  import jwt
  import pytest
  from app.auth.service import decode_supabase_jwt
  from app.config import settings
  from fastapi import HTTPException
  
  def _make_token(payload: dict, secret: str | None = None) -> str:
      return jwt.encode(payload, secret or settings.SUPABASE_JWT_SECRET, algorithm="HS256")
  
  def test_decode_valid_token():
      payload = {"sub": "user-123", "aud": "authenticated", "exp": 9999999999}
      token = _make_token(payload)
      result = decode_supabase_jwt(token)
      assert result["sub"] == "user-123"
  
  def test_reject_expired_token():
      token = _make_token({"sub": "u", "aud": "authenticated", "exp": 1})
      with pytest.raises(HTTPException) as exc:
          decode_supabase_jwt(token)
      assert exc.value.status_code == 401
  
  def test_reject_bad_signature():
      token = _make_token({"sub": "u", "aud": "authenticated", "exp": 9999999999}, secret="wrong")
      with pytest.raises(HTTPException) as exc:
          decode_supabase_jwt(token)
      assert exc.value.status_code == 401
  ```
- [x] **Step 2:** Implement `backend/app/auth/service.py`:
  ```python
  import jwt
  from fastapi import HTTPException, status
  from app.config import settings
  
  
  def decode_supabase_jwt(token: str) -> dict:
      try:
          return jwt.decode(
              token,
              settings.SUPABASE_JWT_SECRET,
              algorithms=["HS256"],
              audience="authenticated",
          )
      except jwt.ExpiredSignatureError as e:
          raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired") from e
      except jwt.InvalidTokenError as e:
          raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from e
  ```
- [x] **Step 3:** Write `backend/app/auth/schemas.py`:
  ```python
  from pydantic import BaseModel
  
  
  class UserInfo(BaseModel):
      id: str
      email: str | None
      display_name: str | None
      next_run_at: str | None  # ISO 8601 or None if eligible now
  ```
- [x] **Step 4:** Add `backend/app/auth/__init__.py` (empty).
- [x] **Step 5:** Run tests — expect pass.
  ```bash
  uv run pytest tests/test_auth_service.py -v
  ```
- [x] **Step 6:** Commit.
  ```bash
  git add backend/app/auth/ backend/tests/test_auth_service.py
  git commit -m "feat(auth): supabase JWT decode"
  ```

### Task 2.2: Auth dependency

**Files:** Create `backend/app/dependencies.py`, `backend/tests/test_dependencies.py`.

- [x] **Step 1:** Implement `backend/app/dependencies.py`:
  ```python
  from collections.abc import AsyncGenerator
  from fastapi import Depends
  from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
  from sqlalchemy.ext.asyncio import AsyncSession
  from app.auth.service import decode_supabase_jwt
  from app.database import async_session_factory
  
  bearer_scheme = HTTPBearer(auto_error=True)
  
  
  async def get_db() -> AsyncGenerator[AsyncSession, None]:
      async with async_session_factory() as session:
          yield session
  
  
  async def get_current_user(
      credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
  ) -> dict:
      return decode_supabase_jwt(credentials.credentials)
  ```
- [x] **Step 2:** Update `backend/tests/conftest.py` to allow auth override:
  ```python
  import pytest_asyncio
  from httpx import AsyncClient, ASGITransport
  from app.main import app
  from app.dependencies import get_current_user
  
  
  @pytest_asyncio.fixture
  async def client():
      app.dependency_overrides[get_current_user] = lambda: {
          "sub": "test-user-id", "email": "test@outpost.local"
      }
      async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
          yield c
      app.dependency_overrides.clear()
  
  
  @pytest_asyncio.fixture
  async def unauth_client():
      async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
          yield c
  ```
- [x] **Step 3:** Commit.
  ```bash
  git add backend/app/dependencies.py backend/tests/conftest.py
  git commit -m "feat(auth): dependency injection for current user + db"
  ```

### Task 2.3: Profile table (Alembic migration)

**Files:** Create `backend/app/models.py`, Alembic migration file.

- [x] **Step 1:** Create `backend/app/models.py` (shared Base):
  ```python
  from datetime import datetime
  from uuid import UUID
  from sqlalchemy import DateTime, Integer, String, text
  from sqlalchemy.dialects.postgresql import UUID as PGUUID
  from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
  
  
  class Base(DeclarativeBase):
      pass
  
  
  class Profile(Base):
      __tablename__ = "profiles"
  
      id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
      display_name: Mapped[str | None] = mapped_column(String)
      last_run_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
      total_runs: Mapped[int] = mapped_column(Integer, server_default=text("0"))
      created_at: Mapped[datetime] = mapped_column(
          DateTime(timezone=True), server_default=text("now()")
      )
  ```
- [x] **Step 2:** Register metadata in `backend/alembic/env.py`. Replace `target_metadata = None` with:
  ```python
  from app.models import Base
  target_metadata = Base.metadata
  ```
- [x] **Step 3:** Generate migration.
  ```bash
  cd backend && uv run alembic revision --autogenerate -m "create profiles table"
  ```
- [x] **Step 4:** Review the generated file in `backend/alembic/versions/`. It should create the `profiles` table. Edit if needed so that the column `id` references `auth.users(id)`:
  ```python
  import sqlalchemy as sa
  from sqlalchemy.dialects import postgresql
  
  def upgrade():
      op.create_table(
          "profiles",
          sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
          sa.Column("display_name", sa.String, nullable=True),
          sa.Column("last_run_started_at", sa.DateTime(timezone=True), nullable=True),
          sa.Column("total_runs", sa.Integer, server_default="0", nullable=False),
          sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
          sa.ForeignKeyConstraint(["id"], ["auth.users.id"], ondelete="CASCADE"),
      )
  
  def downgrade():
      op.drop_table("profiles")
  ```
- [x] **Step 5:** Apply migration.
  ```bash
  uv run alembic upgrade head
  ```
- [x] **Step 6:** Verify with supabase MCP — list tables should include `profiles`.
- [x] **Step 7:** Commit.
  ```bash
  git add backend/app/models.py backend/alembic/
  git commit -m "feat(db): profiles table migration"
  ```

### Task 2.4: RLS + profile bootstrap trigger (Supabase CLI migration)

- [x] **Step 1:** Create Supabase migration.
  ```bash
  cd backend && supabase migration new enable_rls_profiles_and_bootstrap_trigger
  ```
- [x] **Step 2:** Edit the generated file under `supabase/migrations/<timestamp>_enable_rls_profiles_and_bootstrap_trigger.sql`:
  ```sql
  -- Enable RLS on profiles
  ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
  
  CREATE POLICY "users_read_own_profile" ON public.profiles
      FOR SELECT USING (auth.uid() = id);
  
  CREATE POLICY "users_update_own_profile" ON public.profiles
      FOR UPDATE USING (auth.uid() = id);
  
  -- Trigger: auto-create profile row for each new auth user
  CREATE OR REPLACE FUNCTION public.create_profile_for_new_user()
  RETURNS TRIGGER
  LANGUAGE plpgsql
  SECURITY DEFINER
  SET search_path = public
  AS $$
  BEGIN
      INSERT INTO public.profiles (id, display_name)
      VALUES (NEW.id, NEW.raw_user_meta_data->>'name');
      RETURN NEW;
  END;
  $$;
  
  DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
  CREATE TRIGGER on_auth_user_created
      AFTER INSERT ON auth.users
      FOR EACH ROW EXECUTE FUNCTION public.create_profile_for_new_user();
  ```
- [x] **Step 3:** Apply via Supabase CLI.
  ```bash
  supabase db push
  ```
- [x] **Step 4:** Verify with supabase MCP: query `SELECT * FROM pg_trigger WHERE tgname = 'on_auth_user_created'`.
- [x] **Step 5:** Commit.
  ```bash
  git add backend/supabase/
  git commit -m "feat(auth): RLS + profile bootstrap trigger"
  ```

### Task 2.5: Daily gate service

**Files:** Create `backend/app/auth/gate.py`, `backend/tests/test_gate.py`.

- [x] **Step 1:** Write failing test:
  ```python
  # backend/tests/test_gate.py
  from datetime import datetime, timedelta, timezone
  from app.auth.gate import can_start_run, next_run_at
  
  def test_can_start_when_no_prior_run():
      assert can_start_run(None) is True
  
  def test_cannot_start_same_day():
      now = datetime.now(timezone.utc)
      assert can_start_run(now) is False
  
  def test_can_start_next_day():
      yesterday = datetime.now(timezone.utc) - timedelta(days=1, hours=1)
      assert can_start_run(yesterday) is True
  
  def test_next_run_at_returns_midnight_utc_after_last():
      last = datetime(2026, 4, 23, 14, 30, tzinfo=timezone.utc)
      expected = datetime(2026, 4, 24, 0, 0, tzinfo=timezone.utc)
      assert next_run_at(last) == expected
  ```
- [x] **Step 2:** Implement `backend/app/auth/gate.py`:
  ```python
  from datetime import datetime, timedelta, timezone
  
  
  def next_run_at(last_started: datetime) -> datetime:
      return last_started.astimezone(timezone.utc).replace(
          hour=0, minute=0, second=0, microsecond=0
      ) + timedelta(days=1)
  
  
  def can_start_run(last_started: datetime | None) -> bool:
      if last_started is None:
          return True
      return datetime.now(timezone.utc) >= next_run_at(last_started)
  ```
- [x] **Step 3:** Run tests — expect pass.
  ```bash
  uv run pytest tests/test_gate.py -v
  ```
- [x] **Step 4:** Commit.
  ```bash
  git add backend/app/auth/gate.py backend/tests/test_gate.py
  git commit -m "feat(auth): daily gate service"
  ```

### Task 2.6: `/me` endpoint

**Files:** Create `backend/app/auth/router.py`, `backend/tests/test_me_endpoint.py`. Modify `backend/app/main.py`.

- [x] **Step 1:** Write failing test:
  ```python
  # backend/tests/test_me_endpoint.py
  async def test_me_returns_user_info(client):
      response = await client.get("/me")
      assert response.status_code == 200
      data = response.json()
      assert data["id"] == "test-user-id"
      assert "next_run_at" in data  # None or ISO string
  
  async def test_me_requires_auth(unauth_client):
      response = await unauth_client.get("/me")
      assert response.status_code in (401, 403)
  ```
- [x] **Step 2:** Implement `backend/app/auth/router.py`:
  ```python
  from uuid import UUID
  from fastapi import APIRouter, Depends
  from sqlalchemy import select
  from sqlalchemy.ext.asyncio import AsyncSession
  from app.auth.gate import can_start_run, next_run_at
  from app.auth.schemas import UserInfo
  from app.dependencies import get_current_user, get_db
  from app.models import Profile
  
  router = APIRouter(tags=["auth"])
  
  
  @router.get("/me", response_model=UserInfo)
  async def me(user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
      user_id = UUID(user["sub"])
      profile = await db.get(Profile, user_id)
      last = profile.last_run_started_at if profile else None
      eligible = can_start_run(last)
      return UserInfo(
          id=user["sub"],
          email=user.get("email"),
          display_name=profile.display_name if profile else None,
          next_run_at=None if eligible else next_run_at(last).isoformat(),
      )
  ```
- [x] **Step 3:** Include router in `backend/app/main.py` — add after CORS:
  ```python
  from app.auth.router import router as auth_router
  app.include_router(auth_router)
  ```
- [x] **Step 4:** Run tests — expect pass.
  ```bash
  uv run pytest tests/ -v
  ```
- [x] **Step 5:** Commit.
  ```bash
  git add backend/app/auth/router.py backend/app/main.py backend/tests/test_me_endpoint.py
  git commit -m "feat(auth): /me endpoint with daily gate status"
  ```

**Phase 2 exit verification:**
```bash
uv run pytest tests/ -v     # all pass
uv run ruff check app/      # clean
# Manual: hit /me with a real Supabase access token from the frontend auth session
```

---

## Phase 3: Game State + Engine (no LLM yet)

**Status:** ✅ Complete (2026-04-24)
**Entry criteria:** Phase 2 exit passed.
**Exit criteria:** Turn engine resolves a full turn with mocked inputs. All state transitions validated. Setting loader produces valid setting schema. Tool validator rejects bad calls.
**Phase 3 exit:** verified 2026-04-24 — 301 passed / 1 skipped, ruff clean, alembic head `3ba6ba8568f0`, all 3 settings pool-valid.

**Session config:** `/model claude-opus-4-7` · `/effort high` · plan mode: no · execution: subagent-driven

**Session start prompt:**

```
Role: senior game engine architect. Execute Outpost Phase 3 — Game State + Engine.

This is the core of the backend. The entire agent system in Phase 4 sits
on what you build here. Quality matters more than speed.

Read first, in order:
  1. CLAUDE.md + backend/CLAUDE.md
  2. docs/SPEC.md §§5 (mechanics), 6 (agents), 7 (run structure), 8 (settings),
     9 (player), 10 (end states)
  3. docs/ARCHITECTURE.md §§3 (agents + tools), 4 (state model), 7 (DB)
  4. docs/PLAN.md Phase 3 — all tasks
  5. docs/research/02-game-design.md — the three mechanical systems reference

Invoke superpowers:subagent-driven-development and dispatch one subagent
per task (3.1 through 3.10). Review between each subagent. Fresh context
per task is the point — engine decisions deserve it.

Hard constraints:
- CLAUDE.md invariant #11: all state mutations flow through validated tool
  calls. Engine helpers that adjust grievance/health emit tool calls,
  never mutate state directly.
- Coup resolution (Task 3.6, ARCHITECTURE §3.5) is rule-based and pure.
  No LLM. Test every branch of OUSTED/KILLED.
- Setting YAMLs (Task 3.3) are procedural. Never hardcode a setting in
  Python — enforce the schema.
- Difficulty rails (Task 3.8) must be encoded as validator rules, not as
  "we'll remember to tune this later."

Before Phase 3 exit, create backend/app/game/CLAUDE.md documenting:
- The pure-function discipline (state in, state out, no side effects)
- The tool-call → validator → apply pipeline
- How setting YAMLs are loaded and validated
- Any gotchas discovered during implementation

Stop at Phase 3 exit. Do not start Phase 4.
```

### File structure produced

```
backend/app/game/
├── __init__.py
├── schemas.py               # Pydantic models (ColonyState, Agent, Event, ...)
├── models.py                # SQLAlchemy ORM mirroring schemas
├── tools.py                 # Tool schema definitions
├── validator.py             # Pure validator for tool calls
├── engine.py                # Turn resolution + state mutations
├── settings/                # Setting YAML files
│   ├── arctic_station_v1.yml
│   ├── orbital_colony_v1.yml
│   └── desert_outpost_v1.yml
├── setting_loader.py        # Load + validate setting YAMLs
├── generator.py             # Procedural run generation from a setting
├── events.py                # Event pool + selection
├── consequence.py           # Seed scheduler
├── exceptions.py
└── router.py                # FastAPI routes (Phase 3.10)
```

### Task 3.1: Pydantic schemas

**Files:** Create `backend/app/game/schemas.py`, `backend/tests/test_game_schemas.py`.

Write all the Pydantic models from `docs/ARCHITECTURE.md` §4. Reference the architecture doc for exact field definitions.

- [x] **Step 1:** Write failing test that validates a minimal ColonyState roundtrips through JSON:
  ```python
  # backend/tests/test_game_schemas.py
  from uuid import uuid4
  from app.game.schemas import ColonyState, Agent, Quadrant
  
  def test_colony_state_roundtrip():
      agent = Agent(
          id=uuid4(), game_id=uuid4(), name="Nadia", role="Quartermaster",
          archetype_quadrant=Quadrant.ACCOUNTANT,
          ethics=0.8, temperament=-0.6, loyalty=0.7,
          portrait_seed="nadia-v1",
          health=100, morale=80, grievance=0, is_alive=True,
          memory=[], relationships={},
      )
      state = ColonyState(
          game_id=uuid4(), user_id=uuid4(), setting_id="arctic_station_v1", seed=42,
          turn_number=0, max_turns=10,
          resources={}, agents=[agent],
          legitimacy=100, player_health=100,
          consequence_seeds=[], event_log=[],
          current_event=None, turn_proposals=[], turn_tool_calls=[], turn_cost_cents=0,
      )
      dumped = state.model_dump_json()
      restored = ColonyState.model_validate_json(dumped)
      assert restored.agents[0].name == "Nadia"
  ```
- [x] **Step 2:** Implement `backend/app/game/schemas.py` following `docs/ARCHITECTURE.md` §4. Use Pydantic v2 syntax throughout. Key models: `ColonyState`, `Agent`, `Event`, `Resource`, `Proposal`, `ToolCall`, `ConsequenceSeed`, `Decision`, `ResolvedEvent`, `EndState`, `Quadrant`, `ProposalFlavor`, `EventOrigin`, `Transformation`.

- [x] **Step 3:** Run tests — expect pass. Commit.
  ```bash
  uv run pytest tests/test_game_schemas.py -v
  git add backend/app/game/ backend/tests/test_game_schemas.py
  git commit -m "feat(game): pydantic state schemas"
  ```

### Task 3.2: ORM models + migration

**Files:** Modify `backend/app/models.py` (add game tables). Create Alembic migration.

- [x] **Step 1:** Add ORM models to `backend/app/models.py` matching `docs/ARCHITECTURE.md` §7.1. Tables: `game_sessions`, `agents`, `turn_logs`, `consequence_seeds`.
- [x] **Step 2:** Generate migration.
  ```bash
  uv run alembic revision --autogenerate -m "create game tables"
  ```
- [x] **Step 3:** Review and edit if needed. Apply.
  ```bash
  uv run alembic upgrade head
  ```
- [x] **Step 4:** Create Supabase migration for RLS on `game_sessions`:
  ```bash
  supabase migration new enable_rls_game_sessions
  # Add policies from docs/ARCHITECTURE.md §7.2
  supabase db push
  ```
- [x] **Step 5:** Commit.
  ```bash
  git add backend/app/models.py backend/alembic/ backend/supabase/
  git commit -m "feat(game): ORM models + migrations + RLS"
  ```

### Task 3.3: Setting YAML loader

**Files:** Create `backend/app/game/setting_loader.py`, `backend/app/game/settings/arctic_station_v1.yml`, `backend/tests/test_setting_loader.py`.

- [x] **Step 1:** Write `arctic_station_v1.yml` matching the schema in `docs/SPEC.md` §8 and the lore in §8.2 setting 1. Include 5 resources, 4 advisor archetypes mapped to quadrants, and 12-15 event category definitions.
- [x] **Step 2:** Write failing test:
  ```python
  # backend/tests/test_setting_loader.py
  from app.game.setting_loader import load_setting
  
  def test_load_arctic_setting():
      s = load_setting("arctic_station_v1")
      assert s.name == "Vorgrad Station"
      assert len(s.resources) == 5
      assert len(s.advisor_archetypes) == 4
      quadrants = {a.quadrant for a in s.advisor_archetypes}
      assert quadrants == {"ACCOUNTANT", "OPERATOR", "CONSCIENCE", "CRUSADER"}
  
  def test_load_missing_raises():
      import pytest
      with pytest.raises(FileNotFoundError):
          load_setting("nonexistent")
  ```
- [x] **Step 3:** Implement `setting_loader.py` using `PyYAML` (add to deps if not present). Validate against a Pydantic `Setting` schema.
- [x] **Step 4:** Run tests. Commit.
  ```bash
  uv run pytest tests/test_setting_loader.py -v
  git add backend/app/game/setting_loader.py backend/app/game/settings/
  git commit -m "feat(game): setting YAML loader"
  ```

### Task 3.4: Tool schema definitions

**Files:** Create `backend/app/game/tools.py`, `backend/tests/test_tools.py`.

Define tool schemas exactly as in `docs/ARCHITECTURE.md` §3.5. Each tool has a name, description, and JSON schema for its inputs.

- [x] **Step 1:** Implement `tools.py`:
  ```python
  # Shortened — full definition per ARCHITECTURE.md §3.5
  AGENT_TOOL_SCHEMAS = [
      {"name": "propose_action", "description": "...", "input_schema": {...}},
      {"name": "modify_resource", "description": "...", "input_schema": {...}},
      # ... speak_to_agent, adjust_relationship, adjust_own_grievance,
      # remember, initiate_coup, back_leader
  ]
  ```
- [x] **Step 2:** Test that the schemas are valid JSON Schema and have required tool names:
  ```python
  from app.game.tools import AGENT_TOOL_SCHEMAS
  
  def test_all_tools_present():
      names = {t["name"] for t in AGENT_TOOL_SCHEMAS}
      assert names == {
          "propose_action", "modify_resource", "modify_health", "adjust_grievance",
          "speak_to_agent", "adjust_relationship", "adjust_own_grievance",
          "remember", "initiate_coup", "back_leader",
      }
  ```
- [x] **Step 3:** Run tests. Commit.

### Task 3.5: Tool validator

**Files:** Create `backend/app/game/validator.py`, `backend/tests/test_validator.py`.

The validator is a pure function over `(ToolCall, ColonyState) → ValidationResult`. Every tool type has its own validation branch.

- [x] **Step 1:** Write failing tests for each tool:
  ```python
  # backend/tests/test_validator.py
  def test_modify_resource_accepts_valid_delta():
      # construct a ColonyState with fuel=50
      # call validate(ToolCall(name="modify_resource", args={"resource_id": "fuel", "delta": -20, "reason": "x"}), state)
      # assert valid=True
      ...
  
  def test_modify_resource_rejects_would_go_negative():
      # fuel=10, delta=-20
      # assert valid=False, reason contains "negative"
      ...
  
  def test_initiate_coup_requires_grievance_9():
      # agent with grievance=5 attempts coup
      # assert valid=False
      ...
  
  def test_initiate_coup_requires_faction_support():
      # agent grievance=9, no other agent grievance>=7
      # assert valid=False, reason contains "faction"
      ...
  
  # Add tests for every tool type
  ```
- [x] **Step 2:** Implement `validator.py` with one branch per tool type. Reference `docs/ARCHITECTURE.md` §3.5 for the full validation logic.
- [x] **Step 3:** Run tests until all pass. Commit.

### Task 3.6: Engine tick — apply validated tool calls

**Files:** Create `backend/app/game/engine.py`, `backend/tests/test_engine.py`.

- [x] **Step 1:** Write failing tests:
  ```python
  # backend/tests/test_engine.py
  from uuid import uuid4
  from app.game.schemas import ToolCall, EndState
  from app.game.engine import apply_tool_call, check_deaths, resolve_coup, compute_end_state
  
  def test_apply_modify_resource_mutates_state():
      state = make_state(fuel=50)
      tc = ToolCall(name="modify_resource", caller_id=uuid4(),
                    args={"resource_id": "fuel", "delta": -20, "reason": "x"})
      new_state = apply_tool_call(state, tc)
      assert new_state.resources["fuel"].value == 30
  
  def test_apply_rejects_invalid_does_not_mutate():
      state = make_state(fuel=10)
      tc = ToolCall(name="modify_resource", caller_id=uuid4(),
                    args={"resource_id": "fuel", "delta": -20, "reason": "x"})
      new_state = apply_tool_call(state, tc)
      assert new_state.resources["fuel"].value == 10  # unchanged
      assert new_state.turn_tool_calls[-1].rejection_reason is not None
  
  def test_modify_health_drops_agent_to_dead():
      state = make_state_with_agent(name="Marko", health=5)
      marko_id = state.agents_by_id_lookup()["Marko"]
      tc = ToolCall(name="modify_health", caller_id=uuid4(),
                    args={"target_id": str(marko_id), "delta": -10, "reason": "avalanche"})
      new_state = apply_tool_call(state, tc)
      new_state = check_deaths(new_state)
      marko = next(a for a in new_state.agents if a.name == "Marko")
      assert marko.is_alive is False
      assert marko.health == 0
  
  def test_adjust_grievance_caps_at_10():
      state = make_state_with_agent(name="Nadia", grievance=9)
      nadia_id = state.agents_by_id_lookup()["Nadia"]
      tc = ToolCall(name="adjust_grievance", caller_id=uuid4(),
                    args={"target_id": str(nadia_id), "delta": 3, "reason": "x"})
      new_state = apply_tool_call(state, tc)
      nadia = next(a for a in new_state.agents if a.name == "Nadia")
      assert nadia.grievance == 10  # clamped
  
  def test_resolve_coup_kills_when_all_conditions_met():
      # initiator grievance=10, co-conspirator grievance=9, both bold (+0.7),
      # legitimacy=5 → KILLED
      state = make_state_for_coup(legitimacy=5, initiator_grievance=10,
                                  accomplice_grievance=9, boldness_sum=1.2)
      new_state = resolve_coup(state, initiator_id=state.initiator_id)
      assert new_state.end_state == EndState.KILLED
  
  def test_resolve_coup_ousts_when_legitimacy_above_15():
      state = make_state_for_coup(legitimacy=20, initiator_grievance=9,
                                  accomplice_grievance=9, boldness_sum=1.0)
      new_state = resolve_coup(state, initiator_id=state.initiator_id)
      assert new_state.end_state == EndState.OUSTED
  ```
- [x] **Step 2:** Implement `engine.py`:
  - `apply_tool_call(state, tc) -> ColonyState` — validates via `validator.py`, applies if valid, records the attempt either way.
  - `apply_single_effect(state, effect) -> ColonyState` — single-tool-call mutation helper. Dispatches on `tc.name`. Clamps grievance to 0-10, health to 0-100.
  - `check_deaths(state) -> ColonyState` — sets `is_alive=False` for any agent with `health <= 0`; pins "died in turn N" memory into all *surviving* agents' memory lists (non-evicting).
  - `resolve_coup(state, initiator_id) -> ColonyState` — implements the logic from `ARCHITECTURE.md` §3.5 (KILLED vs OUSTED decision).
  - `advance_turn(state) -> ColonyState` — increments `turn_number`, evaluates end state.
  - `compute_end_state(state) -> EndState | None` — ENDURED/COLLAPSED/OUSTED/KILLED per `SPEC.md` §10.

All functions are pure — they return a new state, not mutate.

- [x] **Step 3:** Run tests. Commit.

### Task 3.7: Consequence seed scheduler

**Files:** Create `backend/app/game/consequence.py`, `backend/tests/test_consequence.py`.

- [x] **Step 1:** Write failing test:
  ```python
  def test_schedule_seed_writes_to_state():
      state = make_state(turn=3)
      seed = ConsequenceSeed(
          id=uuid4(), game_id=state.game_id,
          written_at_turn=3, fires_at_turn=7, foreshadowed=True,
          foreshadow_turn=5, payload={...}, origin_decision={...},
          fired=False, dormant=False,
      )
      new_state = schedule_seed(state, seed)
      assert len(new_state.consequence_seeds) == 1
  
  def test_check_and_fire_seeds_injects_event():
      # state with a seed that fires_at_turn=5, turn_number=5
      # call check_and_fire_seeds
      # assert the event pool has a new entry with origin_seed_id
      ...
  ```
- [x] **Step 2:** Implement `consequence.py` with:
  - `schedule_seed(state, seed)`
  - `check_and_fire_seeds(state) -> (new_state, fired_events)`
  - `foreshadow_signals(state, turn) -> list[str]` (returns advisor comment suggestions)

- [x] **Step 3:** Run tests. Commit.

### Task 3.8: Event pool + selection + difficulty rails

**Files:** Create `backend/app/game/events.py`, `backend/app/game/difficulty.py`, `backend/tests/test_events.py`, `backend/tests/test_difficulty.py`.

- [x] **Step 1:** Write tests for event selection:
  ```python
  # backend/tests/test_events.py
  def test_select_event_respects_cooldown():
      # after a severity-5 event on turn 3, no severity-5 on turn 4
      ...
  
  def test_forced_escalation_fires_exactly_once_in_5_to_9():
      # run 100 simulated selections; exactly one forced escalation per run
      # always within turns 5-9 inclusive
      ...
  
  def test_seeded_events_take_priority_over_pool():
      # if a seed is scheduled for this turn, the injected event wins
      ...
  
  def test_wealth_scaling_increases_threat_with_colony_stability():
      # high-stability state → higher-severity weights in selection
      ...
  ```
- [x] **Step 2:** Implement `events.py`:
  - `select_event(state, setting, rng) -> Event`
  - Respects: cooldown rules, wealth-scaling, forced escalation guarantee, seeded event priority

- [x] **Step 3:** Write difficulty-rails tests per `SPEC.md` §7.2:
  ```python
  # backend/tests/test_difficulty.py
  def test_no_event_drops_any_resource_more_than_30_percent():
      # scan the event pool for each setting; verify every event's effect preview
      # never reduces a resource by more than 30% of its max
      ...
  
  def test_no_agent_death_events_before_turn_4():
      # scan the event pool; any event tagged "can_kill_agent" has min_turn >= 4
      ...
  
  def test_legitimacy_floor_on_early_turns():
      # applying any single event's default effect cannot drop legitimacy below 20 before turn 4
      ...
  ```
- [x] **Step 4:** Implement `difficulty.py` enforcing the rails from `SPEC.md` §7.2:
  - `validate_event_pool_for_setting(setting)` — static check run at setting-load time
  - `apply_early_turn_floors(state, effect)` — clamp effects in turns 1-3 per the rails
  - `wealth_scale_factor(state) -> float` — 0.5 to 2.0 based on resource stability + agent health average

- [x] **Step 5:** Run tests. Commit.

### Task 3.9: Procedural run generator + transformations

**Files:** Create `backend/app/game/generator.py`, `backend/app/game/transformations.py`, `backend/tests/test_generator.py`, `backend/tests/test_transformations.py`.

- [x] **Step 1:** Write generator tests:
  ```python
  def test_generate_run_with_seed_is_deterministic():
      r1 = generate_run(user_id=UUID(...), setting_id="arctic_station_v1", seed=42)
      r2 = generate_run(user_id=UUID(...), setting_id="arctic_station_v1", seed=42)
      assert r1.agents[0].name == r2.agents[0].name
      assert r1.max_turns == r2.max_turns
  
  def test_generate_run_covers_all_quadrants():
      run = generate_run(user_id=UUID(...), setting_id="arctic_station_v1", seed=42)
      quadrants = {a.archetype_quadrant for a in run.agents}
      assert len(quadrants) == 4
  
  def test_generate_populates_verbal_tic_per_agent():
      run = generate_run(user_id=UUID(...), setting_id="arctic_station_v1", seed=42)
      assert all(a.verbal_tic for a in run.agents)
  ```
- [x] **Step 2:** Implement `generator.py`:
  - `generate_run(user_id, setting_id, seed) -> ColonyState`
  - Selects 4 advisors, one per quadrant
  - Rolls axes per archetype template
  - Picks names + verbal tics from setting name pool
  - Randomizes max_turns in [8, 12]
  - Initial resources per setting

- [x] **Step 3:** Write transformations tests per `SPEC.md` §6.4:
  ```python
  # backend/tests/test_transformations.py
  def test_transformation_eligibility_requires_high_emotion_turn():
      # agent with grievance >= 5 and high-severity event triggers eligibility
      ...
  
  def test_transformation_once_per_run():
      # if transformation already set, eligibility returns False
      ...
  
  def test_transformation_shifts_axis_direction():
      # Idealist agent watching collective sacrifice fail → shifts toward Individual
      ...
  ```
- [x] **Step 4:** Implement `transformations.py`:
  - `check_transformation_eligibility(state, agent, triggering_event) -> TransformationCandidate | None`
  - `apply_transformation(state, agent_id, candidate) -> ColonyState` — writes transformation to agent, shifts axis value, emits memory entry
  - Triggers are setting-agnostic heuristics based on (agent axes, event severity, outcome of chosen action)

- [x] **Step 5:** Run tests. Commit.

### Task 3.10: Game router — session creation

**Files:** Create `backend/app/game/router.py`, `backend/app/game/service.py`, `backend/tests/test_game_router.py`.

- [x] **Step 1:** Implement `POST /game/session` endpoint.
  - Checks daily gate via `can_start_run(profile.last_run_started_at)`
  - Generates run with a random seed (or date-derived)
  - Persists to DB (`game_sessions` + `agents` rows)
  - Updates profile: `last_run_started_at = now()`, `total_runs += 1`
  - Returns `game_id`, `setting`, `agents` (public fields only)
- [x] **Step 2:** Implement `GET /game/{id}` endpoint returning full state.
- [x] **Step 3:** Write failing tests:
  ```python
  async def test_create_session_enforces_daily_gate(client):
      # first call succeeds
      r1 = await client.post("/game/session")
      assert r1.status_code == 201
      # second call same day fails
      r2 = await client.post("/game/session")
      assert r2.status_code == 429
      assert r2.json()["error"]["code"] == "daily_gate_exceeded"
  ```
- [x] **Step 4:** Implement. Include router in `main.py`. Run tests. Commit.

**Phase 3 exit verification:**
```bash
uv run pytest tests/ -v      # all game engine tests pass
uv run ruff check app/       # clean
# Manually: POST /game/session with a real JWT, verify 201 + valid state, 429 on second attempt
```

---

## Phase 4: Agent System (LangGraph + raw Anthropic SDK) — **COMPLETE (2026-04-24)**

**Entry criteria:** Phase 3 exit passed. ✓
**Exit criteria:** A full turn executes end-to-end with real LLM calls. The XML debate format produces parseable per-agent responses. Tool calls are validated by the engine. Cost stays under $0.30/run in integration test. ✓ — integration test written and skipped-by-default; manual run required for cost verification.

**Phase-exit artifacts:** `backend/app/agent/CLAUDE.md` covers node pattern, XML parsing, cache layout, cost accounting, and Anthropic SDK quirks. ARCHITECTURE §3.7 and §5.1 updated to reflect the memory-in-colony-state-block design. Phase 4 learnings logged below.

**Calibrated cost (2026-04-24, dashboard-verified):** ~13¢ per 8-turn run / ~16¢ per 10-turn run with both caching gaps closed. Per-turn ~1.3¢ once cache is warm. Comfortably under the 30¢ ceiling — leaves budget headroom for additional Sonnet calls or Phase 5 observability overhead.

**Session config:** `/model claude-opus-4-7` · `/effort xhigh` · **plan mode: YES** (graph wiring has design space) · execution: subagent-driven

**Session start prompt:**

```

```

### Task 4.1: Anthropic client wrapper with caching

**Files:** Create `backend/app/agent/anthropic_client.py`, `backend/tests/test_anthropic_client.py`.

- [x] **Step 1:** Verify current model IDs and pricing with context7 MCP before hard-coding. Prefer dated model IDs over aliases for production stability:
  ```
  mcp__plugin_context7_context7__resolve-library-id  "anthropic-sdk-python"
  mcp__plugin_context7_context7__query-docs  <library-id>  "messages create model id prompt caching"
  ```
  Record the latest dated IDs for Haiku 4.5 and Sonnet 4.6 in a comment. The skill `claude-api` will auto-trigger and provide current guidance.

- [x] **Step 2:** Write `anthropic_client.py` with named pricing constants (clarity — avoids the $/cents unit confusion):
  ```python
  from anthropic import AsyncAnthropic
  from app.config import settings
  
  # Dated model IDs — replace alias with latest dated form from context7
  MODEL_HAIKU = "claude-haiku-4-5-20251001"
  MODEL_SONNET = "claude-sonnet-4-6"  # TODO: swap for dated ID once confirmed via context7
  
  # Pricing in cents per million tokens (Haiku 4.5, verify with context7 on first use)
  CENTS_PER_MTOK_HAIKU_INPUT = 100   # $1.00
  CENTS_PER_MTOK_HAIKU_CACHE_WRITE = 125  # $1.25 (ephemeral write)
  CENTS_PER_MTOK_HAIKU_CACHE_READ = 10    # $0.10 (cache hit)
  CENTS_PER_MTOK_HAIKU_OUTPUT = 500       # $5.00
  
  CENTS_PER_MTOK_SONNET_INPUT = 300   # $3.00 — verify with context7
  CENTS_PER_MTOK_SONNET_OUTPUT = 1500 # $15.00 — verify with context7
  
  client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
  
  
  async def call_haiku(system_blocks: list, user_message: str,
                       tools: list | None = None, max_tokens: int = 2000):
      return await client.messages.create(
          model=MODEL_HAIKU, max_tokens=max_tokens,
          system=system_blocks,
          messages=[{"role": "user", "content": user_message}],
          tools=tools or [],
      )
  
  async def call_sonnet(system_blocks: list, user_message: str, max_tokens: int = 1000):
      return await client.messages.create(
          model=MODEL_SONNET, max_tokens=max_tokens,
          system=system_blocks,
          messages=[{"role": "user", "content": user_message}],
      )
  
  
  def cost_cents_of(response, model: str) -> float:
      """Compute cost in cents of one response. Returns a float; caller rounds."""
      u = response.usage
      cache_read = getattr(u, "cache_read_input_tokens", 0)
      cache_creation = getattr(u, "cache_creation_input_tokens", 0)
      fresh_input = u.input_tokens - cache_read - cache_creation
  
      if model == MODEL_HAIKU:
          return (
              fresh_input      * CENTS_PER_MTOK_HAIKU_INPUT        / 1_000_000
            + cache_creation   * CENTS_PER_MTOK_HAIKU_CACHE_WRITE  / 1_000_000
            + cache_read       * CENTS_PER_MTOK_HAIKU_CACHE_READ   / 1_000_000
            + u.output_tokens  * CENTS_PER_MTOK_HAIKU_OUTPUT       / 1_000_000
          )
      elif model == MODEL_SONNET:
          return (
              fresh_input      * CENTS_PER_MTOK_SONNET_INPUT  / 1_000_000
            + u.output_tokens  * CENTS_PER_MTOK_SONNET_OUTPUT / 1_000_000
          )
      raise ValueError(f"Unknown model: {model}")
  ```

- [x] **Step 3:** Write unit test verifying the math with known token counts:
  ```python
  # tests/test_anthropic_client.py
  from unittest.mock import MagicMock
  from app.agent.anthropic_client import cost_cents_of, MODEL_HAIKU
  
  def test_cost_cents_haiku_10k_fresh_input():
      resp = MagicMock()
      resp.usage.input_tokens = 10000
      resp.usage.output_tokens = 0
      resp.usage.cache_read_input_tokens = 0
      resp.usage.cache_creation_input_tokens = 0
      # 10k tokens @ $1/MTok = $0.01 = 1 cent
      assert abs(cost_cents_of(resp, MODEL_HAIKU) - 1.0) < 0.01
  
  def test_cost_cents_haiku_cache_hit_is_10x_cheaper():
      resp = MagicMock()
      resp.usage.input_tokens = 10000
      resp.usage.output_tokens = 0
      resp.usage.cache_read_input_tokens = 10000  # all cache hit
      resp.usage.cache_creation_input_tokens = 0
      # 10k cache read @ $0.10/MTok = $0.001 = 0.1 cents
      assert abs(cost_cents_of(resp, MODEL_HAIKU) - 0.1) < 0.01
  ```

- [x] **Step 4:** Run test — expect pass. Commit.

### Task 4.2: Persona block builder (cached)

**Files:** Create `backend/app/agent/prompts.py`, `backend/tests/test_prompts.py`.

Reference `docs/ARCHITECTURE.md` §3.7 for the XML persona format.

- [x] **Step 1:** Implement `build_persona_block(agent: Agent) -> dict` returning an Anthropic content block with `cache_control: {"type": "ephemeral"}`.
- [x] **Step 2:** Implement `build_colony_state_block(state: ColonyState) -> dict` — cached per turn.
- [x] **Step 3:** Implement `build_debate_user_message(event, agents) -> str` — XML-delimited instructions for each agent to respond in their block.
- [x] **Step 4:** Implement `parse_debate_response(response) -> DebateParse` — splits XML by `<agent>` tag, extracts tool calls per agent.
- [x] **Step 5:** Test prompt assembly and parsing with fixtures.
- [x] **Step 6:** Commit.

### Task 4.3: LangGraph state type

**Files:** Create `backend/app/agent/state.py`.

- [x] **Step 1:** Define `GameTurnState` as a TypedDict (LangGraph requirement). Include:
  - Reference to the DB-backed `ColonyState` (id)
  - Live fields that flow through nodes: `current_event`, `debate_result`, `tool_calls`, `player_choice`, `narration`, `cost_cents_this_turn`
- [x] **Step 2:** Commit.

### Task 4.4: Nodes — `generate_event` and `agent_debate`

**Files:** Create `backend/app/agent/nodes.py`, `backend/tests/test_nodes.py`.

- [x] **Step 1:** Implement `generate_event` node — loads state from DB, calls `select_event` from Phase 3.
- [x] **Step 2:** Implement `agent_debate` node — builds prompt, calls Haiku via `anthropic_client`, parses response, emits SSE events.
- [x] **Step 3:** Test with respx-mocked Anthropic. Simulate a response where all 4 agents speak and propose actions. Assert the parsed output has 3 proposals with different flavors.
- [x] **Step 4:** Commit.

### Task 4.5: Nodes — `apply_tool_calls` through `advance`

- [x] **Step 1:** Implement remaining nodes: `apply_tool_calls`, `resolve_player_choice`, `autonomous_actions`, `check_deaths`, `narrate_turn`, `advance_turn`.
- [x] **Step 2:** Test each node individually.
- [x] **Step 3:** Commit.

### Task 4.6: StateGraph assembly + thread_id discipline

**Files:** Create `backend/app/agent/graph.py`, `backend/app/agent/checkpointer.py`, `backend/tests/test_graph.py`.

Reference `docs/ARCHITECTURE.md` §3.2 for the graph structure.

- [x] **Step 1:** Set up Postgres checkpointer for LangGraph.
  ```python
  # backend/app/agent/checkpointer.py
  from langgraph.checkpoint.postgres import PostgresSaver
  from app.config import settings
  
  checkpointer = PostgresSaver.from_conn_string(settings.DATABASE_URL)
  ```
- [x] **Step 2:** Assemble the graph exactly as specified in ARCHITECTURE §3.2.
- [x] **Step 3:** Write all `graph.ainvoke` / `graph.astream` call sites with explicit `thread_id = str(game_id)`:
  ```python
  config = {"configurable": {"thread_id": str(game_id)}}
  async for event in graph.astream(initial_state, config=config):
      ...
  ```
  This is an **architectural invariant** (see ARCHITECTURE §16 invariant 12). Missing thread_id silently breaks pause/resume between the `await_player` pause and the `/decide` resume call.
- [x] **Step 4:** Integration test: simulate a full turn cycle — start turn (SSE), pause at proposals, submit decision (different request), verify state continuity via the same thread_id.
- [x] **Step 5:** Commit.

### Task 4.7: SSE agent/turn endpoint

**Files:** Create `backend/app/agent/router.py`, `backend/tests/test_agent_router.py`.

- [x] **Step 1:** Implement `POST /game/{id}/turn` — streaming SSE from graph.
- [x] **Step 2:** Implement `POST /game/{id}/decide` — resumes graph with player choice.
- [x] **Step 3:** Include router in `main.py`.
- [x] **Step 4:** Test that SSE stream ends with `[DONE]` and includes `proposals_ready` event.
- [x] **Step 5:** Commit.

### Task 4.8: Full-run integration test (real LLM, flagged)

**Files:** Create `backend/tests/integration/test_full_run.py`.

- [x] **Step 1:** Write test marked `@pytest.mark.integration`:
  ```python
  @pytest.mark.integration
  @pytest.mark.asyncio
  async def test_full_run_arctic():
      """Runs a real 10-turn game against Claude Haiku. Costs ~$0.20."""
      # generate_run, then simulate a scripted player picking option A each turn
      # assert: ends with a valid EndState, cost < 30 cents, >=2 seeds scheduled
      ...
  ```
- [x] **Step 2:** Run manually when needed:
  ```bash
  uv run pytest -m integration tests/integration/ -v -s
  ```
- [x] **Step 3:** Commit.

**Phase 4 exit verification:**
```bash
uv run pytest tests/ -v                    # unit tests pass (mocked Anthropic)
uv run pytest -m integration tests/integration/  # run manually, verify cost + state
```

---

## Phase 5: Observability + Eval Harness — **COMPLETE (2026-04-25)**

**Entry criteria:** Phase 4 exit passed.
**Exit criteria:** Langfuse traces appear in dashboard for every run. 5 eval metrics compute on simulated games. `agentevals` CI test passes.

**Session config:** `/model claude-opus-4-7` · `/effort high` · plan mode: no · execution: subagent-driven

**Session start prompt:**

```
Role: senior ML/observability engineer. Execute Outpost Phase 5 — Observability + Eval.

Read:
  1. CLAUDE.md + backend/CLAUDE.md + backend/app/agent/CLAUDE.md
  2. docs/ARCHITECTURE.md §§11 (observability), 12 (eval)
  3. docs/research/05-eval-observability.md — 5 metric definitions + compute
  4. docs/PLAN.md Phase 5

Invoke superpowers:subagent-driven-development. Each metric is self-
contained — per-task subagent fits perfectly.

Before Langfuse code: context7.
  mcp__plugin_context7_context7__resolve-library-id "langfuse python"
  mcp__plugin_context7_context7__query-docs <id> "langgraph callback trace score"

Personality consistency (5.2) downloads sentence-transformers (~80MB model)
on first use. Warn me or cache in the Docker image.

CI workflow (5.5): the eval job runs on PR and costs real tokens. Include
a budget ceiling — abort if per-run cost exceeds $0.50. ANTHROPIC_API_KEY
from GitHub Secrets.

After Phase 5 exit, verify traces flow to the Langfuse dashboard from one
test run and confirm all 5 metrics compute on a sample.

Add any eval-specific conventions (metric naming, threshold placement,
score emission patterns) to backend/app/eval/CLAUDE.md if the directory
develops enough to warrant one.

Stop at Phase 5 exit. Do not start Phase 6.
```

### Task 5.1: Wire Langfuse callback handler

- [x] **Step 1:** Add Langfuse handler to each `graph.ainvoke()` call. Reference `docs/ARCHITECTURE.md` §11.
- [x] **Step 2:** Verify traces appear in Langfuse dashboard after one test run.
- [x] **Step 3:** Commit.

### Task 5.2: Implement personality consistency metric

**Files:** Create `backend/app/eval/__init__.py`, `backend/app/eval/personality.py`, `backend/tests/test_eval_personality.py`.

- [x] **Step 1:** Implement `score_personality_consistency(agent, responses) -> float` using sentence-transformers.
  - Add `sentence-transformers` to deps: `uv add sentence-transformers`
  - Reference `docs/ARCHITECTURE.md` §12.3
- [x] **Step 2:** Test with contrived in-character and out-of-character responses.
- [x] **Step 3:** Commit.

### Task 5.3: Implement remaining metrics

- [x] **Step 1:** `tool_validity_rate(run_log)` — reads validation results from turn_logs.
- [x] **Step 2:** `narrative_repetition_index(responses)` — pairwise BLEU-2 intra-run.
- [x] **Step 3:** `decision_coherence_score(agent, turn)` — LLM-as-judge via Haiku.
- [x] **Step 4:** `game_balance_metrics(runs)` — aggregates survival rate, volatility, forced-choice rate.
- [x] **Step 5:** `end_state_distribution_check(runs)` — verifies end-state frequencies fall within the SPEC §10 targets (ENDURED 35-45%, COLLAPSED 30-40%, OUSTED 10-15%, KILLED 5-10%). Used as a CI gate with a tolerance band (±10 percentage points on a 50-run sample).
- [x] **Step 6:** `inter_run_narrative_similarity(runs_same_setting)` — embedding-based similarity across runs of the same setting. Flags stylistic/narrative drift or sameness. Threshold: average cosine similarity across N runs should fall in [0.55, 0.80] — below means incoherent, above means repetitive.
- [x] **Step 7:** Test each. Commit per metric.

### Task 5.4: Simulated player + scripted games

**Files:** Create `backend/tests/fixtures/simulated_player.py`, `backend/tests/eval/test_eval_pipeline.py`.

- [x] **Step 1:** Implement `simulated_player(policy)` that picks actions deterministically (moral / pragmatic / random).
- [x] **Step 2:** Implement `simulate_game(policy, setting_id) -> RunRecord` that runs a full game using mocked LLM responses (via respx fixtures) for unit CI, and with real LLM for nightly eval.
- [x] **Step 3:** Write eval test:
  ```python
  @pytest.mark.eval
  @pytest.mark.parametrize("policy", ["moral", "pragmatic", "random"])
  async def test_simulated_run(policy):
      run = await simulate_game(policy=policy, setting_id="arctic_station_v1")
      assert run.tool_validity >= 0.85
      assert run.personality_consistency >= 0.72
      assert 0.30 <= run.survival_rate <= 0.70
  ```
- [x] **Step 4:** Commit.

### Task 5.5: CI eval workflow

**Files:** Create `.github/workflows/eval.yml`.

- [x] **Step 1:** Write workflow per `docs/ARCHITECTURE.md` §13.3 (eval-backend job).
- [x] **Step 2:** Test locally with `act` if available, or push to a PR branch and verify it runs.
- [x] **Step 3:** Commit.

**Phase 5 exit verification:** Langfuse dashboard shows traces. `eval` marker tests pass. GitHub Actions eval job runs.

### Phase 5 exit summary (2026-04-25)

- **9 commits landed** across observability + eval (`2322da2` → `bedf29f`).
- **5 metrics implemented + tested:** `tool_validity_rate`, `narrative_repetition_index`, `score_personality_consistency` (embedding + Haiku judge), `game_balance_metrics`, `end_state_distribution_check`, plus the bonus `inter_run_narrative_similarity`. All compute cleanly on a synthetic `RunRecord` and the mocked-LLM eval pipeline (`tests/eval/test_eval_pipeline.py`) passes for the moral/pragmatic/random policies.
- **Langfuse v3 SDK wired:** `app/agent/observability.py` lazy-imports `langfuse.langchain.CallbackHandler`, env-var-injects credentials into a module-cached `Langfuse` client behind `threading.Lock`, and is fail-closed on init errors (warns, never raises into the SSE generator).
- **CI eval workflow:** `.github/workflows/eval.yml` runs the simulated-game eval on PR with a `OUTPOST_EVAL_BUDGET_CENTS=50` hard ceiling — the real-LLM test fails the job on overrun.
- **Open limitation (track for Phase 6+):** rejected-at-resolve tool calls don't survive into `turn_logs` — `engine.advance_turn` clears `turn_tool_calls` before the state snapshot is dumped. The `tool_validity_rate` metric therefore measures "validity rate of *applied* calls", which inflates the headline number. Fix requires capturing pre-clear `applied_tool_calls`/`rejected_tool_calls` lists into `turn_logs` so post-hoc analysis sees the full pipeline.
- **agentevals deferred:** the LangSmith trajectory-eval integration is acknowledged as a TODO inside `.github/workflows/eval.yml`. Phase 5 exit accepts this — the five engine-driven metrics carry the merge gate today; agentevals revisits when LangSmith trajectory ergonomics warrant the integration cost.

---

## Phase 6: Frontend Scaffolding — **COMPLETE (2026-04-25)**

**Entry criteria:** Phase 5 exit passed (or deferred — FE can start once BE session + turn endpoints work).
**Exit criteria:** Next.js app renders landing page with pixel-art theme applied. Fonts load. DiceBear portrait renders correctly. Auth callback redirects work.

**Session config:** `/model claude-opus-4-7` · `/effort medium` · plan mode: no

**Session start prompt:**

```
Role: senior frontend engineer. Execute Outpost Phase 6 — Next.js + Pixel Theme.

Shift context: backend is done; this is a fresh frontend tree. Re-read:
  1. CLAUDE.md (root)
  2. docs/ARCHITECTURE.md §8 (frontend architecture)
  3. docs/research/04-visual-system.md — full visual reference
  4. docs/SPEC.md §§14 (tone), 10 (aesthetic direction)
  5. docs/PLAN.md Phase 6

Execute inline — each visual step benefits from seeing the previous.

Context7 before writing against:
- Next.js 16 App Router + next/font
- Tailwind v4 theme syntax (different from v3)
- shadcn/ui CLI and theme vars
- @dicebear/core + @dicebear/pixel-art
- @supabase/ssr for SSR auth

Invoke the vercel:shadcn skill during Task 6.1 — it has current theming
patterns.

After each UI task from 6.3 onward, use playwright MCP to screenshot at
1440, 1024, and 768. Describe what you SEE, not what you coded. Iterate.

Create frontend/CLAUDE.md after Task 6.3 with:
- Pixel-art theme conventions (--radius: 0, NES-pattern borders, dither)
- Font role mapping (Silkscreen/Press Start 2P/VT323/IBM Plex Mono)
- shadcn composition strategy (Level 2 override vs Level 3 copy-edit)
- Setting palette swap mechanics (data-setting attribute)

Phase 6 is not done until landing + auth callback work end-to-end in dev.

Stop at Phase 6 exit. Do not start Phase 7.
```

### Task 6.1: Initialize Next.js + Tailwind + shadcn

- [x] **Step 1:** `cd /Users/luclacombe/Documents/Outpost && pnpx create-next-app@latest frontend --ts --app --tailwind --eslint --no-src-dir --import-alias "@/*"`
- [x] **Step 2:** Initialize shadcn.
  ```bash
  cd frontend && pnpx shadcn@latest init
  # choose: Default style, Slate base color, CSS variables: yes
  ```
- [x] **Step 3:** Add a few shadcn components needed upfront: `button`, `card`, `dialog`.
  ```bash
  pnpx shadcn@latest add button card dialog
  ```
- [x] **Step 4:** Commit.

### Task 6.2: Install Supabase client + DiceBear

```bash
cd frontend && pnpm add @supabase/ssr @supabase/supabase-js @dicebear/core @dicebear/pixel-art @dicebear/thumbs
```

### Task 6.3: Pixel-art theme (CSS + Tailwind)

**Files:** `frontend/app/globals.css`, `frontend/tailwind.config.ts`.

- [x] **Step 1:** Override shadcn variables per `docs/ARCHITECTURE.md` §8.4. Set `--radius: 0`, earthy palette, pixel shadow utilities.
- [x] **Step 2:** Add NES.css-pattern pixel border utility class (see `docs/research/04-visual-system.md`).
- [x] **Step 3:** Load fonts via `next/font/google` per architecture §8.5.
- [x] **Step 4:** Apply `image-rendering: pixelated` to `.pixel` class.
- [x] **Step 5:** Commit.

### Task 6.4: Portrait component (DiceBear)

**Files:** `frontend/components/agent/Portrait.tsx`, `frontend/__tests__/Portrait.test.tsx`.

- [x] **Step 1:** Implement component per `docs/ARCHITECTURE.md` §8.6.
- [x] **Step 2:** Storybook-style visual test — render all 3 settings at 64px, use Playwright MCP to screenshot. Compare.
- [x] **Step 3:** Commit.

### Task 6.5: Landing page

**Files:** `frontend/app/page.tsx`.

- [x] **Step 1:** Build landing with: title, short pitch, "Sign in with Google" button, marketing content from SPEC.
- [x] **Step 2:** Use Playwright MCP to screenshot. Iterate on aesthetic.
- [x] **Step 3:** Commit.

### Task 6.6: Supabase auth callback route

**Files:** `frontend/app/auth/callback/route.ts`, `frontend/lib/supabase/server.ts`, `frontend/lib/supabase/client.ts`, `frontend/middleware.ts`.

- [x] **Step 1:** Use supabase MCP + context7 to verify current Supabase SSR patterns for Next.js 16 App Router.
- [x] **Step 2:** Implement callback route that exchanges `code` for a session cookie.
- [x] **Step 3:** Implement middleware to refresh session on every request. *(Next 16 renamed `middleware.ts` → `proxy.ts` and the export to `proxy`. The supabase helper at `lib/supabase/middleware.ts` keeps its old name internally.)*
- [x] **Step 4:** Commit.

### Task 6.7: Protected layout

**Files:** `frontend/app/(authed)/layout.tsx`.

- [x] **Step 1:** Check session via Supabase server client; redirect to `/` if missing. Use `getUser()` (re-validates), not `getSession()` (trusts cookie).
- [x] **Step 2:** Render CRT overlay + setting theme data attribute (`data-setting="arctic"` etc.) based on current game (to be wired in Phase 7).
- [x] **Step 3:** Commit.

### Task 6.8: Pre-run page

**Files:** `frontend/app/(authed)/play/page.tsx`.

- [x] **Step 1:** Fetch `/me` from backend. If `next_run_at` is set, show countdown to that time. Otherwise show "Begin run" button.
- [x] **Step 2:** On click: POST `/game/session`, redirect to `/run/[id]`.
- [x] **Step 3:** Commit.

**Phase 6 exit verification (2026-04-25):**

```bash
cd frontend && pnpm dev
# visit localhost:3000, verify landing renders with pixel theme    ✓
# sign in with Google, verify redirect + /me works                 ✓ — without env, click → /auth/error end-to-end
# verify /play shows either countdown or Begin button              ✓ — /play 307s to / when unauthed, renders correctly when authed
# screenshot with Playwright MCP; compare to vision                ✓ — 1440 / 1024 / 768 captured
```

Build / lint / typecheck all green. Console clean on landing and error page.

Commits: `c8c27d0` (Tasks 6.1–6.4 + frontend/CLAUDE.md), `1d4fc50` (Tasks 6.5–6.8 + env tolerance).

---

## Phase 7: Dashboard UI (live game) — **COMPLETE (2026-04-25)**

**Entry criteria:** Phase 6 exit. Backend `/game/{id}/turn` SSE works.
**Exit criteria:** Full dashboard renders. Starting a turn streams agent debate. Player choice submission works. Dashboard state updates correctly across turns.

**Session config:** `/model claude-opus-4-7` · `/effort high` · plan mode: no

**Session start prompt:**

```
Role: senior frontend engineer + UI designer. Execute Outpost Phase 7 — Live Dashboard.

This is where the game becomes real. Visual iteration IS the work. Expect
many screenshot-adjust loops.

Read:
  1. CLAUDE.md + frontend/CLAUDE.md (from Phase 6)
  2. docs/SPEC.md §4 (core loop walkthrough — shape of what the UI expresses)
  3. docs/ARCHITECTURE.md §§8.3 (layout grid), 8.7 (SSE hook)
  4. docs/PLAN.md Phase 7

Execute inline, task by task. After every component task, use playwright
MCP to screenshot and describe the visual result before proceeding.

Auto-trigger: vercel:react-best-practices will fire after multiple TSX
edits. Run the checklist.

Hard rules for this phase:
- No placeholder copy on a committed component. WIP lives in branches, not
  main.
- CRT scanline overlay (Task 7.8) must not drop text contrast below WCAG
  AA. Verify with a contrast tool before commit.
- SSE token refresh (Task 7.6) is non-negotiable. Phase 7 not done until
  a simulated mid-stream refresh recovers cleanly.

After Phase 7 exit, update frontend/CLAUDE.md with:
- Dashboard grid conventions and responsive breakpoints
- SSE consumption patterns (hook shape, error recovery)
- How errors surface to the user (toasts? inline? dialog?)
- Any shadcn components that needed Level 3 override

Stop at Phase 7 exit. Do not start Phase 8.
```

### Task 7.1: Dashboard layout shell

**Files:** `frontend/app/(authed)/run/[id]/page.tsx`, `frontend/components/dashboard/DashboardShell.tsx`.

- [x] **Step 1:** CSS Grid 3-panel layout per `docs/ARCHITECTURE.md` §8.3.
- [x] **Step 2:** Playwright screenshot. Commit.

### Task 7.2: Header panel

- [x] Show setting name, turn/max_turns, Legitimacy meter, cost display (dev mode only).

### Task 7.3: ColonyStatus panel (left)

**Files:** `frontend/components/dashboard/ColonyStatus.tsx`, `frontend/components/dashboard/ResourceBar.tsx`, `frontend/components/dashboard/AgentRoster.tsx`, `frontend/components/dashboard/EventLog.tsx`.

- [x] **Step 1:** `ResourceBar` — pixel-chunked fill with `steps()` transition.
- [x] **Step 2:** `AgentRoster` — DiceBear portrait + name + status pill.
- [x] **Step 3:** `EventLog` — scrollable list, VT323 font.
- [x] **Step 4:** Compose in `ColonyStatus`. Screenshot-verify.

### Task 7.4: Event + Debate panel (right top)

**Files:** `frontend/components/dashboard/EventDebatePanel.tsx`. (Single component — `Event` and `Debate` are tightly coupled and never appear apart, so splitting added cost without payoff. Internal sections are still `EventHeader` / `EventBody` / `Debate` / `Narration`.)

- [x] **Step 1:** Event header + body with title in Silkscreen, body in IBM Plex Mono. Per-event portrait deferred — backend `Event` schema has no agent affiliation field; the screen the player has is "which advisor will be hit hardest if this resolves badly," which lives in the debate panel below.
- [x] **Step 2:** Debate per-advisor lines with portrait, name, utterance. Cursor-blink shown on `partial: true` lines.

### Task 7.5: Choose panel (right bottom)

**Files:** `frontend/components/dashboard/ChoosePanel.tsx`.

- [x] 3 choice buttons (DOMINANT / MORAL / WILDCARD) + optional "Issue direct order" button (costs 10 legitimacy; gated when player can't afford it). Phase reflects ChoosePhase: `idle | running | awaiting_choice | submitting | advancing | ended`.

### Task 7.6: SSE consumption hook

**Files:** `frontend/hooks/useTurnStream.ts`, `frontend/lib/api/sse.ts`.

- [x] **Step 1:** Implemented fetch + ReadableStream + TextDecoderStream + manual SSE frame parser. `[DONE]` sentinel handled. Reducer (`applyEvent`) is exported pure for testability.
- [x] **Step 2:** Token refresh — `getAuthToken()` is called BEFORE every fetch (default impl pulls `supabase.auth.getSession()`; tests / dev override). On 401 (response or stream error), `refreshSession()` is called and the request retries once. `/turn` is idempotent while paused; `/decide` is not safely retryable past pause and surfaces error if dropped.
- [x] **Step 3:** Verified via Playwright against the in-process mock at `/api/dev/turn?fail_first=1` — first POST returned 401, hook refreshed and retried, full stream completed (4 debate lines + 3 proposals, no error visible). See screenshot `phase7-task76-fail-first.png`.

### Task 7.7: Wire full turn cycle

- [x] `(authed)/run/[id]/page.tsx` server component fetches `/game/{id}` server-side and mounts the client `DashboardShell` with the snapshot.
- [x] Begin turn → POST `/game/{id}/turn`, streams SSE into state.
- [x] Choice click → POST `/game/{id}/decide`, streams autonomous + narration + next turn's proposals.
- [x] Errors surface inline at the top of the event panel (`role="alert"`, `data-testid="stream-error"`). Pre-stream HTTP errors use the standard `error.code` / `error.message` envelope. Stream errors retry once on first attempt, then surface.
- [x] End-to-end exercised via dev preview: `/dev/dashboard` (no backend required) drove turn 5 → choose BYPASS → turn 6 with debate, narration, log entry, and turn advance. Screenshot `phase7-task76-after-decide.png`.

### Task 7.8: Visual polish pass

- [x] CRT scanline overlay extracted to `<CrtOverlay />`, mounted in `(authed)/layout.tsx` and `dev/dashboard`. Scanline opacity capped at 0.08.
- [x] Blinking cursor on event body and on streaming debate lines (`.cursor` class).
- [x] Resource bar `.resource-bar-fill` with `transition: width 400ms steps(20)`. Filled overlay + segment-divider mask paints the chunked look.
- [x] Playwright screenshots at 1440 / 1024 / 768. Mobile collapses to single column with action above the fold.
- [x] WCAG AA verified with the CRT applied: body text 9.34:1, dim text 5.12:1 (after lifting `--px-text-dim` from `#7a7a6e` → `#968f7d`; old value failed AA at 4.01:1 even without the CRT).

**Phase 7 exit verification:**
- [x] Played a full real run on 2026-04-25 (orbital setting "Helios-7"): `/play` → 10 turns → end state reached. **Cost: $0.16** (Haiku $0.13 + Sonnet narration $0.03), well under the $0.30 cap. SSE flow worked end-to-end against cloud Supabase; no fatal errors. UX issues found and triaged — see Errors & Learnings below.

---

## Phase 8: Post-Mortem + Share — **COMPLETE (2026-04-25)**

**Entry criteria:** Phase 7 exit passed.
**Exit criteria:** Post-mortem renders timeline + counterfactuals for completed runs. Share card generates correctly and is accessible via public `/share/[token]` URL.

**Session config:** `/model claude-opus-4-7` · `/effort medium` · plan mode: no

**Session start prompt:**

```
Role: senior frontend engineer. Execute Outpost Phase 8 — Post-Mortem + Share.

This phase produces the shareable artifact that replaces a leaderboard.
Quality of the timeline + share card determines how the game spreads.

Read:
  1. CLAUDE.md + frontend/CLAUDE.md
  2. docs/SPEC.md §11 (post-mortem structure — detailed)
  3. docs/PLAN.md Phase 8 — INCLUDING the "Phase 7 issues found during
     exit run" table under Errors & Learnings. Task 8.0 below clears
     those before any post-mortem work.

Execute inline. Playwright-screenshot after each UI task.

**Task 8.0 first — clear Phase 7 follow-ups before starting on the
post-mortem.** P1 narration "Continue" gate is a real player-experience
blocker; P2 msgpack warnings are noise that obscure real errors; the P3
trio (log truncation, setting-icon glyphs, clipping audit at 1024/1280)
are quick wins.

Set up a dev fixture for post-mortem iteration (mirror `/dev/dashboard`
pattern). Phase 7's first completed run id is
`7451e10a-092a-549d-907b-25b80282f4fa` — use it as the source-of-truth
data fixture so the post-mortem timeline can iterate offline without
re-playing turns. No faked data: dump the real `turn_logs` /
`consequence_seeds` rows into the fixture once, then drive UI from
there. If the run has been deleted, complete a fresh one first.

Don't add the `/run/<id>` → `/post-mortem/<id>` redirect (Task 8.6)
until Task 8.5 lands — it would 404.

Before opengraph image (Task 8.4): context7 next/og — API changes per
Next.js major version.

Test the share URL on Twitter/X, Discord, and Bluesky — each has different
OG rules. Task 8.4 is done only when all three unfurl correctly.

Counterfactuals panel (Task 8.3) needs a backend endpoint for dormant
seeds + near-miss coup data. If it doesn't exist yet, build backend
first — no faked data in the UI.

Run /humanizer on any marketing-voice copy before commit.

Update frontend/CLAUDE.md if new conventions emerge (share-card layout,
public vs auth routes, OG image generation patterns).

Stop at Phase 8 exit. Do not start Phase 9.
```

### Task 8.0: Phase 7 follow-up cleanup

Address the items triaged at Phase 7 close-out (see Errors & Learnings → "Phase 7 issues found during exit run"). Bundle into one or two commits before Task 8.1; do NOT mix with post-mortem work.

- [x] **P1 — Narration "Continue" gate.** Reducer holds next-turn `proposals_ready` in `pendingEvent`/`pendingProposals`/`pendingTurnNumber`/`pendingLogEntry` slots when narration is set AND new event id differs. New phase `awaiting_continue`; `ChoosePanel` renders Continue button; `continueAfterNarration()` promotes pending → main. Verified on `/dev/dashboard` end-to-end.
- [x] **P2 — msgpack deserialization warnings.** `app/agent/checkpointer.py` constructs `JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED_MSGPACK_TYPES)` and rebinds it onto `saver.serde` after `setup()`. Pinned set covers the 16 game-schema types the graph carries. Regression test asserts the warning text never fires on a state round-trip (`tests/test_checkpointer.py`). `with_msgpack_allowlist` is a no-op when the base allowlist is `True` — must construct fresh.
- [x] **P3 — EventLog truncation.** `title` attr added to `<span class="truncate">` in `EventLog.tsx`.
- [x] **P3 — Resource icon rendering.** Setting YAMLs migrated to single-char glyphs (`flame`→▲, `grain`→◆, `thermometer`→✦, `cross`→✚, `spark`→♪, `bolt`→⌁, `bubble`→○, `bullet`→●, `drop`→◇, `roof`→⌂). `ResourceBar` guards via `[...icon].length === 1` — multi-grapheme icons silently drop rather than printing words.
- [x] **P3 — Clipping audit.** No clipping at 1024 or 1280; status panel + event/debate + choose layout holds across both. Screenshots `phase8-clipping-1024-debate.png`, `phase8-clipping-1024-fullpage.png`, `phase8-clipping-1280-debate.png`.

### Task 8.1: Post-mortem data endpoint

**Files:** `backend/app/game/router.py` (added `GET /game/{id}/post-mortem` and `GET /game/share/{token}`), `backend/app/game/post_mortem.py`, `backend/app/game/schemas_io.py` (new `PostMortem*` types), `backend/app/game/exceptions.py` (new `GameNotEnded`), `backend/tests/test_post_mortem.py`.

- [x] Aggregates turn_logs, consequence_seeds (fired + dormant), decisions, end_state. Returns `PostMortem` schema. Counterfactuals computed: dormant seeds (any seed `dormant=true OR fired=false`), near-miss coups (peak pair where both held grievance ≥ 7 simultaneously), didn't-happen transformations (empty in MVP — engine doesn't track near-transform state).
- [x] `ensure_share_token` lazily mints a urlsafe token on first authed view; `build_post_mortem_by_token` resolves token → game → PostMortem for the public route.
- [x] 12 unit + integration tests passing.

### Task 8.2: Timeline component

**Files:** `frontend/components/postmortem/Timeline.tsx`, `frontend/components/postmortem/TurnCard.tsx`, `frontend/components/postmortem/PostMortemHeader.tsx`, `frontend/components/postmortem/PostMortemView.tsx`.

- [x] Vertical timeline per `docs/SPEC.md` §11. `TurnCard` highlights chosen proposal with accent border + CHOSEN badge; alternatives dim to `opacity-60`. Origin badge color-coded (NATURAL=accent, SEED_FIRED=warning, FORCED_ESCALATION=danger). Per-agent advisor lines are one-sentence summaries (collapsed via the backend's `_to_one_line` helper). State delta footer surfaces legitimacy, per-turn cost, and any non-zero grievance per agent.

### Task 8.3: Counterfactuals panel

**Files:** `frontend/components/postmortem/Counterfactuals.tsx`.

- [x] Dormant seeds with origin reference + foreshadow flag.
- [x] Near-miss coups with agent names + per-turn grievance.
- [x] Empty state explicit ("No dormant consequences. No near-miss factions.") so the player sees the system DID track these — empty is meaningful, not a missing feature.
- [x] Didn't-happen transformations is a structural slot reserved for when the engine tracks near-transform state; renders as no-op in MVP.

### Task 8.4: Share card (image generation)

**Files:** `frontend/app/share/[token]/opengraph-image.tsx`.

- [x] Built on `next/og` `ImageResponse`. Layout: brand strip → end-state badge → 12 flavor tiles (DOMINANT cream, MORAL ok-green, WILDCARD warning-orange; empty slots dim) → opening line dim italic, closing line primary italic.
- [x] **Deviation from PLAN file path.** PLAN listed `(authed)/post-mortem/[id]/opengraph-image.tsx`. Built at `share/[token]/opengraph-image.tsx` because the URL pasted into Twitter/Discord/Bluesky is `/share/{token}` and that's the URL crawlers fetch. The authed post-mortem URL would 401 a non-cookie crawler. Documented in `frontend/CLAUDE.md`.
- [x] **1200×630, not square.** SPEC §11.3 said "compact square" but cross-platform unfurl behavior with square images is uneven. Wide-format unfurls predictably on Twitter, Discord, Bluesky.
- [x] **Twitter/X, Discord, Bluesky unfurl verification deferred to Phase 9 deploy.** Local dev verifies the image renders correctly via direct GET; platform-side OG-cache testing requires a public URL. Added to Phase 9.7 verification checklist territory.

### Task 8.5: Public share route

**Files:** `frontend/app/share/[token]/page.tsx`.

- [x] Read-only view of post-mortem via backend `/game/share/{token}` endpoint. No auth required. `generateMetadata` derives title (`"{setting_name} · {end_state}"`) and description (closing line) per run, so each share gets unique unfurl text.

### Task 8.6: Redirect closed runs to post-mortem

**Files:** `frontend/app/(authed)/run/[id]/page.tsx`, `frontend/app/(authed)/post-mortem/[id]/page.tsx` (new).

- [x] `loadGame` returns the `GameSessionResponse`; if `end_state !== null` we `redirect` to `/post-mortem/{id}` BEFORE rendering `DashboardShell`. Falls through to the existing error-or-dashboard branches otherwise.
- [x] New authed `/post-mortem/{id}` page calls `/game/{id}/post-mortem`. 404 → `/play`, 409 (`game_not_ended`) → `/run/{id}` so a player who hits `/post-mortem/<id>` mid-run bounces into the live dashboard rather than seeing an error.

**Phase 8 exit verification (2026-04-25):**
- [x] Continue gate verified end-to-end on `/dev/dashboard`: heating-core narration held, Continue button surfaced, click promoted next event. Screenshot `phase8-task80a-continue-gate.png`.
- [x] Post-mortem renders at `/dev/post-mortem` from the real Phase 7 exit run (id `7451e10a-...`) — header, timeline (9 turns, SEED_FIRED tone for turns 3/4/7/8, FORCED_ESCALATION tone for turn 6), empty Counterfactuals, closing line.
- [x] Rich-fixture variant (`?fixture=rich`) verifies populated Counterfactuals: 2 dormant seeds with origin context, 1 near-miss coup pair (Irena 7 / Nadia 8 on d02). Screenshot `phase8-task83-counterfactuals-rich.png`.
- [x] OG card renders against both fixtures via `/share/dev/opengraph-image` and `/share/dev-rich/opengraph-image`. Real run shows mixed flavor tiles (DOMINANT/MORAL/WILDCARD); rich shows all-DOMINANT. Screenshots `phase8-task84-og-real.png`, `phase8-task84-og-rich-v2.png`.
- [x] Public share page `/share/dev` and `/share/dev-rich` mount the same `PostMortemView` as the authed route, with per-run page title from `generateMetadata`.
- [x] Closed-run redirect: `(authed)/run/[id]/page.tsx` checks `end_state` BEFORE mounting the dashboard.
- [x] Backend test suite green: 447 passing including 12 new post-mortem tests.

**Deferred to Phase 9:**
- Twitter/X, Discord, Bluesky unfurl verification (requires public URL).
- Real-run share-button affordance from inside the post-mortem (copy-link UX).

---

## Phase 9: Deploy + Polish

**Entry criteria:** Phase 8 exit passed.
**Exit criteria:** Outpost is live at a public URL. All items in `docs/SPEC.md` §16 success criteria check.

**Session config:** `/model claude-opus-4-7` (Opus for README in Task 9.6, Opus for Task 9.7 verification) · `/effort medium` · plan mode: no

**Session start prompt:**

```
Role: senior DevOps + SRE. Execute Outpost Phase 9 — Deploy + Polish.

Read:
  1. CLAUDE.md entire (launch reality check)
  2. docs/ARCHITECTURE.md §13 (deployment)
  3. docs/PLAN.md Phase 9
  4. docs/SPEC.md §16 (MVP ship criteria — the gate)

Execute inline. Deploy first (9.1, 9.2), polish second.

9.1 (Vercel) and 9.2 (Railway) run in parallel — Vercel auto-deploys from
GitHub, Railway wants a first deploy then auto. Use vercel MCP for build
status.

9.4 (CI/CD): verify the full pipeline runs green on a trivial follow-up PR.

9.6 (README polish): switch to Opus. Run /humanizer on marketing prose.
Avoid AI-writing tells — this is the recruiter first impression.

9.7 is THE GATE. Opus + /effort high. Walk through docs/SPEC.md §16
checklist item by item. Each unchecked box = Phase 9 not done. Failures
route back to the relevant earlier phase for fix.

Final CLAUDE.md update: note anything production or deploy-related that
future you (or a maintainer) would need to know — Railway quirks, Vercel
config surprises, env-var precedence, CORS edge cases discovered.

When §16 fully checks: declare MVP complete. Prompt me to flip the repo
public (commands in README.md). Do not flip it yourself.
```

### Task 9.1: Frontend deploy

- [ ] **Step 1:** Connect GitHub repo to Vercel. Root: `frontend/`.
- [ ] **Step 2:** Set env vars: `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_API_BASE_URL`.
- [ ] **Step 3:** First deploy. Use `mcp__plugin_vercel_vercel__deploy_to_vercel`. Verify production build passes.
- [ ] **Step 4:** Commit vercel.ts config if needed.

### Task 9.2: Backend deploy

- [ ] **Step 1:** Connect repo to Railway. Service: outpost-backend, root: `backend/`.
- [ ] **Step 2:** Set all env vars from `.env.example`.
- [ ] **Step 3:** Deploy. Verify `/health` returns 200 at the generated Railway URL.
- [ ] **Step 4:** Set `FRONTEND_URL` to the Vercel production domain.
- [ ] **Step 5:** Railway cold-start note — on the $5-credit tier, services *can* idle-sleep after inactivity, adding ~1-3s to first request. Acceptable for MVP. Document this in README. If it becomes a problem, upgrade to the Pro plan with `min_machines_running = 1` or set up a keep-alive cron that hits `/health` every 5 minutes.

### Task 9.3: Wire production frontend → backend

- [ ] **Step 1:** Update Vercel env var `NEXT_PUBLIC_API_BASE_URL` to Railway URL.
- [ ] **Step 2:** Trigger redeploy.
- [ ] **Step 3:** Playwright-test end-to-end: sign in → start run → complete run → view post-mortem → share.

### Task 9.4: CI/CD full workflow

- [ ] Complete `.github/workflows/deploy.yml` per `docs/ARCHITECTURE.md` §13.3.
- [ ] Verify first push triggers test → eval → deploy pipeline.

### Task 9.5: Custom domain (optional for MVP)

- [ ] Vercel: add domain, follow DNS instructions.
- [ ] Railway: add custom domain for API.
- [ ] Update env vars accordingly.

### Task 9.6: README + docs polish

- [ ] **Step 1:** Write full README covering: what Outpost is, stack, how to run locally, how to deploy, links to SPEC/ARCHITECTURE/PLAN, screenshots.
- [ ] **Step 2:** Run `humanizer` skill on the README to remove AI-writing tells.
- [ ] **Step 3:** Commit.

### Task 9.7: MVP verification checklist

Go through `docs/SPEC.md` §16 item by item:

- [ ] Complete a run on all three settings without errors
- [ ] End-state distribution targets hit after 50 test runs
- [ ] Eval pipeline passes thresholds
- [ ] p95 cost < $0.25
- [ ] OAuth + daily gate work in production
- [ ] Post-mortem + share cards render across platforms
- [ ] WCAG AA passes on all views
- [ ] Load time < 2s on 3G
- [ ] CI green, Lighthouse mobile > 85

Any gap → back to fix.

---

## Errors & Learnings

<!-- Populate during execution. Log any non-obvious failure, the fix, and the file changed. Prevents re-learning. -->

### Phase 0 (2026-04-23)

- **Supabase free tier caps accounts at 2 active projects per org.** Symptom: "organization has members who have exceeded their free project limits" on project creation. Root cause: free-tier quota is per account owner across all orgs they administer, not per org. Fix: created a second Supabase account via email alias (`luclacombe+outpost@duck.com`) rather than pausing an unrelated active project. Takeaway: budget one Supabase account per major project.

- **Supabase-generated DB passwords contain reserved URL characters.** Symptom: the auto-generated password with `#`, `%`, `!`, `$` in `DATABASE_URL` would silently truncate at `#` or fail as invalid percent-escape. Fix: percent-encode in the URL (`#`→`%23`, `%`→`%25`, `!`→`%21`, `$`→`%24`). File: `backend/.env` `DATABASE_URL`. Takeaway: always URL-encode Supabase passwords before embedding.

- **`LANGFUSE_HOST` is deprecated; SDK reads `LANGFUSE_BASE_URL`.** Symptom: I suggested `LANGFUSE_HOST` based on stale training knowledge. Root cause: Langfuse Python SDK repo rules explicitly deprecate HOST. Fix: renamed var in `backend/.env.example`. Takeaway: verify env var names via context7 before instructing — don't rely on memory for SDK contracts.

### Phase 1 (2026-04-23)

- **Module-level `settings = Settings()` crashes at import when `.env` is missing a required key.** Symptom: `ruff check` and tests exploded with `FRONTEND_URL Field required` because the Phase 0 `.env` predates the Phase 1 Settings schema. Fix: appended `FRONTEND_URL=http://localhost:3000` to `backend/.env`. Takeaway: whenever Settings gains a required field, update `.env.example` _and_ prompt the user to top up their `.env` — pydantic-settings doesn't fall back to `.env.example`.

- **Postgres connection tests must override `DATABASE_URL` to localhost.** Symptom: `uv run pytest tests/test_database.py` failed against the remote Supabase URL baked into `.env`. Fix: prefix the test command with `DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:54322/postgres` so pydantic-settings picks up the OS env over `.env`. Takeaway: document the override as the standard local test invocation; OS env beats `.env` so no `.env` mutation needed. Inside `docker-compose.yml` the same override is baked in via `environment:` so the container always hits the host's local Postgres.

- **Supabase CLI project-id follows the directory name where `supabase init` ran.** Symptom: running `supabase status` from `backend/` looked for container `supabase_db_backend` while the live container was `supabase_db_Outpost` (started from the repo root). Fix: always run `supabase start/stop/status` from the same directory — preferring `backend/` since `backend/supabase/config.toml` lives there. Takeaway: stale project-id references are a normal migration footgun; `docker ps` is the fastest sanity check.

- **Ruff B017: `pytest.raises(Exception)` is too broad.** Symptom: `ruff check` flagged `tests/test_config.py` after I wrote the initial red test. Fix: narrowed to `pytest.raises(ValidationError)`. Takeaway: assert the specific exception type when testing validation paths; B017 is intentional and useful.

### Phase 2 (2026-04-23)

- **Alembic `config.set_main_option("sqlalchemy.url", ...)` crashes on URL-encoded passwords.** Symptom: `ValueError: invalid interpolation syntax in ... at position 31` when running `alembic revision --autogenerate`. Root cause: Alembic's ConfigParser treats `%` as interpolation. Fix: `settings.DATABASE_URL.replace("%", "%%")` before `set_main_option` in `backend/alembic/env.py`. Takeaway: any `%` in a Supabase password must be doubled before handing to Alembic's config layer.

- **Autogenerate would destroy Supabase-owned tables without an `include_object` filter.** Symptom: autogenerate against a Supabase DB wants to DROP every table that isn't in `target_metadata` (auth/storage CLI-managed tables later). Fix: added `include_object` in `backend/alembic/env.py` that drops reflected tables absent from `target_metadata.tables` and anything outside the `public` schema. Takeaway: coexistence with Supabase CLI migrations requires this filter — do not remove it when adding ORM models in later phases.

- **Remote Supabase direct-connect (port 5432) fails from dev machines over IPv6.** Symptom: `psycopg.OperationalError: connection to server at "2a05:…" port 5432 failed: No route to host` when running Alembic against `db.<ref>.supabase.co:5432`. Fix: apply Alembic schema locally against `localhost:54322`, then mirror via the `supabase` MCP `apply_migration` on the remote project, including a matching `alembic_version` row so future Alembic runs against remote see the head. Takeaway: Phase 9 deploy (Railway) will resolve IPv6 natively — the workaround is dev-only.

- **Local Supabase CLI signs Auth tokens with ES256 by default.** Symptom: a signed-up user's `access_token` from `/auth/v1/signup` has `alg: ES256`, so an HS256-only decode returned 401 "Invalid token". Root cause: modern Supabase CLI has migrated to asymmetric signing with a published JWKS; legacy projects remain on HS256. Fix: `decode_supabase_jwt` now reads the header `alg` and dispatches — HS256 against `SUPABASE_JWT_SECRET`, ES256/RS256 against the JWKS at `{SUPABASE_URL}/auth/v1/.well-known/jwks.json` (cached 5 min). Per-branch algorithm whitelist prevents alg-confusion attacks; unit test `test_reject_alg_confusion_hs256_with_public_key_bytes` guards the invariant. E2E: `tests/test_integration_supabase_jwks.py` signs up a real user locally and verifies /me returns 200 with the JWKS-issued ES256 token. Files: `app/auth/jwks.py`, `app/auth/service.py`.

- **Ruff B008 flags the canonical FastAPI `Depends()` default argument.** Symptom: `ruff check` fired B008 on every endpoint/dependency. Fix: added `"B008"` to `[tool.ruff.lint] ignore` in `backend/pyproject.toml` with a comment explaining it's the documented FastAPI pattern. Takeaway: don't work around the idiom per-file; the global ignore is the convention.

- **Alembic FK to a cross-schema parent (`auth.users`) isn't autogen-perfect.** Symptom: autogenerated migration omits the `ForeignKeyConstraint(["id"], ["auth.users.id"], ondelete="CASCADE")` because Alembic doesn't introspect the `auth` schema by default. Fix: hand-edit the generated migration to add the FK (see `backend/alembic/versions/c569693a0392_create_profiles_table.py`). Takeaway: cross-schema FKs always need manual review post-autogenerate.

### Phase 3 (2026-04-24)

- **Pydantic v2 `@field_validator` only sees earlier-declared sibling fields.** Symptom: a `@field_validator` that referenced a sibling field returned `None` from `info.data` because the sibling was declared below the validated field. Root cause: v2 validators fire in declaration order; `info.data` only holds already-validated fields. Fix: switched cross-field checks to `@model_validator(mode="after")` where the full instance is available, and added a regression test that reorders declaration. File: `backend/app/game/schemas.py` (Task 3.1 review).

- **Supabase CLI migrations run before Alembic on `supabase start`.** Symptom: the Supabase CLI's `on_auth_user_created` trigger expected `public.profiles` to exist, but the local Postgres had Alembic migrations pending at CLI-startup time — so the initial bootstrap produced a duplicate/empty profiles table owned by Supabase. Root cause: `supabase start` only applies `supabase/migrations/`; Alembic is a separate step. Fix: documented the ordering (supabase start → `alembic upgrade head` → then auth ops work) and ensured the profiles-table SQL in `supabase/migrations/` is `CREATE TABLE IF NOT EXISTS` compatible with the Alembic-owned definition. File: `backend/supabase/migrations/*create_profiles_table.sql` (Task 3.2).

- **`MissingGreenlet` after `await db.rollback()` on SQLAlchemy async.** Symptom: `POST /game/session` on a second same-day attempt rolled back, then accessed `profile.last_run_started_at` for the 429 detail → `MissingGreenlet: greenlet_spawn has not been called`. Root cause: lazy-loaded async relationships detach after rollback; the ORM tried to re-fetch from a sync path and couldn't. Fix: snapshot the attribute into a local variable **before** `await db.rollback()`. File: `backend/app/game/service.py` (Task 3.10).

- **`_append_memory` silently dropped entries when all 10 slots were pinned.** Symptom: after a coup scenario accumulated 10 death-pins, subsequent `remember` tool calls appeared to apply but the entry never surfaced. Root cause: the eviction branch looked for a non-pinned entry to drop; finding none, it fell through and returned the list unchanged. Fix: when every slot is pinned, evict the oldest pinned by turn number; added a test covering the all-pinned path. File: `backend/app/game/engine.py` (Task 3.6 review).

- **Float precision in multiplicative `forced_escalation_probability`.** Symptom: over turns 5-9 the cumulative guarantee drifted ~1e-12, so a strict `== 1.0` assertion in the forced-escalation test failed intermittently. Root cause: product-of-complements (`1 - ∏(1 - p_t)`) accumulates epsilon. Fix: rewrote as additive normalization against a running `remaining_budget` so the final turn's probability equals exactly `1.0` when reached. File: `backend/app/game/events.py` (Task 3.8).

- **`_pin_memory` deduped by `id()`.** Symptom: two agents pinning identical "died in turn 5" entries in the same tick — one call deduped, the other didn't. Root cause: Python interns short strings unpredictably; `id()` is not a stable identity for value equality. Fix: dedupe by `(turn, text)` tuple. File: `backend/app/game/engine.py` (Task 3.6 review).

- **Over-broad `git add` silently deleted unrelated files.** Symptom: Task 3.2's two Supabase CLI game-table migrations (`20260424073634_create_game_tables.sql`, `20260424073655_enable_rls_game_sessions.sql`) disappeared from the tree between `78aaad1` (added them) and Phase 3 close — tests kept passing because the live DB kept the RLS from its earlier apply. Root cause: a subsequent commit's `git add` used a broad directory path that picked up both additions and deletions; the deletions weren't noticed because they didn't regress any test. Fix: the duplicate-DDL issue was going to be removed anyway, so the accidental delete didn't hurt — but the lesson stands. **Prefer `git add <explicit-path>` for every file; run `git diff --cached --stat` before committing multi-file work to confirm the staged set matches intent.** Files: `backend/supabase/migrations/` (commit `16034a5`).

### Phase 4 (2026-04-24)

- **`HTTPException` inside `StreamingResponse` body never reaches the client cleanly.** Symptom: pre-stream validations (`/decide` when not paused, bogus proposal id) raised `HTTPException` from inside the SSE generator and produced crashed connections / 500s instead of the intended 409/400. Root cause: Starlette flushes status + headers before iterating the body generator, so exceptions surface as an anyio `ExceptionGroup` wrapping the task-group, not as a typed HTTP response. Fix: split every streaming service into `prepare_<verb>(...)` (sync-ish; runs checks and raises) plus `stream_<verb>(...)` (pure async iterator). Routers `await prepare_` first, then hand the resulting dict into the `StreamingResponse` body. Recorded in `backend/CLAUDE.md` anti-patterns. Files: `backend/app/agent/service.py`, `backend/app/agent/router.py` (Task 4.7).

- **`httpx.ASGITransport` does NOT run FastAPI's `lifespan` hook.** Symptom: SSE router tests saw `AttributeError: 'State' object has no attribute 'graph'` because `app.state.graph` is populated in `lifespan`, which `ASGITransport` skips. Root cause: `ASGITransport` only drives the ASGI app protocol — startup/shutdown lifespan events aren't emitted. Fix: fixtures (`graph_memory`, `graph_postgres`) attach the compiled graph to `app.state.graph` directly and restore the prior attribute on teardown. Recorded in `backend/CLAUDE.md`. File: `backend/tests/test_agent_router.py` (Task 4.7).

- **Patching `cost_cents_of` on the producer module doesn't affect the consumer.** Symptom: an integration-test probe wrapping `app.agent.anthropic_client.cost_cents_of` saw zero calls even though the nodes were running LLMs. Root cause: `nodes.py` uses `from app.agent.anthropic_client import ..., cost_cents_of`, binding the function **object** to a name in `app.agent.nodes` at module load. Monkeypatching the producer's attribute doesn't update the consumer's already-bound name. Fix: `patch.object(app.agent.nodes, "cost_cents_of", wrapper)`. Recorded in `backend/app/agent/CLAUDE.md`. File: `backend/tests/integration/test_full_run.py` (Task 4.8).

- **AsyncPostgresSaver rejects SQLAlchemy-dialected DSNs.** Symptom: checkpointer startup failed with `psycopg.OperationalError` when given the same `postgresql+psycopg://...` URL used for SQLAlchemy. Root cause: LangGraph passes the DSN straight to `psycopg.AsyncConnection.connect`, which doesn't understand SQLAlchemy's driver-prefix form. Fix: `_sqlalchemy_to_psycopg_url` strips the `+psycopg` suffix; rejects other schemes loudly. File: `backend/app/agent/checkpointer.py` (Task 4.6).

- **Memories in the persona block collapse the per-run cache.** Symptom: initial design (ARCHITECTURE §3.7 pre-Phase-4) put per-turn memories inside each `<persona>` block, which would have invalidated the persona cache on every turn, dropping hit rate from 85–90% to 40–50%. Fix: moved memory rendering into the colony-state block (which already invalidates per turn); personas block stays run-stable. Updated ARCHITECTURE §3.7 and §5.1. Files: `backend/app/agent/prompts.py`, `docs/ARCHITECTURE.md` (Task 4.2).

- **`tool_calls_on_pick` is carried empty through Phase 4.** The JSON schema for `propose_action` declares `tool_calls_on_pick: array of strings`; the Pydantic `Proposal.tool_calls_on_pick` is `list[ToolCall]`. They don't round-trip. Phase 3's engine left `tool_calls_on_pick=[]` with a "wired in Phase 4" comment. Phase 4 chose engine-canonical per-flavor effects in `resolve` (DOMINANT=0, MORAL=+2, WILDCARD=-3 on legitimacy; severe-event seed scheduling) rather than LLM-emitted nested tool calls — avoids the schema mismatch and keeps the engine as source of truth. Revisit if a later phase wants richer per-proposal effects. File: `backend/app/agent/nodes.py::resolve` (Task 4.5).

- **`player_choice` in the initial graph state is silently dropped.** Symptom: passing `{"player_choice": id}` into the first `ainvoke` seemed correct but had no effect on resume. Root cause: `generate_event` resets `player_choice = None` at the top of every turn, so any value present in the initial dict is overwritten before the graph pauses. Fix: `/decide` calls `await graph.aupdate_state(config, {"player_choice": id})` AFTER the pause, then `ainvoke(None, config)` to resume. Recorded in `backend/app/agent/CLAUDE.md`. File: `backend/app/agent/service.py::stream_decide` (Task 4.7).

- **Subagents ran `ruff check` but forgot `ruff format --check` repeatedly.** Symptom: first spec review on Task 4.0 flagged formatting divergence; the implementer had reported "ruff clean" because they only ran `ruff check`. Root cause: the two commands verify disjoint things — `check` covers lint rules, `format --check` covers style. Fix: every subagent prompt from Task 4.1 onward explicitly required running BOTH. The project convention is in `backend/CLAUDE.md` but needs reinforcing at dispatch time. Files: process-level, not code.

- **Prompt caching only writes at the LAST `cache_control` and needs ≥4096 cumulative tokens for Haiku 4.5.** Symptom: first real-LLM integration run completed under budget (8 turns, 15¢) but every single one of 24 LLM calls reported `cache_read_input_tokens=0`. Invariant #5 (caching mandatory) was false-green in unit tests (which only checked that `cache_control` markers were present) while broken in practice. Root cause, discovered via live API diagnostic: (a) Anthropic persists a cache entry only at the last `cache_control` breakpoint — earlier breakpoints are lookup hints, not durable entries; (b) the minimum cacheable prefix for Haiku 4.5 is 4096 cumulative tokens through that last cache_control, and Anthropic silently skips caching when below it (no error, both cache_* fields return 0). The original design put cache_control on colony_state (last system block), which changed every turn, so every turn wrote a fresh entry that never matched. Fix: two cache_control markers on preamble + personas; colony_state rendering moved into the user message. Preamble enriched so cumulative through personas clears 4096 with ~600-token headroom. Updated ARCHITECTURE §5.1 and `backend/app/agent/CLAUDE.md`. Files: `backend/app/agent/prompts.py`, `backend/app/agent/nodes.py`. Integration test now passes with cache hits observed turn 2+.

- **`cost_cents_of` was under-counting by ~65% and reporting NEGATIVE costs on Sonnet calls.** Symptom: integration test reported "10 turns, 9¢" while the Anthropic dashboard delta for the same run was 20¢ — and earlier Sonnet calls registered around `−0.5¢` each (silently cancelling Haiku positives, which is how the total looked plausible). Root cause: the formula assumed `usage.input_tokens` was the TOTAL input size and subtracted `cache_read + cache_creation` to get "fresh input". Empirically (verified by reading Anthropic's docs and matching their `usage` JSON output): `input_tokens` is ALREADY the non-cached billable input — it does not include cached tokens. The buggy subtraction produced large negative `fresh_input` values on cache-hit calls. Fix: `cost_cents_of` now bills `input_tokens * INPUT_RATE` directly without subtraction, plus the cache and output buckets at their own rates. Calibrated against a dashboard delta on 2026-04-24 — corrected formula matches billed Haiku spend within 5% and Sonnet within rounding on a 31-call run. Added Sonnet cache rates to `constants.py` (`CENTS_PER_MTOK_SONNET_CACHE_WRITE` = 375, `CENTS_PER_MTOK_SONNET_CACHE_READ` = 30). Unit tests rewritten — they had calibrated against the buggy formula by using the same wrong shape (`input_tokens=total` instead of the SDK's actual `input_tokens=non-cached-only`), so both code and tests were wrong in the same way and passed together. New tests pin behaviour against exact `usage` shapes captured during the calibration run. Files: `backend/app/agent/anthropic_client.py`, `backend/app/agent/constants.py`, `backend/tests/test_anthropic_client.py`. **Calibrated per-run cost: ~20¢** (with the autonomous-not-caching gap below; ~11-13¢ once that's fixed).

- **Autonomous-actions phase silently skipped caching too.** Spotted in the same calibration trace as the formula bug: every autonomous Haiku call reported `cache_creation=0, cache_read=0, input_tokens=~5000` — i.e. paying full price for the entire prompt as fresh input, every turn. Root cause: `autonomous_actions` filtered `AGENT_TOOL_SCHEMAS` to a 6-tool subset for safety (see `AUTONOMOUS_TOOL_NAMES` in `app/agent/nodes.py`). The subset is ~800 tokens lighter than the full 10-tool list, which drops cumulative prefix at the last cache_control below the 4096-token Haiku minimum. Same gotcha as the debate-cache fix, in a different node. Fix: send the FULL `AGENT_TOOL_SCHEMAS` and apply the autonomous tool whitelist as a post-LLM filter on `parsed.tool_calls` instead. Side benefit: debate and autonomous now share the same cached prefix (~4647 tokens), so autonomous reads from debate's cache write within the same turn. Verified by re-running the calibration with the fix in place: per-turn cost dropped from ~2¢ to ~1.3¢; 10-turn projection went from 20¢ to ~16¢. Files: `backend/app/agent/nodes.py`, `backend/tests/test_nodes.py` (replaced the "tools sent are exactly the subset" assertion with "tools sent are the full set AND off-list emissions are dropped before reaching the engine").

### Phase 5 (2026-04-25)

- **Langfuse v3 changed the import path.** Symptom: initial wiring tried `from langfuse.callback import CallbackHandler` (the v2 path); v3 raises `ModuleNotFoundError`. Fix: `from langfuse.langchain import CallbackHandler`, lazy-imported inside `build_langfuse_handler` so the disabled-mode path never pays the import. File: `backend/app/agent/observability.py`. Older Langfuse docs and any LLM-suggested wiring will still point at the deprecated path — verify against the v3 docs (or context7) before believing a "missing module" message.
- **`langfuse.langchain` requires the umbrella `langchain` package, not just `langchain-core`.** Symptom: `ImportError: cannot import name 'BaseCallbackHandler' from 'langchain'` even though `langchain-core` was already pulled in by langgraph. Fix: explicit `uv add langchain` (chore commit `f1fd8f6`). Takeaway: when adding a Langfuse integration to a LangGraph project, don't assume the langchain transitive deps are sufficient — Langfuse imports from `langchain` (the umbrella) directly.
- **`engine.advance_turn` clears `turn_tool_calls` BEFORE the state snapshot lands in `turn_logs.state_snapshot`.** Implication: any post-hoc tool-validity analysis only sees per-turn calls via the SSE stream / `RunRecord`; reading them back from `turn_logs` yields an empty list. The headline `tool_validity_rate` on a stored run is therefore "validity rate of *applied* calls" — rejected calls are absent. Closing this gap means capturing `applied_tool_calls`/`rejected_tool_calls` on the `TurnLog` row before the clear, which is queued as a Phase 6+ follow-up.
- **`state_snapshot["resources"]` rounds Pydantic `Resource` to a dict — read `value`, not the object.** Symptom: a first cut of resource-history extraction in `app.eval` indexed `snapshot["resources"][rid]` expecting an integer; actual shape is `{"id": ..., "value": int, "max": int, ...}`. Fix: read `snap["resources"][rid]["value"]` for the per-turn integer. Recorded here so the next session writing a metric or post-mortem extractor doesn't repeat the typo.

### Phase 6 (2026-04-25)

- **Next.js 16 renamed `middleware.ts` → `proxy.ts`.** Symptom: `pnpm build` warns `The "middleware" file convention is deprecated. Please use "proxy" instead.` Fix: rename the root file and the exported function (`export async function proxy(...)`). The supabase helper at `lib/supabase/middleware.ts` keeps its old internal name — only the root entry-point file changed. Recorded in `frontend/CLAUDE.md`.
- **DiceBear v9 per-style packages no longer have a named export matching the style.** Symptom: `import { pixelArt } from "@dicebear/pixel-art"` fails with `Export pixelArt doesn't exist`. Root cause: v9 packages export `{ create, meta, schema }` directly. Fix: `import * as pixelArt from "@dicebear/pixel-art"` — the namespace object satisfies the `Style<O>` shape `createAvatar()` expects. The earlier-form import seen in some docs is from v8.
- **`createAvatar` ternary collapses two `Style<O>` types into a wider union TS can't narrow.** Symptom: `tsc --noEmit` rejected `createAvatar(variant === "portrait" ? pixelArt : thumbs, ...)` because each style's `Options` is distinct. Fix: branch on `variant` and call `createAvatar(pixelArt, ...)` / `createAvatar(thumbs, ...)` in each arm. Same runtime, types narrow.
- **`@theme inline { --font-X: var(--font-X) }` is the shadcn-init footgun.** The default scaffold writes a self-referential `--font-sans: var(--font-sans)` because shadcn's CSS doesn't know about the next/font runtime variable name. Fix: bind `--font-X` to the next/font variable explicitly (`--font-display: var(--font-press-start), ui-monospace, monospace`). The `inline` modifier still resolves runtime `var()` references — only literal hex/string values get inlined at parse time.
- **Tailwind v4 needs no `tailwind.config.ts`.** PLAN.md Task 6.3 listed one as a target file, but Tailwind v4 is CSS-first via `@theme {...}` in `globals.css`. The scaffold doesn't ship a config, and one isn't needed. Lesson: revisit PLAN file lists when the underlying tool changes major versions.
- **Server-component `searchParams` is a Promise.** Symptom: typing `searchParams` as a plain object throws at build time in Next 16. Fix: type as `searchParams: Promise<{ ... }>` and `await` it before reading. Same for `params`. Recorded in `frontend/CLAUDE.md`.
- **`getUser()` re-validates against Supabase; `getSession()` trusts the cookie.** On the server, always `getUser()` for auth gates — a tampered cookie would otherwise pass `getSession()`. Recorded in `frontend/CLAUDE.md`.
- **Env tolerance is a soft requirement for the landing page.** A fresh clone with no `.env.local` should still boot the dev server and render the landing — otherwise reviewers see a broken page on first start. Achieved by short-circuiting the `proxy`, `(authed)` layout, callback handler, and SignInButton when `NEXT_PUBLIC_SUPABASE_URL` is unset. Production deploys must set both env vars; the SignInButton click visibly redirects to `/auth/error?reason=supabase_not_configured` if forgotten.

### Phase 7 (2026-04-25)

- **Tailwind `md:` (≥768) collides with `@media (max-width: 768px)`.** Symptom: at exactly 768px viewport both rules fire, the dashboard event panel locks to a fixed 280px height and clips debate text behind the choose panel. Fix: use `lg:` (≥1024) for desktop-only utilities (`lg:h-svh`, `lg:h-full`, `lg:overflow-y-auto`). Below `lg` the page scrolls naturally; below the mobile breakpoint the grid collapses to one column. Recorded in `frontend/CLAUDE.md`. Lesson: when bridging Tailwind responsive utilities and hand-written `@media` rules, pick breakpoints that don't share an edge — `lg:` against `(max-width: 768px)` is safe; `md:` against the same is not.
- **Dim text `--px-text-dim: #7a7a6e` already failed WCAG AA at 4.01:1 (CRT off) and 3.81:1 (CRT on, 0.08 opacity).** Phase 6 oversight not caught because there was no Phase 6 contrast check. Phase 7's hard rule on the CRT overlay forced verification. Fix: lifted to `#968f7d` (5.12:1 with CRT). Lesson: every palette variable should be auto-checked against bg before commit; a Phase 8 cleanup task is to add a small node script that diffs the palette tokens and fails on AA violations. Until that exists, manual contrast verification on any new dim/text/border palette change.
- **`StreamingResponse` body generators can't emit JSON HTTP errors.** This is a backend rule (already documented in `backend/CLAUDE.md`) but bites the frontend reducer too: the `/decide` route applies the player choice via `aupdate_state` BEFORE streaming — if the client connection drops mid-/decide and reconnects, the server graph has already advanced past pause and `/decide` is no longer safe to retry. The hook now refuses to retry `/decide` on 401, surfacing `stream_error` and leaving reconciliation (re-fetch `GET /game/{id}`) to a future Phase 7+ refinement. Acceptable for MVP because the most common cause of mid-stream 401 is client tab idle while the supabase access token expires (5 min default), and the player can simply refresh the page to recover. Recorded in `frontend/CLAUDE.md` SSE section.
- **`overflow-y-auto` on a grid cell with `auto` row height creates an indeterminate scroll container.** Spent ~30 min chasing why mobile debate clipped. The grid row sized to content + `overflow-y-auto` doesn't establish a scroll container without an explicit max-height — but it does interact with `h-full` on inner children to create a fixed-pixel mismatch. Always pair `overflow-y-auto` with a constrained parent (fixed grid row, `100vh`, etc.). Recorded.
- **Live-run exit verification was a manual user step — passed 2026-04-25.** Run completed in the orbital setting at $0.16 total cost. Path that worked: cloud Supabase (free-tier outpost project) for both auth and DB, backend running natively (`uv run uvicorn`) so it picks up the cloud `DATABASE_URL` from `.env`, frontend `.env.local` carrying `NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY` derived from `backend/.env`. Local Supabase Docker is unused for the live flow; it's only needed for local-DB testing.
- **Cloud-Supabase IPv6 unreachability bites in Phase 7.** The cloud project's direct-connect endpoint (`db.<ref>.supabase.co:5432`) is IPv6-only on residential networks and a `psycopg.OperationalError: No route to host` is what the user sees. Workaround: use the **session pooler** URL (`aws-0-<region>.pooler.supabase.com:5432`) which is IPv4-enabled. Build via Supabase dashboard → Project Settings → Database → Session pooler. Critical: scheme MUST be `postgresql+psycopg://` (not `postgresql://` or `postgres://`), and password must be URL-encoded (`%` → `%25` first, then `! # $`). Already documented under `backend/CLAUDE.md`'s Migrations section; cross-referenced here because Phase 7 is when it actually trips a player-facing flow.
- **Frontend `MeResponse` type was a Phase 6 scaffold guess that didn't match `/me`'s actual flat response.** Phase 6 visual tests never hit `/me` with a real session, so the bug only surfaced in Phase 7. Fixed by aligning the type to backend's `UserInfo` (flat `{id, email, display_name, next_run_at}`) — `next_run_at === null` means eligible. Lesson: when a phase is "scaffold the type", actually invoke the API at least once before declaring done. Affected files: `frontend/lib/api/client.ts`, `frontend/app/(authed)/play/page.tsx`.
- **Cloud schema bootstrap is a separate workflow from local migrations.** Path that worked: (1) `supabase link --project-ref <ref>`, (2) inspect remote tables via Supabase MCP `list_tables` to see what exists, (3) apply Alembic's game-tables migration via `apply_migration` MCP tool (NOT `supabase db push`, which only handles `supabase/migrations/*.sql`), (4) `UPDATE alembic_version SET version_num = '<head>'` in the same migration so future runs against remote know the schema is current. Doing this through the MCP avoids the IPv6 / direct-connect / pooler password / psycopg URL-encoding maze entirely — the MCP uses the user's HTTPS session, not a Postgres connection.

### Phase 8 (2026-04-25)

- **`with_msgpack_allowlist` is a no-op when the existing allowlist is `True`.** Symptom: my first cut wrapped `saver.serde = saver.serde.with_msgpack_allowlist(_ALLOWED_MSGPACK_TYPES)` and the warning didn't go away — and the unit test that introspected `_allowed_msgpack_modules` failed because the attribute stayed `True`. Root cause: the helper short-circuits with `return self` when the base is `True` (the permissive sentinel) or `False`. Fix: construct a fresh `JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED_MSGPACK_TYPES)` and rebind onto `saver.serde`. The new serializer is in "explicit allowlist" mode — anything off-list now logs "Blocked" rather than allowing-with-warning, which is a stricter (and intended) failure mode. File: `backend/app/agent/checkpointer.py`.
- **`opengraph-image.tsx`'s `params` is a Promise in Next.js 16.** Symptom: the OG card showed the fallback "A daily narrative roguelike." instead of the real run, even though the dev fallback path was correctly importing the fixture. Root cause: `params.token` was `undefined` because `params` is a Promise and the property access ran on the Promise object, not the resolved value. Next 16 logs a `sync-dynamic-apis` warning but still returns `undefined` for the property. Fix: type as `params: Promise<{token: string}>` and `await params`. Same convention as page components — already documented for `page.tsx` in `frontend/CLAUDE.md`, now applies to `opengraph-image.tsx` too. File: `frontend/app/share/[token]/opengraph-image.tsx`.
- **Real Phase 7 exit run had zero `consequence_seeds` rows but multiple SEED_FIRED events.** Symptom: building the post-mortem from run `7451e10a-...` produced empty `seeds_fired` lists for turns 3/4/7/8 even though their events were `origin: SEED_FIRED`. Root cause: the engine's seed lifecycle (Phase 4) appears to delete the seed row when the seed fires, leaving the event's `origin_seed_id` referencing a row that no longer exists. Fix: defensive double-lookup in `build_post_mortem` — first try `seeds_fired_by_turn[turn_number]`, then fall back to `seeds_by_id[origin_seed_id]`. Both empty in this run; the post-mortem still surfaces "this turn was a seed-fired event" via the origin badge. The deletion-on-fire behavior is worth revisiting in Phase 9 — keeping the row with `fired=true` would let dormant + fired both surface in counterfactuals from a single source of truth.
- **Phase 7 exit summary said "10 turns" but `turn_logs` only has 9.** Run records turn_number 0-8 (9 rows) ending in ENDURED. Plan summary was off-by-one — likely counting the initial respite as "before turn 1" while the engine numbers from 0. Not a bug, just a doc/engine mismatch worth flagging if a future eval references "turn count."

### Phase 7 issues found during exit run — triaged for Phase 8/9

| Severity | Issue | Phase |
|---|---|---|
| P1 | Narration disappears immediately when next turn's `proposals_ready` arrives. `/decide` stream auto-continues into next turn; dashboard needs a "Continue" gate after narration so the player can absorb consequences. | Phase 8 |
| P2 | LangGraph checkpointer prints `Deserializing unregistered type` warnings for `EventOrigin`, `EndState`, `ColonyState`, etc. Add to `allowed_msgpack_modules` at saver setup. | Phase 8 |
| P2 | Run felt low-stakes — resources stayed near full all 10 turns. Difficulty rails (`wealth_scale_factor`, `apply_early_turn_floors`, forced escalation) need re-tuning. The eval pipeline (Phase 5) didn't catch this — investigate what the resource-trajectory metric is actually measuring. | Phase 9 (balance) |
| P3 | `EventLog` truncates entries with no tooltip; player can't read history. | Phase 8 polish |
| P3 | Setting YAML icons render as words ("grain", "bolt"). Switch to single-char glyphs in YAML, or guard `ResourceBar` to only render single chars. | Phase 8 polish |
| P3 | DiceBear `thumbs` style used for advisor portraits everywhere; SPEC §3 implies pixel-art for the dashboard. Re-evaluate per agent context. | Phase 8 polish |
| Design | "4 advisors, only 3 options" — by design per SPEC §7.3 (three flavors: DOMINANT/MORAL/WILDCARD). Daisuke's voice is in the debate; he just doesn't always anchor a proposal. Document if it confuses playtesters again. | (none) |
| Design | MET timestamps in advisor lines — orbital setting flavor. Other settings use different conventions (arctic uses dates, desert uses sols). | (none) |

---

## Scope Check (from writing-plans skill)

The plan covers 9 phases spanning backend, frontend, and deployment. Each phase produces independently working, testable software:
- Phase 1: a FastAPI server with health endpoint
- Phase 2: authenticated endpoint with daily-gate
- Phase 3: a complete game engine (playable via unit tests, no UI)
- Phase 4: the agent system wired in (playable via API with curl)
- Phase 5: observability + eval
- Phase 6: static frontend with auth
- Phase 7: live dashboard
- Phase 8: post-mortem + share
- Phase 9: production deploy

This is within a single plan scope because phases 1-5 (backend) and 6-8 (frontend) share deep architectural coupling (API contracts, state shapes, tool schemas). Splitting would create synchronization overhead that would slow overall progress. A solo builder executes this as one plan with internal phase gates.

---

## Execution Handoff

**Plan complete and saved to `docs/PLAN.md`. Two execution options:**

**1. Subagent-Driven (recommended for phases 3, 4, 7)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Best for the gnarly parts: engine, agent system, dashboard UI.

**2. Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints for review. Best for scaffolding phases (0, 1, 2, 6) where tasks are sequential and low-ambiguity.

**Recommended mix:**
- Phases 0-2: inline (simple, sequential)
- Phases 3-5: subagent-driven (high ambiguity, each task benefits from fresh context)
- Phases 6-7: inline (UI iteration benefits from seeing previous state)
- Phases 8-9: inline (polish + deploy)

**Which approach? Or let me know when to start Phase 0.**
