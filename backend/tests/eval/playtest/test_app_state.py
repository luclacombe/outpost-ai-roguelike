"""Regression test for the harness app-state bootstrap.

Without this bootstrap, `scripts.play_test.run_harness` crashes on the first
`/game/{id}/turn` POST with `'State' object has no attribute 'graph'` because
`httpx.ASGITransport` does not run FastAPI lifespan, so `app.state.graph` is
never attached. Catches the regression that surfaced during the real-LLM
smoke before the 42-game CLI run.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.database import async_session_factory
from app.eval.playtest.app_state import harness_app_state
from app.main import app


async def _require_db() -> None:
    try:
        async with async_session_factory() as s:
            await s.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover — env-dependent
        pytest.skip(f"Postgres unreachable: {exc}")


@pytest.mark.eval
async def test_harness_app_state_attaches_and_restores_graph() -> None:
    """harness_app_state attaches app.state.graph + checkpointer inside the
    context, and restores the prior values (or absence) on exit."""
    await _require_db()

    # No prior state — assert clean attach + clean teardown.
    for attr in ("graph", "checkpointer"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)

    async with harness_app_state() as compiled:
        assert compiled is not None
        assert getattr(app.state, "graph", None) is compiled
        assert getattr(app.state, "checkpointer", None) is not None

    assert not hasattr(app.state, "graph")
    assert not hasattr(app.state, "checkpointer")


@pytest.mark.eval
async def test_harness_app_state_restores_prior_value() -> None:
    """If app.state.graph was already set (e.g. nested under another fixture),
    the prior value must be restored, not deleted."""
    await _require_db()

    sentinel_graph = object()
    sentinel_cp = object()
    app.state.graph = sentinel_graph
    app.state.checkpointer = sentinel_cp

    try:
        async with harness_app_state():
            assert app.state.graph is not sentinel_graph
            assert app.state.checkpointer is not sentinel_cp

        assert app.state.graph is sentinel_graph
        assert app.state.checkpointer is sentinel_cp
    finally:
        delattr(app.state, "graph")
        delattr(app.state, "checkpointer")
