"""Pipeline-level eval for axis-driven proposal generation.

Loads debate-phase seed fixtures, builds the system blocks via
`app.agent.prompts`, calls Haiku, parses proposals, scores via the
two new judges. Asserts mean scores >= module thresholds.

Marked `eval` — gated by `OUTPOST_EVAL_REAL_LLM=1` upstream (see
`tests/eval/conftest.py`). Without the flag, the test SKIPs.

Three modes:
  - assert iterating mode (default for Phase 2 inner loop): runs against
    the iterating subset (8 seeds), asserts mean axis_diversity >=
    AXIS_DIVERSITY_THRESHOLD and mean axis_attribution >=
    AXIS_ATTRIBUTION_THRESHOLD.
  - assert gating mode: same but over all 30 seeds.
  - baseline-capture mode: dumps proposals + per-seed scores to
    fixtures/axis_baseline.json. Gated additionally by
    OUTPOST_AXIS_BASELINE_CAPTURE=1; runs ONCE before any prompt edit.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.agent.anthropic_client import call_haiku
from app.agent.prompts import (
    build_colony_state_block,
    build_debate_user_message,
    build_personas_block,
    build_preamble_block,
    parse_debate_response,
)
from app.eval.axis_attribution import (
    AXIS_ATTRIBUTION_THRESHOLD,
    score_turn_attribution,
)
from app.eval.axis_diversity import (
    AXIS_DIVERSITY_THRESHOLD,
    ProposalForJudge,
    score_axis_diversity,
)
from app.game.schemas import ColonyState, Event
from app.game.tools import AGENT_TOOL_SCHEMAS

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "axis_seeds"
_BASELINE_PATH = Path(__file__).parent / "fixtures" / "axis_baseline.json"

_REQUIRES_REAL_LLM = pytest.mark.skipif(
    os.environ.get("OUTPOST_EVAL_REAL_LLM") != "1",
    reason="real-LLM eval; set OUTPOST_EVAL_REAL_LLM=1 to enable",
)


@dataclass
class _SeedRunResult:
    seed_id: str
    axis_diversity: float
    axis_attribution: float
    proposals: list[dict]


def _load_seeds(*, iterating_only: bool) -> list[dict]:
    seeds = []
    for path in sorted(_FIXTURES_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        if iterating_only and not data["iterating"]:
            continue
        seeds.append(data)
    return seeds


async def _run_one_seed(seed: dict) -> _SeedRunResult:
    state = ColonyState.model_validate(seed["colony_state"])
    event = Event.model_validate(seed["event"])
    alive_agents = [a for a in state.agents if a.is_alive]
    system_blocks = [
        build_preamble_block(),
        build_personas_block(alive_agents),
    ]
    colony_text = build_colony_state_block(state)["text"]
    user_message = f"{colony_text}\n\n{build_debate_user_message(event)}"

    response = await call_haiku(
        system_blocks=system_blocks,
        user_message=user_message,
        tools=AGENT_TOOL_SCHEMAS,
        max_tokens=2000,
    )
    parsed = parse_debate_response(response, alive_agents)

    proposals_for_judge = []
    proposal_dicts = []
    for tc in parsed.tool_calls:
        if tc.name != "propose_action":
            continue
        if tc.caller_id is None:
            continue
        agent = next((a for a in alive_agents if a.id == tc.caller_id), None)
        if agent is None:
            continue
        proposals_for_judge.append(
            ProposalForJudge(
                advisor_id=tc.caller_id,
                advisor_axes={
                    "ethics": agent.ethics,
                    "temperament": agent.temperament,
                    "loyalty": agent.loyalty,
                },
                name=str(tc.args.get("name", ""))[:80],
                description=str(tc.args.get("description", ""))[:200],
                effects_preview=list(tc.args.get("effects_preview", []))[:5],
                flavor=str(tc.args.get("flavor", "DOMINANT")),
            )
        )
        proposal_dicts.append(
            {
                "advisor_id": str(tc.caller_id),
                "name": tc.args.get("name"),
                "description": tc.args.get("description"),
                "flavor": tc.args.get("flavor"),
                "effects_preview": tc.args.get("effects_preview"),
            }
        )

    if len(proposals_for_judge) != 3:
        return _SeedRunResult(
            seed_id=seed["seed_id"],
            axis_diversity=0.0,
            axis_attribution=0.0,
            proposals=proposal_dicts,
        )

    diversity = await score_axis_diversity(
        event_text=event.body,
        proposals=proposals_for_judge,
    )
    attribution = await score_turn_attribution(
        event_text=event.body,
        proposals=proposals_for_judge,
    )
    return _SeedRunResult(
        seed_id=seed["seed_id"],
        axis_diversity=diversity.score,
        axis_attribution=attribution.score,
        proposals=proposal_dicts,
    )


@_REQUIRES_REAL_LLM
@pytest.mark.eval
async def test_axis_proposals_iterating():
    """Phase 2 inner loop — 8 iterating seeds; gates the prompt change."""
    seeds = _load_seeds(iterating_only=True)
    assert len(seeds) == 8, f"expected 8 iterating seeds, got {len(seeds)}"

    results = []
    for s in seeds:
        results.append(await _run_one_seed(s))

    mean_diversity = sum(r.axis_diversity for r in results) / len(results)
    mean_attribution = sum(r.axis_attribution for r in results) / len(results)

    print(f"\nmean axis_diversity = {mean_diversity:.3f}")
    print(f"mean axis_attribution = {mean_attribution:.3f}")
    for r in results:
        print(
            f"  {r.seed_id}: diversity={r.axis_diversity:.2f}, attribution={r.axis_attribution:.2f}"
        )

    assert mean_diversity >= AXIS_DIVERSITY_THRESHOLD, (
        f"mean axis_diversity {mean_diversity:.3f} < threshold {AXIS_DIVERSITY_THRESHOLD}"
    )
    assert mean_attribution >= AXIS_ATTRIBUTION_THRESHOLD, (
        f"mean axis_attribution {mean_attribution:.3f} < threshold {AXIS_ATTRIBUTION_THRESHOLD}"
    )


@_REQUIRES_REAL_LLM
@pytest.mark.eval
async def test_axis_proposals_gating():
    """30-seed gating eval — runs on PR merge / nightly."""
    seeds = _load_seeds(iterating_only=False)
    assert len(seeds) == 30, f"expected 30 seeds, got {len(seeds)}"

    results = []
    for s in seeds:
        results.append(await _run_one_seed(s))

    mean_diversity = sum(r.axis_diversity for r in results) / len(results)
    mean_attribution = sum(r.axis_attribution for r in results) / len(results)

    print(f"\nmean axis_diversity (n=30) = {mean_diversity:.3f}")
    print(f"mean axis_attribution (n=30) = {mean_attribution:.3f}")

    assert mean_diversity >= AXIS_DIVERSITY_THRESHOLD
    assert mean_attribution >= AXIS_ATTRIBUTION_THRESHOLD


# ---------------------------------------------------------------------------
# Baseline capture — manual one-shot, NOT a normal pytest target.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("OUTPOST_AXIS_BASELINE_CAPTURE") != "1",
    reason="set OUTPOST_AXIS_BASELINE_CAPTURE=1 to capture baseline",
)
@pytest.mark.eval
async def test_capture_baseline():
    """Records iterating-subset judge scores under the CURRENT prompt.

    Run ONCE before any prompt change, then never again. The committed
    baseline JSON serves as the calibration anchor for the threshold.
    """
    seeds = _load_seeds(iterating_only=True)
    results = []
    for s in seeds:
        r = await _run_one_seed(s)
        results.append(
            {
                "seed_id": r.seed_id,
                "axis_diversity": r.axis_diversity,
                "axis_attribution": r.axis_attribution,
                "proposals": r.proposals,
            }
        )
    mean_diversity = sum(r["axis_diversity"] for r in results) / len(results)
    mean_attribution = sum(r["axis_attribution"] for r in results) / len(results)
    payload = {
        "captured_under_prompt_sha": "see git log feat/axis-driven-proposals",
        "n_seeds": len(results),
        "mean_axis_diversity": mean_diversity,
        "mean_axis_attribution": mean_attribution,
        "per_seed": results,
    }
    _BASELINE_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True))
    print(f"\nbaseline written to {_BASELINE_PATH}")
    print(f"mean axis_diversity = {mean_diversity:.3f}")
    print(f"mean axis_attribution = {mean_attribution:.3f}")
