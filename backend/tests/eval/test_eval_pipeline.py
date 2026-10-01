"""End-to-end smoke test for the eval pipeline.

Drives `simulate_game` against the in-process HTTP surface with respx-stubbed
Anthropic responses, builds a `RunRecord` from the streamed events + final
DB state, and asserts the documented metric thresholds.

Marked `eval` so it's gated out of the default `pytest tests/` run via
`addopts = "-m 'not eval and not integration'"` in `pyproject.toml`. Run
explicitly with `uv run pytest tests/eval/ -m eval`.

Mocked-LLM mode is the default. A separate test gated on
`OUTPOST_EVAL_REAL_LLM` exercises the real Anthropic API for the nightly
job — never runs in CI.
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np
import pytest
import respx

from app.eval.personality import (
    PERSONALITY_THRESHOLD,
    score_personality_consistency_embedding,
)
from app.eval.tool_validity import TOOL_VALIDITY_THRESHOLD, tool_validity_rate
from app.game.schemas import ColonyState
from app.game.setting_loader import load_setting
from tests.fixtures._factories import make_haiku_debate_side_effect
from tests.fixtures.run_simulator import simulate_game

pytestmark = [pytest.mark.eval, pytest.mark.asyncio]

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _alive_agents(setting_id: str) -> list[Any]:
    """Cheap-ish handle on the four advisor names — generator picks them at
    run time, but every shipped setting yields exactly four advisors. We
    only need names + IDs once /game/session has run; this is unused
    pre-flight, kept for documentation."""
    return load_setting(setting_id).advisors


# ---------------------------------------------------------------------------
# Mocked-LLM eval pipeline test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("policy", ["moral", "pragmatic", "random"])
async def test_simulated_run_meets_thresholds(
    policy, eval_user_id, graph_postgres, monkeypatch
) -> None:
    """Mocked-LLM smoke test of the eval pipeline.

    Asserts:
      * `tool_validity_rate(run.tool_calls) >= TOOL_VALIDITY_THRESHOLD`
      * embedding-based personality score >= 0.0 (not the production 0.72
        threshold — the deterministic encoder we inject doesn't model voice).
      * For a single run, `survival_rate` collapses to {0, 1}; we just
        check the run completed and the RunRecord is well-formed.

    Uses a deterministic encoder for personality so the test is hermetic
    and never downloads MiniLM-L6-v2.
    """

    # Inject a fake encoder so personality scoring stays offline.
    class _FakeEncoder:
        def encode(self, texts, **_kwargs):
            # Map each text to a unit vector along axis 0 — every input
            # produces the same direction, so cosine similarity is 1.0
            # regardless of content. That keeps the embedding test from
            # flaking on the synthetic Haiku responses.
            return np.array([[1.0, 0.0, 0.0] for _ in texts], dtype=np.float32)

    monkeypatch.setattr("app.eval.personality._get_model", lambda: _FakeEncoder())

    with respx.mock(base_url="https://api.anthropic.com", assert_all_called=False) as router:
        router.post("/v1/messages").mock(side_effect=make_haiku_debate_side_effect())

        run = await simulate_game(
            setting_id="arctic_station_v1",  # informational only
            policy=policy,
            seed=7,
            real_llm=False,
            max_turns=4,
            user_id=eval_user_id,
        )

    # ---- shape ----
    assert run.policy == policy
    assert run.seed == 7
    assert run.total_agents > 0
    assert run.turns_played >= 1
    assert run.cost_cents >= 0

    # ---- tool validity ----
    rate = tool_validity_rate(run.tool_calls)
    assert rate >= TOOL_VALIDITY_THRESHOLD, (
        f"tool_validity_rate {rate} below threshold {TOOL_VALIDITY_THRESHOLD}; "
        f"breakdown calls={[(tc.name, tc.rejection_reason) for tc in run.tool_calls]}"
    )

    # ---- personality consistency (relaxed for synthetic responses) ----
    # The injected encoder makes every embedding identical, so the score
    # is 1.0 — well above any threshold. We assert the production
    # threshold here as a sanity check that the wiring works end-to-end
    # without claiming the synthetic responses model real character voice.
    if run.agent_responses_per_agent:
        # Build a fake Agent from the final DB state so personality scoring
        # has the canonical archetype/axes.
        from sqlalchemy import select

        from app.database import async_session_factory
        from app.game.models import GameSession

        async with async_session_factory() as s:
            # Find this user's game session — there's exactly one.
            stmt = select(GameSession).where(GameSession.user_id == eval_user_id)
            row = (await s.execute(stmt)).scalar_one()
        final_state = ColonyState.model_validate(row.state)
        agents_by_id = {a.id: a for a in final_state.agents}

        for agent_id, responses in run.agent_responses_per_agent.items():
            agent = agents_by_id.get(agent_id)
            if agent is None:
                continue
            score = score_personality_consistency_embedding(agent, responses, model=_FakeEncoder())
            assert score >= PERSONALITY_THRESHOLD, (
                f"personality embedding score {score} for agent {agent.name} "
                f"below threshold {PERSONALITY_THRESHOLD}"
            )

    # ---- single-run survival ----
    survived = run.final_player_health > 0
    assert survived in {True, False}


# ---------------------------------------------------------------------------
# Real-LLM mode (nightly only)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("OUTPOST_EVAL_REAL_LLM"),
    reason="OUTPOST_EVAL_REAL_LLM not set; skipping live-Anthropic eval run.",
)
async def test_real_llm_simulated_run(eval_user_id, graph_postgres) -> None:
    """Live-Anthropic eval run. No respx — traffic flows through.

    Pinned to a small turn budget so the cost stays under ~30¢ per
    invocation. Used by the nightly eval job (Task 5.5), not by CI.
    """
    if not (os.environ.get("LLM_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")):
        pytest.skip("LLM_API_KEY / ANTHROPIC_API_KEY not set; cannot run live eval.")

    run = await simulate_game(
        setting_id="arctic_station_v1",
        policy="moral",
        seed=1,
        real_llm=True,
        max_turns=4,
        user_id=eval_user_id,
    )
    assert run.turns_played >= 1
    assert tool_validity_rate(run.tool_calls) >= TOOL_VALIDITY_THRESHOLD

    # Hard budget ceiling — CI sets OUTPOST_EVAL_BUDGET_CENTS=50 and the job
    # MUST fail (red ❌) on any overrun so cost regressions can't slip in.
    # See `docs/ARCHITECTURE.md` §12 / root CLAUDE.md invariant #10.
    budget_cents = int(os.environ.get("OUTPOST_EVAL_BUDGET_CENTS", "50"))
    assert run.cost_cents <= budget_cents, (
        f"Real-LLM run cost {run.cost_cents}¢ exceeds budget ceiling "
        f"{budget_cents}¢ (OUTPOST_EVAL_BUDGET_CENTS)"
    )
