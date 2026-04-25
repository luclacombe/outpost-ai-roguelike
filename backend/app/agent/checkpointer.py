"""AsyncPostgresSaver wiring for the LangGraph pause/resume flow.

Key quirks:
  * LangGraph's AsyncPostgresSaver.from_conn_string wants a raw Postgres
    DSN — "postgresql://..." — NOT the SQLAlchemy "postgresql+psycopg://..."
    form we use elsewhere. `_sqlalchemy_to_psycopg_url` strips the driver
    suffix so the same app.config.settings.DATABASE_URL works for both
    layers.
  * The saver manages its own tables (checkpoints*, checkpoint_blobs,
    checkpoint_writes, checkpoint_migrations) outside Alembic. Call
    `await checkpointer.setup()` at app startup — idempotent.
  * For a long-running FastAPI process, enter the async-context once at
    startup and exit at shutdown via the lifespan's AsyncExitStack. Tests
    typically use the `async with ... as cp:` sugar instead.
"""

from __future__ import annotations

from contextlib import AsyncExitStack

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

# Game-state Pydantic types that ride inside `GameTurnState` and therefore land
# in the LangGraph checkpoint. Without an allowlist, the saver logs
# "Deserializing unregistered type ..." on every checkpoint read. Pin the exact
# set the graph actually carries — broad allow-all defeats the security posture
# the saver is moving toward.
_ALLOWED_MSGPACK_TYPES: tuple[tuple[str, str], ...] = (
    # Schema enums
    ("app.game.schemas", "Quadrant"),
    ("app.game.schemas", "ProposalFlavor"),
    ("app.game.schemas", "EventOrigin"),
    ("app.game.schemas", "EndState"),
    # Direct GameTurnState members
    ("app.game.schemas", "ColonyState"),
    ("app.game.schemas", "Event"),
    ("app.game.schemas", "Proposal"),
    ("app.game.schemas", "ToolCall"),
    # ColonyState transitive members
    ("app.game.schemas", "Resource"),
    ("app.game.schemas", "Agent"),
    ("app.game.schemas", "Transformation"),
    ("app.game.schemas", "Decision"),
    ("app.game.schemas", "DecisionRef"),
    ("app.game.schemas", "ConsequenceSeed"),
    ("app.game.schemas", "ResolvedEvent"),
    ("app.game.schemas", "SeedPayload"),
)


def _sqlalchemy_to_psycopg_url(url: str) -> str:
    """Turn `postgresql+psycopg://...` into `postgresql://...` for LangGraph.

    Leaves an already-stripped URL unchanged. Non-Postgres URLs raise so
    we fail loudly rather than silently handing LangGraph garbage.
    """
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://") :]
    if url.startswith("postgresql://"):
        return url
    raise ValueError(f"Unexpected DATABASE_URL scheme for LangGraph checkpointer: {url!r}")


async def open_checkpointer(stack: AsyncExitStack, database_url: str) -> AsyncPostgresSaver:
    """Enter the AsyncPostgresSaver context and run its one-time setup.

    Caller owns `stack`; `stack.__aexit__` at shutdown closes the saver.
    Returns the live saver ready to hand to `graph.compile(checkpointer=...)`.
    """
    dsn = _sqlalchemy_to_psycopg_url(database_url)
    cm = AsyncPostgresSaver.from_conn_string(dsn)
    saver = await stack.enter_async_context(cm)
    await saver.setup()
    # Replace the saver's permissive default serializer with one whose msgpack
    # allowlist explicitly enumerates our schema types. The default serializer
    # runs in "all types allowed with warning" mode, which spams a
    # "Deserializing unregistered type ..." line per checkpoint read. We can't
    # use `serde.with_msgpack_allowlist(...)` because that helper is a no-op
    # when the existing allowlist is `True` (permissive sentinel). Constructing
    # a fresh serializer with our pinned tuple flips the saver into "explicit
    # allowlist" mode — game-state types load silently; anything else (which
    # nothing on our state graph emits) would now log "Blocked", which is the
    # safer failure mode.
    saver.serde = JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED_MSGPACK_TYPES)
    return saver
