"""FastAPI app factory.

Order matters:
  1. configure_logging() runs at import so early middleware messages render.
  2. CORS middleware is registered before log_requests so preflights get
     logged but without tripping auth once it lands.
  3. Routers (auth, game, agent) are included here in later phases.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth.router import router as auth_router
from app.config import settings
from app.game.router import router as game_router
from app.middleware.logging import configure_logging, log_requests

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup / shutdown hooks go here (DB warmup, Langfuse flush).
    yield


app = FastAPI(title="Outpost Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL, "http://localhost:3000"],
    # Bearer tokens don't need cookie credentials; keeping this false lets us
    # avoid the wildcard/credential interaction quirk in browsers.
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

app.middleware("http")(log_requests)

app.include_router(auth_router)
app.include_router(game_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "outpost-backend"}
