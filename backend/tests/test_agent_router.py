"""/game/{id}/turn and /game/{id}/decide — SSE router tests.

Covers:
  * 404 on missing/foreign game (pre-stream ownership check).
  * 409 on /decide before /turn (graph not paused).
  * 400 on /decide with a bogus proposal id.
  * Happy-path /turn pauses and emits `proposals_ready`.
  * `autonomous_action` args never include `caller_agent_id`.
  * /decide emits `turn_advanced` then `proposals_ready` (mid-run).
  * /decide emits `run_over` when the resumed turn hits end_state.
  * `_sse_frame` helper shape (including the `[DONE]` sentinel).

DB-touching tests call `_require_db()` so contributors without local
Supabase get SKIP (matching the pattern in `test_game_router.py`). The
MemorySaver-backed tests run everywhere.

### Test-only graph wiring

The production `lifespan` sets `app.state.graph` at startup, but
`httpx.ASGITransport` does not run the lifespan. Tests attach a
MemorySaver-backed graph to `app.state.graph` via the `graph_memory`
fixture, and a Postgres-backed one via `graph_postgres` for the full
pause/resume flow. Each fixture restores any prior attribute on teardown.

### SSE parsing

`parse_sse` is a small frame parser — splits on `\\n\\n`, pulls the
`event:` and `data:` lines, JSON-decodes data when not the `[DONE]`
sentinel. Keeps assertions readable.
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
from httpx import ASGITransport, AsyncClient, Response
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import text

from app.agent.checkpointer import _sqlalchemy_to_psycopg_url
from app.agent.graph import build_graph
from app.agent.service import _DONE_SENTINEL, _sse_frame
from app.config import settings
from app.database import async_session_factory
from app.dependencies import get_current_user
from app.main import app
from tests.conftest import fake_haiku_response, fake_sonnet_response

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

# Matches the stub in tests/conftest.py.
TEST_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_sse(body: str) -> list[dict[str, Any]]:
    """Parse an SSE response body into a list of {event, data} dicts.

    `data` is JSON-decoded unless the raw line is the `[DONE]` sentinel,
    in which case it's kept as the literal string so tests can assert the
    wire shape of the terminal frame.
    """
    frames: list[dict[str, Any]] = []
    # Strip trailing whitespace to avoid a trailing empty split.
    for block in body.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event: str | None = None
        data: str | None = None
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data = line[len("data:") :].strip()
        if event is None:
            continue
        parsed: Any
        if data is None:
            parsed = None
        elif data == _DONE_SENTINEL:
            parsed = _DONE_SENTINEL
        else:
            parsed = json.loads(data)
        frames.append({"event": event, "data": parsed})
    return frames


async def _collect_stream(resp) -> list[dict[str, Any]]:
    """Drain a streaming response's body and parse as SSE."""
    buf = b""
    async for chunk in resp.aiter_bytes():
        buf += chunk
    return parse_sse(buf.decode("utf-8"))


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
            {"id": user_id, "name": "Agent Router Test User"},
        )
        await s.commit()


async def _delete_auth_user(user_id: UUID) -> None:
    async with async_session_factory() as s:
        await s.execute(text("DELETE FROM auth.users WHERE id = :id"), {"id": user_id})
        await s.commit()


def _debate_haiku_body(agents) -> dict:
    """Three-proposal debate response shaped to survive parse_debate_response."""
    xml = "".join(f'<agent name="{a.name}">Reacts.</agent>' for a in agents)
    tool_uses = [
        {
            "id": f"toolu_{i}",
            "name": "propose_action",
            "input": {
                "caller_agent_id": str(agents[i % len(agents)].id),
                "name": f"Plan {i}",
                "description": f"Option {i}.",
                "flavor": ["DOMINANT", "MORAL", "WILDCARD"][i],
                # Setting-agnostic nested effect (the setting is picked per
                # user): without it the proposal classes as no_effect and the
                # debate retries.
                "tool_calls_on_pick": [
                    {
                        "name": "modify_health",
                        "args": {"target_id": "player", "delta": -1, "reason": "strain"},
                    }
                ],
            },
        }
        for i in range(3)
    ]
    return fake_haiku_response(text_xml=xml, tool_uses=tool_uses)


def _autonomous_haiku_body_with_tool(agents) -> dict:
    """Autonomous response with one `remember` call so we can assert the
    `autonomous_action` SSE event's shape (and the caller_agent_id strip).

    Schema is strict: `fact` is the required string; `text` would be
    rejected by the validator and the SSE event would never fire.
    """
    tool_uses = [
        {
            "id": "toolu_autonomous_0",
            "name": "remember",
            "input": {
                "caller_agent_id": str(agents[0].id),
                "fact": "This should be remembered.",
            },
        }
    ]
    return fake_haiku_response(text_xml="", tool_uses=tool_uses)


def _dsn() -> str:
    return _sqlalchemy_to_psycopg_url(settings.DATABASE_URL)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def graph_memory() -> AsyncIterator[Any]:
    """Attach an in-memory graph to app.state for tests that don't need DB
    pause/resume persistence (error paths, helpers).

    Restores any prior graph attribute on teardown so tests don't leak into
    each other.
    """
    saver = MemorySaver()
    compiled = build_graph(saver)
    prior = getattr(app.state, "graph", None)
    app.state.graph = compiled
    try:
        yield compiled
    finally:
        if prior is None:
            if hasattr(app.state, "graph"):
                delattr(app.state, "graph")
        else:
            app.state.graph = prior


@pytest_asyncio.fixture
async def graph_postgres() -> AsyncIterator[Any]:
    """Attach a Postgres-checkpointer-backed graph for full pause/resume tests."""
    await _require_db()
    async with AsyncPostgresSaver.from_conn_string(_dsn()) as saver:
        await saver.setup()
        compiled = build_graph(saver)
        prior = getattr(app.state, "graph", None)
        app.state.graph = compiled
        try:
            yield compiled
        finally:
            if prior is None:
                if hasattr(app.state, "graph"):
                    delattr(app.state, "graph")
            else:
                app.state.graph = prior


@pytest_asyncio.fixture
async def seeded_user() -> AsyncIterator[UUID]:
    """Provision auth.users + profiles for the default test user."""
    await _require_db()
    await _ensure_auth_user(TEST_USER_ID, "test@outpost.local")
    try:
        yield TEST_USER_ID
    finally:
        await _delete_auth_user(TEST_USER_ID)


@pytest_asyncio.fixture
async def persisted_game(seeded_user) -> AsyncIterator[Any]:
    """Create a real game via the service so /turn has valid data to load.

    Yields the ColonyState. The session row is torn down by `seeded_user`'s
    ON DELETE CASCADE on teardown.
    """
    from app.game.service import create_game_session

    async with async_session_factory() as db:
        colony = await create_game_session(user_id=seeded_user, db=db)
        await db.commit()
    yield colony


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_sse_frame_helper_shape() -> None:
    frame = _sse_frame("hello", {"k": 1})
    assert frame == 'event: hello\ndata: {"k": 1}\n\n'

    # `[DONE]` sentinel path — skips JSON encoding.
    done = _sse_frame("done", _DONE_SENTINEL)
    assert done == "event: done\ndata: [DONE]\n\n"

    # Every frame ends in the blank-line separator.
    assert frame.endswith("\n\n")
    assert done.endswith("\n\n")


async def test_prepare_turn_wires_observability(
    client, graph_memory, persisted_game, monkeypatch
) -> None:
    """`prepare_turn` returns a `stream_cfg` carrying Langfuse callbacks +
    metadata when LANGFUSE_* keys are populated, while the bare `cfg`
    keeps invariant-#12 thread_id discipline. Network-free: monkeypatches
    the CallbackHandler to a fake.
    """
    import sys
    import types
    from unittest.mock import MagicMock

    from fastapi import Request

    from app.agent.service import prepare_turn
    from app.config import settings as _settings

    await _require_db()

    fake_module = types.ModuleType("langfuse.langchain")
    fake_module.CallbackHandler = MagicMock(name="CallbackHandler")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langfuse.langchain", fake_module)
    monkeypatch.setattr(_settings, "LANGFUSE_PUBLIC_KEY", "pk-test", raising=False)
    monkeypatch.setattr(_settings, "LANGFUSE_SECRET_KEY", "sk-test", raising=False)

    # Build a minimal Request that exposes app.state.graph (the fixture
    # already attached the in-memory graph to app.state).
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [],
        "app": app,
    }
    request = Request(scope)

    async with async_session_factory() as db:
        prepared = await prepare_turn(
            game_id=persisted_game.game_id,
            user_id=TEST_USER_ID,
            db=db,
            request=request,
        )

    assert prepared["cfg"]["configurable"]["thread_id"] == str(persisted_game.game_id)
    stream_cfg = prepared["stream_cfg"]
    assert stream_cfg["configurable"]["thread_id"] == str(persisted_game.game_id)
    assert "callbacks" in stream_cfg
    assert len(stream_cfg["callbacks"]) == 1
    md = stream_cfg["metadata"]
    assert md["langfuse_session_id"] == f"outpost-run-{persisted_game.game_id}"
    assert md["langfuse_user_id"] == str(TEST_USER_ID)


async def test_turn_404_when_game_missing(client, graph_memory) -> None:
    """/turn with a game_id that doesn't exist (or isn't ours) → 404 with
    the standard error envelope. No SSE frame is ever emitted.

    Requires DB because the ownership check goes through
    `get_game_session`, which does a real SELECT — skip cleanly (not
    fail) for contributors without a local Postgres.
    """
    await _require_db()
    random_id = uuid4()
    resp = await client.post(f"/game/{random_id}/turn")
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["detail"]["error"]["code"] == "game_not_found"


async def test_decide_404_when_game_missing(client, graph_memory) -> None:
    await _require_db()
    random_id = uuid4()
    resp = await client.post(
        f"/game/{random_id}/decide",
        json={"chosen_proposal_id": "anything"},
    )
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["detail"]["error"]["code"] == "game_not_found"


@respx.mock
async def test_turn_happy_path_ends_with_proposals_ready(persisted_game, graph_postgres) -> None:
    """Full pre-pause flow: one Haiku debate call, then the SSE stream
    should emit agent_spoke frames and terminate with proposals_ready + done.
    """
    colony = persisted_game
    respx.post(_ANTHROPIC_URL).mock(
        return_value=Response(200, json=_debate_haiku_body(colony.agents))
    )

    # httpx's streaming API is on AsyncClient.stream; open a dedicated
    # client because the shared `client` fixture doesn't expose .stream
    # usage patterns consistently across httpx versions.
    app.dependency_overrides[get_current_user] = lambda: {
        "sub": str(colony.user_id),
        "email": "test@outpost.local",
        "aud": "authenticated",
    }
    try:
        async with (
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c,
            c.stream("POST", f"/game/{colony.game_id}/turn") as resp,
        ):
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            frames = await _collect_stream(resp)
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    event_names = [f["event"] for f in frames]
    assert "agent_spoke" in event_names, frames
    assert "proposals_ready" in event_names, frames
    # Last frame is the done sentinel.
    assert frames[-1] == {"event": "done", "data": _DONE_SENTINEL}

    # proposals_ready payload has the expected keys.
    pr = next(f for f in frames if f["event"] == "proposals_ready")
    assert pr["data"]["turn_number"] is not None
    assert len(pr["data"]["proposals"]) > 0
    for p in pr["data"]["proposals"]:
        # ProposalPublic drops `tool_calls_on_pick`; sanity check that
        # the public projection was used.
        assert "tool_calls_on_pick" not in p


@respx.mock
async def test_turn_double_post_short_circuits_to_paused_proposals(
    persisted_game, graph_postgres
) -> None:
    """Double-POST /turn on an already-paused run re-emits the paused
    proposals without making a second Anthropic call. Protects the
    client reconnect/replay path (see app.agent.service docstring).
    """
    colony = persisted_game
    route = respx.post(_ANTHROPIC_URL).mock(
        return_value=Response(200, json=_debate_haiku_body(colony.agents))
    )

    app.dependency_overrides[get_current_user] = lambda: {
        "sub": str(colony.user_id),
        "email": "test@outpost.local",
        "aud": "authenticated",
    }
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # First /turn runs the graph to pause.
            async with c.stream("POST", f"/game/{colony.game_id}/turn") as resp:
                first_frames = await _collect_stream(resp)
            assert route.call_count == 1

            # Second /turn should short-circuit — no additional Anthropic call.
            async with c.stream("POST", f"/game/{colony.game_id}/turn") as resp:
                second_frames = await _collect_stream(resp)
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert route.call_count == 1, "second /turn should not re-invoke the graph"

    def _proposal_ids(frames: list[dict[str, Any]]) -> list[str]:
        pr = next(f for f in frames if f["event"] == "proposals_ready")
        return [p["id"] for p in pr["data"]["proposals"]]

    assert _proposal_ids(first_frames) == _proposal_ids(second_frames)
    assert second_frames[-1] == {"event": "done", "data": _DONE_SENTINEL}

    # The short-circuit must ALSO re-emit the cached `agent_spoke` frames.
    # Without this, the second /turn returns proposals_ready alone and any
    # caller assembling a dashboard from the SSE stream renders all advisors
    # as `(silent)`. (Smoke 2026-04-29 regression.)
    def _agent_spoke_ids(frames: list[dict[str, Any]]) -> set[str]:
        return {f["data"]["agent_id"] for f in frames if f["event"] == "agent_spoke"}

    first_spoke = _agent_spoke_ids(first_frames)
    second_spoke = _agent_spoke_ids(second_frames)
    assert first_spoke, "first /turn should emit agent_spoke frames"
    assert second_spoke == first_spoke, (
        f"second /turn must re-emit cached agent_spoke. first={first_spoke}, second={second_spoke}"
    )


@respx.mock
async def test_decide_409_when_not_paused(persisted_game, graph_postgres) -> None:
    """/decide before /turn → 409 `not_paused`. No SSE body."""
    colony = persisted_game

    app.dependency_overrides[get_current_user] = lambda: {
        "sub": str(colony.user_id),
        "email": "test@outpost.local",
        "aud": "authenticated",
    }
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                f"/game/{colony.game_id}/decide",
                json={"chosen_proposal_id": "whatever"},
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["detail"]["error"]["code"] == "not_paused"


@respx.mock
async def test_decide_400_when_proposal_id_unknown(persisted_game, graph_postgres) -> None:
    """Flow to the pause via /turn, then /decide with a garbage id → 400."""
    colony = persisted_game
    respx.post(_ANTHROPIC_URL).mock(
        return_value=Response(200, json=_debate_haiku_body(colony.agents))
    )

    app.dependency_overrides[get_current_user] = lambda: {
        "sub": str(colony.user_id),
        "email": "test@outpost.local",
        "aud": "authenticated",
    }
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # Run /turn to get into the paused state.
            async with c.stream("POST", f"/game/{colony.game_id}/turn") as resp:
                assert resp.status_code == 200
                await _collect_stream(resp)

            # /decide with a bogus id.
            resp = await c.post(
                f"/game/{colony.game_id}/decide",
                json={"chosen_proposal_id": "prop_does_not_exist"},
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["detail"]["error"]["code"] == "unknown_proposal"


@respx.mock
async def test_decide_strips_caller_agent_id_from_autonomous_action_args(
    persisted_game, graph_postgres
) -> None:
    """The `autonomous_action` SSE payload's `args` must NOT contain
    `caller_agent_id` — that's promoted to the top-level `agent_id` field.

    Runs the full /turn → /decide cycle: /turn pauses, /decide resumes and
    emits autonomous_action for each applied autonomous tool call. The
    autonomous Haiku response is crafted to emit exactly one `remember`
    call from the first agent.
    """
    colony = persisted_game

    # Four Anthropic calls: turn-1 debate (pre-pause), turn-1 autonomous
    # (post-pause), turn-1 narration Sonnet (post-pause), and turn-2
    # debate — the graph loops through generate_event → agent_debate
    # after advance_turn because the run isn't over.
    responses = [
        Response(200, json=_debate_haiku_body(colony.agents)),
        Response(200, json=_autonomous_haiku_body_with_tool(colony.agents)),
        Response(200, json=fake_sonnet_response("Turn recap.")),
        Response(200, json=_debate_haiku_body(colony.agents)),
    ]
    respx.post(_ANTHROPIC_URL).mock(side_effect=responses)

    app.dependency_overrides[get_current_user] = lambda: {
        "sub": str(colony.user_id),
        "email": "test@outpost.local",
        "aud": "authenticated",
    }
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # Pause.
            async with c.stream("POST", f"/game/{colony.game_id}/turn") as r1:
                assert r1.status_code == 200
                turn_frames = await _collect_stream(r1)
            chosen = next(f for f in turn_frames if f["event"] == "proposals_ready")
            chosen_id = chosen["data"]["proposals"][0]["id"]

            # Resume.
            async with c.stream(
                "POST",
                f"/game/{colony.game_id}/decide",
                json={"chosen_proposal_id": chosen_id},
            ) as r2:
                assert r2.status_code == 200
                decide_frames = await _collect_stream(r2)
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    autonomous_frames = [f for f in decide_frames if f["event"] == "autonomous_action"]
    assert len(autonomous_frames) >= 1, decide_frames
    for f in autonomous_frames:
        assert "caller_agent_id" not in f["data"]["args"]
        assert f["data"]["agent_id"] is not None
        assert f["data"]["tool_name"] == "remember"


@respx.mock
async def test_decide_emits_turn_advanced_and_next_proposals(
    persisted_game, graph_postgres
) -> None:
    """Full cycle: /turn → pause → /decide → run continues → next turn's
    `proposals_ready` closes the stream."""
    colony = persisted_game

    # 4 calls: debate(turn1), autonomous(turn1), narration(turn1), debate(turn2).
    responses = [
        Response(200, json=_debate_haiku_body(colony.agents)),
        Response(200, json=fake_haiku_response(text_xml="")),
        Response(200, json=fake_sonnet_response("End of turn 1.")),
        Response(200, json=_debate_haiku_body(colony.agents)),
    ]
    respx.post(_ANTHROPIC_URL).mock(side_effect=responses)

    app.dependency_overrides[get_current_user] = lambda: {
        "sub": str(colony.user_id),
        "email": "test@outpost.local",
        "aud": "authenticated",
    }
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            async with c.stream("POST", f"/game/{colony.game_id}/turn") as r1:
                assert r1.status_code == 200
                turn_frames = await _collect_stream(r1)
            chosen_id = next(f for f in turn_frames if f["event"] == "proposals_ready")["data"][
                "proposals"
            ][0]["id"]

            async with c.stream(
                "POST",
                f"/game/{colony.game_id}/decide",
                json={"chosen_proposal_id": chosen_id},
            ) as r2:
                assert r2.status_code == 200
                decide_frames = await _collect_stream(r2)
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    events = [f["event"] for f in decide_frames]
    assert "decision_applied" in events
    assert "turn_advanced" in events
    assert "proposals_ready" in events
    assert "run_over" not in events
    assert decide_frames[-1] == {"event": "done", "data": _DONE_SENTINEL}

    # Event ordering: decision_applied MUST precede turn_advanced, and
    # turn_advanced MUST precede the next turn's proposals_ready.
    da_idx = events.index("decision_applied")
    ta_idx = events.index("turn_advanced")
    pr_idx = events.index("proposals_ready")
    assert da_idx < ta_idx < pr_idx, events

    # turn_advanced payload's turn_number is post-advance, so > original.
    ta = decide_frames[ta_idx]["data"]
    assert ta["turn_number"] == colony.turn_number + 1


@respx.mock
async def test_turn_after_decide_emits_cached_agent_spoke(persisted_game, graph_postgres) -> None:
    """Post-decide /turn must emit `agent_spoke` frames for the new turn.

    The graph topology runs `advance_turn → generate_event → agent_debate
    → apply_tool_calls → [pause]` at the END of /decide, so by the time
    the harness POSTs /turn for the next turn, the graph is already
    paused at resolve. Without re-emitting cached agent_spoke frames in
    that short-circuit branch, the harness's `TurnViewTracker.build`
    sees zero advisor reactions and the dashboard renders every advisor
    as `(silent)` — which is exactly what the 2026-04-29 smoke saw, and
    which the persona then HONESTLY reported as silence.
    """
    colony = persisted_game

    # 5 calls: debate(t1), autonomous(t1), narration(t1), debate(t2),
    # then the /turn-2 short-circuit makes no Anthropic calls.
    responses = [
        Response(200, json=_debate_haiku_body(colony.agents)),
        Response(200, json=fake_haiku_response(text_xml="")),
        Response(200, json=fake_sonnet_response("End of turn 1.")),
        Response(200, json=_debate_haiku_body(colony.agents)),
    ]
    respx.post(_ANTHROPIC_URL).mock(side_effect=responses)

    app.dependency_overrides[get_current_user] = lambda: {
        "sub": str(colony.user_id),
        "email": "test@outpost.local",
        "aud": "authenticated",
    }
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            async with c.stream("POST", f"/game/{colony.game_id}/turn") as r1:
                turn1_frames = await _collect_stream(r1)
            chosen_id = next(f for f in turn1_frames if f["event"] == "proposals_ready")["data"][
                "proposals"
            ][0]["id"]

            async with c.stream(
                "POST",
                f"/game/{colony.game_id}/decide",
                json={"chosen_proposal_id": chosen_id},
            ) as r2:
                await _collect_stream(r2)

            async with c.stream("POST", f"/game/{colony.game_id}/turn") as r3:
                turn2_frames = await _collect_stream(r3)
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    spoke = [f for f in turn2_frames if f["event"] == "agent_spoke"]
    assert len(spoke) == len(colony.agents), (
        f"post-decide /turn must emit one agent_spoke per advisor; "
        f"got {len(spoke)}, expected {len(colony.agents)}. Frames: "
        f"{[f['event'] for f in turn2_frames]}"
    )
    spoke_ids = {f["data"]["agent_id"] for f in spoke}
    expected_ids = {str(a.id) for a in colony.agents}
    assert spoke_ids == expected_ids, f"spoke={spoke_ids}, expected={expected_ids}"

    # Texts must be non-empty — the cached frame had real reaction content.
    for f in spoke:
        assert f["data"]["text"], f"agent_spoke text was empty: {f}"


@respx.mock
async def test_decide_emits_run_over_on_final_turn(persisted_game, graph_postgres) -> None:
    """Pin the run to max_turns-1 via DB, then /decide → engine.advance_turn
    sets end_state=ENDURED/COLLAPSED and the graph's conditional edge routes
    to END. The stream ends with `run_over`, not `proposals_ready`."""
    colony = persisted_game

    # Only 3 calls this time (debate, autonomous, narration) — after
    # advance_turn the graph ends, no next-turn debate.
    responses = [
        Response(200, json=_debate_haiku_body(colony.agents)),
        Response(200, json=fake_haiku_response(text_xml="")),
        Response(200, json=fake_sonnet_response("Final turn.")),
    ]
    respx.post(_ANTHROPIC_URL).mock(side_effect=responses)

    # Drain resources and pin to max_turns-1 so advance hits end_state.
    drained = {rid: r.model_copy(update={"value": 0}) for rid, r in colony.resources.items()}
    terminal = colony.model_copy(
        update={
            "turn_number": colony.max_turns - 1,
            "legitimacy": 5,
            "resources": drained,
        }
    )
    async with async_session_factory() as s:
        await s.execute(
            text(
                "UPDATE game_sessions SET current_turn = :t, legitimacy = :l,"
                " state = :st WHERE id = :gid"
            ),
            {
                "t": terminal.turn_number,
                "l": terminal.legitimacy,
                "st": json.dumps(terminal.model_dump(mode="json")),
                "gid": terminal.game_id,
            },
        )
        await s.commit()

    app.dependency_overrides[get_current_user] = lambda: {
        "sub": str(terminal.user_id),
        "email": "test@outpost.local",
        "aud": "authenticated",
    }
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            async with c.stream("POST", f"/game/{terminal.game_id}/turn") as r1:
                assert r1.status_code == 200
                turn_frames = await _collect_stream(r1)
            chosen_id = next(f for f in turn_frames if f["event"] == "proposals_ready")["data"][
                "proposals"
            ][0]["id"]

            async with c.stream(
                "POST",
                f"/game/{terminal.game_id}/decide",
                json={"chosen_proposal_id": chosen_id},
            ) as r2:
                assert r2.status_code == 200
                decide_frames = await _collect_stream(r2)
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    events = [f["event"] for f in decide_frames]
    assert "run_over" in events, events
    assert "proposals_ready" not in events, events
    ro = next(f for f in decide_frames if f["event"] == "run_over")
    assert ro["data"]["end_state"] in {"ENDURED", "COLLAPSED", "OUSTED", "KILLED"}
    assert decide_frames[-1] == {"event": "done", "data": _DONE_SENTINEL}
