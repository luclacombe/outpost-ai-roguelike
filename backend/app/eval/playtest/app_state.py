"""Bootstrap `app.state.graph` + `app.state.checkpointer` for ASGI-transport
in-process eval drivers.

`httpx.ASGITransport(app=app)` does NOT run FastAPI's `lifespan` hook, so
`app.state.graph` (normally attached by `app/main.py`'s lifespan) is unset
when the eval pipeline or the LLM playtest harness POSTs to `/game/.../turn`.
Both code paths must wrap their driving loop in `harness_app_state()` so the
routers can read `request.app.state.graph`.

Mirrors the lifespan body in `app/main.py` (same `open_checkpointer` →
`build_graph` sequence + msgpack-allowlisted serializer), so harness behavior
matches production rather than the conftest's slimmer setup.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from app.agent.checkpointer import open_checkpointer
from app.agent.graph import build_graph
from app.config import settings
from app.main import app


@asynccontextmanager
async def harness_app_state() -> AsyncIterator[Any]:
    """Attach `app.state.graph` + `app.state.checkpointer` for the duration
    of the `async with` block, then restore the prior values (or absence).

    Yields the compiled graph for callers that want to introspect it; most
    callers ignore the value and rely on the side effect.
    """
    prior_graph = getattr(app.state, "graph", None)
    prior_checkpointer = getattr(app.state, "checkpointer", None)

    async with AsyncExitStack() as stack:
        checkpointer = await open_checkpointer(stack, settings.DATABASE_URL)
        compiled = build_graph(checkpointer)
        app.state.graph = compiled
        app.state.checkpointer = checkpointer
        try:
            yield compiled
        finally:
            if prior_graph is None:
                if hasattr(app.state, "graph"):
                    delattr(app.state, "graph")
            else:
                app.state.graph = prior_graph
            if prior_checkpointer is None:
                if hasattr(app.state, "checkpointer"):
                    delattr(app.state, "checkpointer")
            else:
                app.state.checkpointer = prior_checkpointer
