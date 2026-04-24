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
    return saver
