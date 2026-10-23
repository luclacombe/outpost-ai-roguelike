"""Task 4.6 — StateGraph assembly + Postgres checkpointer wiring.

Shape tests (1-3) use `MemorySaver` so they run everywhere.

Integration tests (4-6) wire up a real `AsyncPostgresSaver` against the
local Supabase Postgres and skip cleanly via `_require_db()` when the
database isn't reachable — matches the pattern in `test_game_router.py`
and `test_nodes.py`.

Each integration test uses a unique `thread_id` (uuid) to avoid cross-
test pollution and enters the saver via `async with AsyncPostgresSaver
.from_conn_string(...)` so cleanup is per-test — never the production
`open_checkpointer` helper.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
import respx
from httpx import Response
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import text

from app.agent.checkpointer import _sqlalchemy_to_psycopg_url
from app.agent.graph import build_graph, thread_config
from app.config import settings
from app.database import async_session_factory
from tests.conftest import fake_haiku_response, fake_sonnet_response

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


# ---------------------------------------------------------------------------
# Pure / shape tests (no DB)
# ---------------------------------------------------------------------------


def test_thread_config_shape() -> None:
    assert thread_config("abc") == {"configurable": {"thread_id": "abc"}}
    # Non-string ids get coerced to str so LangGraph's serializer is happy.
    assert thread_config(str(uuid.UUID("00000000-0000-0000-0000-000000000001"))) == {
        "configurable": {"thread_id": "00000000-0000-0000-0000-000000000001"}
    }


def test_sqlalchemy_to_psycopg_url_strips_suffix() -> None:
    sa = "postgresql+psycopg://u:p@h:5432/db?sslmode=disable"
    assert _sqlalchemy_to_psycopg_url(sa) == "postgresql://u:p@h:5432/db?sslmode=disable"

    plain = "postgresql://u:p@h:5432/db"
    assert _sqlalchemy_to_psycopg_url(plain) == plain

    with pytest.raises(ValueError):
        _sqlalchemy_to_psycopg_url("mysql://u:p@h/db")


def test_build_graph_has_all_nodes_and_correct_shape() -> None:
    compiled = build_graph(MemorySaver())
    g = compiled.get_graph()

    expected_nodes = {
        "generate_event",
        "agent_debate",
        "apply_tool_calls",
        "resolve",
        "autonomous_actions",
        "check_deaths",
        "narrate_turn",
        "advance_turn",
    }
    assert expected_nodes.issubset(set(g.nodes.keys()))

    # Collect directed edges (source -> target) for assertion.
    edges = {(e.source, e.target) for e in g.edges}
    # Straight-through edges.
    assert ("__start__", "generate_event") in edges
    assert ("generate_event", "agent_debate") in edges
    assert ("agent_debate", "apply_tool_calls") in edges
    assert ("apply_tool_calls", "resolve") in edges
    assert ("resolve", "autonomous_actions") in edges
    assert ("autonomous_actions", "check_deaths") in edges
    assert ("check_deaths", "narrate_turn") in edges
    assert ("narrate_turn", "advance_turn") in edges

    # Conditional fan-out from advance_turn.
    conditional_targets = {
        e.target for e in g.edges if e.source == "advance_turn" and e.conditional
    }
    assert conditional_targets == {"generate_event", "__end__"}


# ---------------------------------------------------------------------------
# DB-touching integration tests
# ---------------------------------------------------------------------------


async def _require_db() -> None:
    try:
        async with async_session_factory() as s:
            await s.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover — env-dependent
        pytest.skip(f"Postgres unreachable: {exc}")


async def _ensure_auth_user(user_id: UUID, email: str) -> None:
    async with async_session_factory() as s:
        await s.execute(
            text(
                """
                INSERT INTO auth.users (
                    id, instance_id, aud, role, email, encrypted_password,
                    email_confirmed_at, created_at, updated_at
                )
                VALUES (
                    :id,
                    '00000000-0000-0000-0000-000000000000'::uuid,
                    'authenticated', 'authenticated', :email,
                    '', now(), now(), now()
                )
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {"id": user_id, "email": email},
        )
        await s.execute(
            text(
                """
                INSERT INTO profiles (id, display_name, total_runs, created_at)
                VALUES (:id, :name, 0, now())
                ON CONFLICT (id) DO UPDATE SET last_run_started_at = NULL, total_runs = 0
                """
            ),
            {"id": user_id, "name": "Graph Test User"},
        )
        await s.commit()


async def _delete_auth_user(user_id: UUID) -> None:
    async with async_session_factory() as s:
        await s.execute(text("DELETE FROM auth.users WHERE id = :id"), {"id": user_id})
        await s.commit()


@pytest_asyncio.fixture
async def persisted_colony() -> AsyncIterator[Any]:
    """Provision an auth user + profile + a game_sessions row via the service.

    Yields the ColonyState; cleans up via CASCADE on teardown.
    """
    await _require_db()
    from app.game.service import create_game_session

    user_id = uuid4()
    await _ensure_auth_user(user_id, f"graph_{user_id.hex[:8]}@outpost.local")
    try:
        async with async_session_factory() as db:
            colony = await create_game_session(user_id=user_id, db=db)
            await db.commit()
        yield colony
    finally:
        await _delete_auth_user(user_id)


def _debate_haiku_body(agents) -> dict:
    """Three-option `submit_debate` response (setting-agnostic health effect)."""
    options = [
        {
            "flavor": ["DOMINANT", "MORAL", "WILDCARD"][i],
            "advisor": agents[i % len(agents)].name,
            "title": f"Plan {i}",
            "description": f"Option {i}.",
            # Setting-agnostic effect (the setting is picked per user):
            # without one the proposal classes as no_effect and the debate
            # retries.
            "resource_effects": [],
            "person_effects": [{"kind": "health", "target": "player", "delta": -1}],
        }
        for i in range(3)
    ]
    reactions = [{"advisor": a.name, "line": "Reacts."} for a in agents]
    return fake_haiku_response(
        tool_uses=[{"name": "submit_debate", "input": {"reactions": reactions, "options": options}}]
    )


def _autonomous_haiku_body() -> dict:
    """Autonomous call submits no actions — keeps the post-pause path simple."""
    return fake_haiku_response(tool_uses=[{"name": "submit_offturn", "input": {"actions": []}}])


def _dsn() -> str:
    return _sqlalchemy_to_psycopg_url(settings.DATABASE_URL)


@respx.mock
async def test_graph_pauses_before_resolve_on_first_run(persisted_colony) -> None:
    """First `ainvoke` should stop before `resolve` fires; proposals surface."""
    colony = persisted_colony

    # Only the pre-pause debate call should happen before we pause.
    respx.post(_ANTHROPIC_URL).mock(
        return_value=Response(200, json=_debate_haiku_body(colony.agents))
    )

    initial_state: dict[str, Any] = {
        "game_id": str(colony.game_id),
        "user_id": str(colony.user_id),
        "colony_state": colony.model_copy(update={"turn_number": 1}),
        "cost_cents_this_turn": 0.0,
    }
    cfg = thread_config(str(uuid.uuid4()))

    async with AsyncPostgresSaver.from_conn_string(_dsn()) as saver:
        await saver.setup()
        compiled = build_graph(saver)

        result = await compiled.ainvoke(initial_state, config=cfg)

        # Pre-pause nodes ran: proposals populated, no narration, no is_run_over.
        assert "proposals" in result
        assert len(result["proposals"]) > 0
        assert result.get("narration") is None
        assert result.get("is_run_over") is None

        # The state's `next` should be exactly `resolve`.
        snapshot = await compiled.aget_state(cfg)
        assert snapshot.next == ("resolve",)


@respx.mock
async def test_graph_resume_requires_same_thread_id(persisted_colony) -> None:
    """Resuming with a different thread_id starts a fresh run — not the paused one."""
    colony = persisted_colony
    respx.post(_ANTHROPIC_URL).mock(
        return_value=Response(200, json=_debate_haiku_body(colony.agents))
    )

    initial_state: dict[str, Any] = {
        "game_id": str(colony.game_id),
        "user_id": str(colony.user_id),
        "colony_state": colony.model_copy(update={"turn_number": 1}),
        "cost_cents_this_turn": 0.0,
    }

    cfg_a = thread_config(str(uuid.uuid4()))
    cfg_b = thread_config(str(uuid.uuid4()))

    async with AsyncPostgresSaver.from_conn_string(_dsn()) as saver:
        await saver.setup()
        compiled = build_graph(saver)

        # Pause thread A.
        await compiled.ainvoke(initial_state, config=cfg_a)
        snap_a = await compiled.aget_state(cfg_a)
        assert snap_a.next == ("resolve",)
        assert len(snap_a.values.get("proposals") or []) > 0

        # Thread B has no state — aget_state returns empty `values`.
        snap_b = await compiled.aget_state(cfg_b)
        # Proves pause is keyed by thread_id: B doesn't see A's proposals.
        assert not snap_b.values.get("proposals")


@respx.mock
async def test_graph_resume_applies_player_choice_and_advances(persisted_colony) -> None:
    """Full pre->pause->resume->end cycle. The run exits cleanly when
    resumed after `player_choice` is set via `aupdate_state`.
    """
    colony = persisted_colony

    # Three Anthropic calls total: debate (pre-pause), autonomous (post-pause),
    # narration Sonnet (post-pause). respx routes via URL — same endpoint for
    # all. We use `side_effect` so each call gets the right body in sequence.
    debate_body = _debate_haiku_body(colony.agents)
    autonomous_body = _autonomous_haiku_body()
    narration_body = fake_sonnet_response("The snow settled.")
    responses = [
        Response(200, json=debate_body),
        Response(200, json=autonomous_body),
        Response(200, json=narration_body),
    ]
    respx.post(_ANTHROPIC_URL).mock(side_effect=responses)

    # Start at turn_number=1; advance_turn will bump it to 2 (below max_turns
    # so the conditional edge loops — we force exit by setting is_run_over
    # via direct state before invoking? No — easier: drain resources so
    # engine.advance_turn sets end_state=COLLAPSED and the conditional edge
    # sends us to END after one turn.
    drained = {rid: r.model_copy(update={"value": 0}) for rid, r in colony.resources.items()}
    starting = colony.model_copy(
        update={
            "turn_number": colony.max_turns - 1,  # advance takes us to max_turns -> run over
            "legitimacy": 5,
            "resources": drained,
        }
    )

    # Persist the tweaked state so advance_turn's DB read sees it.
    async with async_session_factory() as s:
        await s.execute(
            text(
                "UPDATE game_sessions SET current_turn = :t, legitimacy = :l, state = :st"
                " WHERE id = :gid"
            ),
            {
                "t": starting.turn_number,
                "l": starting.legitimacy,
                "st": json.dumps(starting.model_dump(mode="json")),
                "gid": starting.game_id,
            },
        )
        await s.commit()

    initial_state: dict[str, Any] = {
        "game_id": str(starting.game_id),
        "user_id": str(starting.user_id),
        "colony_state": starting,
        "cost_cents_this_turn": 0.0,
    }
    cfg = thread_config(str(uuid.uuid4()))

    async with AsyncPostgresSaver.from_conn_string(_dsn()) as saver:
        await saver.setup()
        compiled = build_graph(saver)

        # Part 1: run to the pre-resolve pause.
        paused = await compiled.ainvoke(initial_state, config=cfg)
        chosen_id = paused["proposals"][0].id

        snap = await compiled.aget_state(cfg)
        assert snap.next == ("resolve",)

        # Part 2: set the player's choice via aupdate_state, then resume.
        await compiled.aupdate_state(cfg, {"player_choice": chosen_id})

        resumed = await compiled.ainvoke(None, config=cfg)

        # Post-resume state was persisted: narration set, run-over evaluated,
        # event_log grew by 1 (resolve appends), turn advanced.
        assert resumed["narration"] is not None
        assert resumed["is_run_over"] is True
        assert resumed["colony_state"].turn_number == starting.turn_number + 1
        assert len(resumed["colony_state"].event_log) == len(starting.event_log) + 1
