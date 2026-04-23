# Research: FastAPI + Docker + Supabase Integration

> Research agent output from 2026-04-23. Source for backend stack decisions. Consulted when making decisions in `docs/ARCHITECTURE.md` sections 6, 7, 9, 13 and `docs/PLAN.md` phases 1-2.

## FastAPI Project Structure (Feature-Scoped, 2026 Best Practice)

```
outpost-backend/
├── app/
│   ├── main.py                  # App factory, middleware registration
│   ├── config.py                # Pydantic Settings (reads .env)
│   ├── database.py              # Async SQLAlchemy engine + session factory
│   ├── dependencies.py          # Shared deps: get_db, get_current_user
│   │
│   ├── auth/
│   │   ├── router.py            # POST /auth/verify
│   │   ├── schemas.py           # TokenPayload, UserInfo
│   │   ├── dependencies.py      # require_auth() dependency
│   │   └── service.py           # JWT decode, user lookup
│   │
│   ├── game/
│   │   ├── router.py            # POST /game/session, GET /game/{id}/state
│   │   ├── schemas.py           # GameSession, TurnRequest, TurnResponse
│   │   ├── models.py            # SQLAlchemy ORM models
│   │   ├── service.py           # Turn resolution engine, state mutations
│   │   ├── dependencies.py      # get_game_session dep
│   │   └── exceptions.py        # GameNotFound, InvalidTurnError
│   │
│   ├── agent/
│   │   ├── router.py            # POST /agent/turn (streaming SSE)
│   │   ├── schemas.py           # AgentInput, AgentEvent
│   │   ├── graph.py             # LangGraph StateGraph definition
│   │   ├── nodes.py             # Individual agent node functions
│   │   └── service.py           # Orchestrates graph.astream()
│   │
│   └── middleware/
│       ├── auth.py              # JWT extraction
│       ├── logging.py           # Structured request logging
│       └── rate_limit.py        # Per-user/IP rate limiting
│
├── tests/
│   ├── conftest.py              # pytest fixtures: async client, DB override
│   └── test_*.py
│
├── alembic/                     # Python-side migration management
├── Dockerfile
├── Dockerfile.dev
├── docker-compose.yml
├── pyproject.toml               # uv + ruff + pytest config
└── .env
```

## Dependency Injection Patterns

```python
# app/dependencies.py
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import async_session_factory
from app.auth.service import decode_supabase_jwt

bearer_scheme = HTTPBearer()

async def get_db() -> AsyncSession:
    async with async_session_factory() as session:
        yield session

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
):
    token = credentials.credentials
    payload = decode_supabase_jwt(token)  # raises 401 on failure
    return payload  # payload["sub"] = user_id
```

FastAPI caches dependency results within a single request.

## Async Patterns for LLM / Agent Calls

### Streaming SSE with LangGraph
```python
# app/agent/router.py
from fastapi.responses import StreamingResponse

@router.post("/turn")
async def agent_turn(input: AgentInput, user = Depends(get_current_user)):
    async def event_generator():
        async for event in run_agent_stream(input, user["sub"]):
            yield f"data: {json.dumps(event)}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

### Concurrent Agent Calls
```python
results = await asyncio.gather(
    agent.ainvoke({"role": "merchant", "context": ctx}),
    agent.ainvoke({"role": "guard", "context": ctx}),
)
```

**Key rule:** Never call blocking I/O inside `async def`. Use `await` or `asyncio.to_thread()` for blocking libraries.

## Supabase Integration from FastAPI

### JWT Validation — Local HS256 decode
Supabase tokens are HS256 JWTs signed with your project's JWT secret. Do not hit JWKS endpoint on every request — decode locally.

```python
# app/auth/service.py
import jwt
from fastapi import HTTPException, status
from app.config import settings

def decode_supabase_jwt(token: str) -> dict:
    try:
        payload = jwt.decode(
            token,
            settings.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
        return payload  # payload["sub"] = user UUID
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
```

### DB Access: SQLAlchemy async + psycopg3 (not supabase-py)

| Approach | Best For | Cons |
|---|---|---|
| supabase-py (async since 2.2.0) | Quick prototyping, Storage/Auth admin, RPC | Thin query API, Transaction Mode pooler conflicts |
| SQLAlchemy async + asyncpg | Game state queries | asyncpg + Supavisor Transaction Mode conflicts with prepared statements |
| **SQLAlchemy async + psycopg3** | **Recommended** | Newer, `prepare_threshold=None` handled cleanly |

**Recommendation:** SQLAlchemy async + psycopg3 for game state. supabase-py only for auth admin tasks.

### Connection String Setup
```python
# app/database.py
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

engine = create_async_engine(
    settings.DATABASE_URL,  # postgresql+psycopg://user:pass@db.xxx.supabase.co:5432/postgres
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
```

For a single long-lived container, use direct connection (port 5432) — Supavisor (6543) adds latency and complexity we don't need.

## Migrations: Supabase CLI + Alembic (both)

| Tool | Purpose |
|---|---|
| Supabase CLI migrations | RLS policies, triggers, auth schema, storage |
| Alembic | Python ORM models, game tables, type-safe schema version control |

They don't conflict — separate `supabase/migrations/` and `alembic/versions/` directories.

```bash
# Supabase-managed
supabase migration new enable_rls_game_sessions

# Python ORM
alembic revision --autogenerate -m "add_turn_log_table"
alembic upgrade head
```

## RLS: Service Role Key + Explicit User Filter

For a game backend, the service role approach is correct:
- FastAPI validates JWT, extracts `user_id`, queries as service role
- Filter by `user_id` in WHERE clause
- Do NOT pass user JWT through to DB — RLS can silently return 0 rows

```python
async def get_game_sessions(user_id: str, db: AsyncSession):
    result = await db.execute(
        select(GameSession).where(GameSession.user_id == user_id)
    )
    return result.scalars().all()
```

Keep RLS enabled as safety net but don't rely on it for primary authorization.

## Docker Compose for Local Development

Supabase CLI (`supabase start`) runs its own Docker Compose stack on a separate network. FastAPI container connects via `host.docker.internal:54322` (macOS/Windows).

```yaml
# docker-compose.yml
version: "3.9"

services:
  api:
    build:
      context: .
      dockerfile: Dockerfile.dev
    volumes:
      - ./app:/app/app
      - ./alembic:/app/alembic
    ports:
      - "8000:8000"
    env_file:
      - .env
    environment:
      DATABASE_URL: postgresql+psycopg://postgres:postgres@host.docker.internal:54322/postgres
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
```

```dockerfile
# Dockerfile.dev
FROM python:3.12-slim
WORKDIR /app
RUN pip install uv
COPY pyproject.toml .
RUN uv sync
COPY . .
```

```dockerfile
# Dockerfile (production)
FROM python:3.12-slim
WORKDIR /app
RUN pip install uv
COPY pyproject.toml .
RUN uv sync --no-dev
COPY app/ app/
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
```

### Workflow
1. `supabase start` — starts local Postgres on 54322, Studio on 54323
2. `docker compose up` — starts FastAPI with hot reload connected to local Supabase
3. Code changes in `./app/` reflect immediately without rebuilding

## Deployment: Railway over Fly.io

| | Railway | Fly.io |
|---|---|---|
| Setup | Connect GitHub repo, auto-detects Dockerfile, < 1 min | Requires fly.toml, manual |
| Cold starts | None — always-on by default | Machines pause unless `min_machines_running = 1` |
| Pricing | ~$5/mo Hobby | ~$2-5/mo smallest VM |
| Free tier | $5 credit/mo | No true free tier since Oct 2024 |
| Docker | Full, point at Dockerfile | Full, Docker-native |

**Verdict: Railway.** Revisit Fly when needing sub-100ms global latency or persistent WebSockets across regions.

## CORS Configuration
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://outpost.vercel.app",
        "https://outpost.yourdomain.com",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type"],
)
```

Never `allow_origins=["*"]` with `allow_credentials=True`.

## Top 5 Mistakes for Next.js → FastAPI Developer

1. **Mixing sync and async.** `async def` routes must never call blocking libraries directly.
2. **Business logic in route handlers.** FastAPI handlers should be thin — validate, call service, return.
3. **Not using `Depends()` for shared state.** Auth, DB, game IDs should all be dependencies.
4. **Pydantic v2 semantics.** `@validator` deprecated → `@field_validator`. `orm_mode = True` → `model_config = ConfigDict(from_attributes=True)`.
5. **Running pytest without asyncio mode set.** Async tests silently pass without running. Set `asyncio_mode = "auto"` in pyproject.toml.

## Essential Middleware (Day One)
```python
from fastapi.middleware.cors import CORSMiddleware
import time

app.add_middleware(CORSMiddleware, ...)

@app.middleware("http")
async def add_request_logging(request, call_next):
    start = time.time()
    response = await call_next(request)
    log.info("request", method=request.method, path=request.url.path,
              status=response.status_code, duration_ms=round((time.time()-start)*1000))
    return response

# slowapi for rate limiting
from slowapi import Limiter
from slowapi.util import get_remote_address
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
```

## Testing Pattern
```python
# tests/conftest.py
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.dependencies import get_current_user

@pytest_asyncio.fixture
async def client():
    app.dependency_overrides[get_current_user] = lambda: {"sub": "test-user-id"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
```

```toml
# pyproject.toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

## Build Order
1. `config.py` — Pydantic BaseSettings (fails fast on missing vars)
2. `database.py` — async engine pointed at Supabase local
3. `dependencies.py` — `get_db` and `get_current_user`
4. `main.py` — CORS + logging middleware, include routers
5. `game/` — core turn engine, wire up first before agents
6. `agent/` — LangGraph graph last, depends on stable game state

## Sources
- [FastAPI Best Practices (zhanymkanov)](https://github.com/zhanymkanov/fastapi-best-practices)
- [Validating Supabase JWT in FastAPI](https://dev.to/zwx00/validating-a-supabase-jwt-locally-with-python-and-fastapi-59jf)
- [Supabase asyncpg Pooling Issues](https://medium.com/@patrickduch93/supabase-pooling-and-asyncpg-dont-mix-here-s-the-real-fix-44f700b05249)
- [Streaming AI Agent with FastAPI & LangGraph](https://dev.to/kasi_viswanath/streaming-ai-agent-with-fastapi-langgraph-2025-26-guide-1nkn)
- [Railway vs Fly.io 2026 Comparison](https://thesoftwarescout.com/fly-io-vs-railway-2026-which-developer-platform-should-you-deploy-on/)
- [FastAPI Testing Strategies 2025](https://blog.greeden.me/en/2025/11/04/fastapi-testing-strategies-to-raise-quality-pytest-testclient-httpx-dependency-overrides-db-rollbacks-mocks-contract-tests-and-load-testing/)
