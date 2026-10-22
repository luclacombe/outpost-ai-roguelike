"""Real-LLM end-to-end integration test for the Outpost agent graph.

Runs a 10-turn game through the live HTTP surface
(/game/session → loop of /game/{id}/turn + /game/{id}/decide) against
real Anthropic Haiku + Sonnet APIs.

Marked `integration` and auto-skipped unless:
  * `ANTHROPIC_API_KEY` is set in env.
  * Postgres is reachable (override `DATABASE_URL` to local Supabase).

Expected cost (back-of-envelope from app/agent/constants.py):
  * Debate per turn (Haiku, ~3000 in / ~800 out with cache hit after turn 2):
      - turn 1: fresh 3000 * $1/MTok + 800 * $5/MTok = 0.3¢ + 4¢ ≈ 4.3¢
      - turn 2+: 2700 cached * $0.10/MTok + 300 fresh * $1/MTok + 800 * $5/MTok ≈ 0.03 + 0.3 + 4 = 4.3¢
  * Autonomous per turn (Haiku, similar shape): ~4¢
  * Narration per turn (Sonnet, 1500 in / 300 out): 1500 * $3/MTok + 300 * $15/MTok ≈ 0.5 + 4.5 = 5¢
  * Per turn total: ~13¢; 10 turns theoretical ceiling: ~130¢
  * But narration is gated at MAX_COST_CENTS_PER_TURN=15, so most turns skip it.
    Realistic: ~8¢/turn × 10 = ~80¢ without narration cap; with the cap,
    narration fires only on cheap turns → expect budget ~25-28¢.

Assertions:
  * end_state set to one of {ENDURED, COLLAPSED, OUSTED, KILLED}.
  * cost_cents < 30 (per plan budget).
  * cache_read_input_tokens > 0 observed after turn 2 (invariant #5).
  * At least 2 consequence seeds scheduled during the run.
  * No 5xx responses anywhere in the flow.

Run it manually:
    ANTHROPIC_API_KEY="sk-ant-..." \\
    DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:54322/postgres" \\
      uv run pytest -m integration tests/integration/ -v -s
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database import async_session_factory
from app.dependencies import get_current_user
from app.game.models import GameSession
from app.main import app

# SSE helpers are test-only plumbing — don't duplicate them here.
from tests.test_agent_router import _collect_stream

# Collect-time skip if the env var isn't set. Keeps `pytest tests/` safe.
if not (os.environ.get("LLM_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")):
    pytest.skip(
        "LLM_API_KEY / ANTHROPIC_API_KEY not set; skipping full-run integration test.",
        allow_module_level=True,
    )


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _auth_override(user_id: UUID) -> dict[str, str]:
    return {
        "sub": str(user_id),
        "email": "integration@outpost.local",
        "aud": "authenticated",
    }


async def _load_game_session(game_id: UUID) -> GameSession:
    async with async_session_factory() as s:
        row = (await s.execute(select(GameSession).where(GameSession.id == game_id))).scalar_one()
        return row


async def test_full_run_10_turns_against_real_anthropic(
    full_run_user_id: UUID,
    graph_postgres: Any,
) -> None:
    """Drive a real 10-turn game through the live HTTP surface.

    Skipped at collection if ANTHROPIC_API_KEY is absent. DB-skipping is
    handled by the fixture.
    """
    cache_read_samples: list[int] = []
    statuses: list[int] = []

    # nodes.py imports usage_record by name at module load, so patch the
    # consumer-side reference. Every LLM call (debate, autonomous, narration)
    # builds one record, whose `cache_read_tokens` is the cache-hit evidence
    # (the same field that lands in `turn_meta["llm_calls"]`).
    import app.agent.nodes as nodes

    original_record = nodes.usage_record

    def _traced_record(resp: Any, model: str, purpose: str) -> dict[str, Any]:
        record = original_record(resp, model, purpose)
        cache_read_samples.append(record["cache_read_tokens"])
        return record

    app.dependency_overrides[get_current_user] = lambda: _auth_override(full_run_user_id)

    try:
        with patch.object(nodes, "usage_record", _traced_record):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                # --- create game ---
                create_resp = await client.post("/game/session")
                statuses.append(create_resp.status_code)
                assert create_resp.status_code == 201, create_resp.text
                game_id = UUID(create_resp.json()["game_id"])
                print(f"[integration] created game {game_id}")

                # --- 10-turn loop ---
                turns_played = 0
                ended = False
                for _ in range(10):
                    # POST /turn — streams until pause (or end).
                    async with client.stream("POST", f"/game/{game_id}/turn") as resp:
                        statuses.append(resp.status_code)
                        assert resp.status_code == 200, await resp.aread()
                        frames = await _collect_stream(resp)

                    # Detect immediate end (unlikely on turn 1 but guard for it).
                    if any(f["event"] == "run_over" for f in frames):
                        ended = True
                        break

                    pr = next(f for f in frames if f["event"] == "proposals_ready")
                    proposals = pr["data"]["proposals"]
                    assert proposals, "proposals_ready emitted with empty proposals"
                    chosen = proposals[0]["id"]

                    # POST /decide — streams through resolve → autonomous →
                    # check_deaths → narrate → advance, and may loop into the
                    # next turn's proposals_ready.
                    async with client.stream(
                        "POST",
                        f"/game/{game_id}/decide",
                        json={"chosen_proposal_id": chosen},
                    ) as resp:
                        statuses.append(resp.status_code)
                        assert resp.status_code == 200, await resp.aread()
                        frames = await _collect_stream(resp)

                    turns_played += 1
                    if any(f["event"] == "run_over" for f in frames):
                        ended = True
                        break
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert turns_played >= 1, "no turns completed"

    # --- invariants ---
    game_row = await _load_game_session(game_id)

    assert game_row.end_state in {"ENDURED", "COLLAPSED", "OUSTED", "KILLED"}, (
        f"end_state={game_row.end_state!r}; expected a terminal value"
    )
    assert ended or game_row.end_state is not None, (
        "loop completed without hitting run_over; final end_state check failed"
    )

    print(f"[integration] final cost: {game_row.cost_cents}¢ across {turns_played} turns")
    assert game_row.cost_cents < 30, f"cost {game_row.cost_cents}¢ exceeds 30¢ budget"

    # Cache hit: at least one sample after turn 2's debate should show >0
    # cache_read_input_tokens. Debate is the first LLM call per turn, so
    # sample index 2 corresponds to turn 3's debate roughly (turn 1 debate,
    # turn 1 autonomous, turn 2 debate is index 2). The check is lenient —
    # ANY sample after the first two counts as cache-hit evidence.
    later_samples = cache_read_samples[2:] if len(cache_read_samples) > 2 else []
    assert any(s > 0 for s in later_samples), (
        f"no cache_read_input_tokens > 0 observed in samples after index 2: {cache_read_samples}"
    )

    # Consequence seeds: at least 2 scheduled across the run.
    seeds = game_row.state.get("consequence_seeds", [])
    assert len(seeds) >= 2, f"expected >=2 consequence seeds, got {len(seeds)}: {seeds}"

    # No 5xx anywhere.
    assert all(s < 500 for s in statuses), f"saw 5xx response codes in flow: {statuses}"
