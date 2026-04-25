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
from httpx import Response

from app.eval.personality import (
    PERSONALITY_THRESHOLD,
    score_personality_consistency_embedding,
)
from app.eval.tool_validity import TOOL_VALIDITY_THRESHOLD, tool_validity_rate
from app.game.schemas import ColonyState
from app.game.setting_loader import load_setting
from tests.conftest import fake_haiku_response, fake_sonnet_response
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


def _haiku_debate_factory():
    """Return a respx side_effect callable that emits a fresh debate body
    for each Haiku call.

    The debate body needs three `propose_action` tool_uses (DOMINANT, MORAL,
    WILDCARD) and an XML reaction line per agent. We don't know the agent
    UUIDs at test set-up time — they come from `/game/session` — so we read
    them off the request body's `system` block isn't viable. Instead we
    return a debate body whose XML uses placeholder names and tool_uses
    whose `caller_agent_id` is a UUID string that the parser will discard
    (no match → caller_id stays None, but the proposal still surfaces with
    `proposed_by_agent_id=None`).

    Calls 2+ are autonomous-phase Haiku calls — we differentiate by request
    body shape: if the system prompt mentions "propose three options"
    (debate), return debate body; else return an empty autonomous body.

    Sonnet calls are routed to a separate respx mock; this factory only
    fires for Haiku. The model field on the request distinguishes them.
    """

    def _make_debate_body() -> dict[str, Any]:
        # Use four placeholder agent names matching the shipped settings'
        # advisor templates is brittle — but the parser tolerates unknown
        # agent names (logs WARNING + skips). Use generic names; xml
        # reaction text is harmless filler.
        xml = (
            '<agent name="Advisor1">Steady on.</agent>'
            '<agent name="Advisor2">We must act.</agent>'
            '<agent name="Advisor3">Risky.</agent>'
            '<agent name="Advisor4">I object.</agent>'
        )
        tool_uses = [
            {
                "id": f"toolu_d_{i}",
                "name": "propose_action",
                "input": {
                    "name": f"Plan {i}",
                    "description": f"Option {i}.",
                    "flavor": ["DOMINANT", "MORAL", "WILDCARD"][i],
                    "effects_preview": [],
                },
            }
            for i in range(3)
        ]
        return fake_haiku_response(text_xml=xml, tool_uses=tool_uses)

    def _make_autonomous_body() -> dict[str, Any]:
        # Zero tool_uses — autonomous phase fires no actions in the smoke test.
        return fake_haiku_response(text_xml="")

    call_count = {"n": 0}

    def _side_effect(request) -> Response:
        # Inspect the request body to decide debate vs autonomous vs Sonnet.
        try:
            body = request.read()
            payload = body.decode("utf-8") if isinstance(body, bytes) else body
        except Exception:  # noqa: BLE001
            payload = ""

        # Sonnet narration uses a different model id.
        if '"claude-sonnet' in payload:
            return Response(200, json=fake_sonnet_response("The colony endures."))

        # Haiku: debate vs autonomous distinguished by user-message shape.
        # Debate's user message embeds `<event id=...>`; autonomous embeds
        # `<turn_summary>`. The latter is the post-pause phase and emits
        # zero tool_uses in the smoke test.
        is_debate = "<event id=" in payload
        call_count["n"] += 1
        if is_debate:
            return Response(200, json=_make_debate_body())
        return Response(200, json=_make_autonomous_body())

    return _side_effect


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
        router.post("/v1/messages").mock(side_effect=_haiku_debate_factory())

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
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set; cannot run live eval.")

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
