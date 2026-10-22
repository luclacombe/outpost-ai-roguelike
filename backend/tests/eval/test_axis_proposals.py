"""Pipeline-level eval for axis-driven proposal generation.

Loads debate-phase seed fixtures, builds the system blocks via
`app.agent.prompts`, calls the fast model, parses the debate tool calls, runs
them through `prepare_proposals` (engine-rendered effects, the surfaced triple
the player would see), and scores that via the two judges. Asserts mean scores >= module thresholds.

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
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.agent.anthropic_client import call_fast
from app.agent.prepare import prepare_proposals
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
from app.game import setting_loader
from app.game.schemas import ColonyState, Event, ToolCall
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


def build_judge_inputs(
    state: ColonyState,
    debate_tool_calls: list[ToolCall],
    extra_texts: Iterable[str] = (),
) -> tuple[list[ProposalForJudge], list[dict]]:
    """Parsed debate tool calls + seed state -> what the axis judges see.

    Runs the calls through `prepare_proposals` exactly as the `apply_tool_calls`
    node does, so the judges get the surfaced triple (same normalized names,
    same selection) with the engine-rendered `effects_preview`. Any
    `effects_preview` the LLM put in its raw args is ignored. Lines are passed
    through uncut: the judges see exactly what the player sees.

    The harness does not run production's single debate retry, so seeds scored
    0 for a short triple overstate production's short-triple rate.
    """
    setting = setting_loader.load_setting(state.setting_id)
    prepared = prepare_proposals(state, debate_tool_calls, setting, extra_texts)
    agents = {a.id: a for a in state.agents}

    for_judge: list[ProposalForJudge] = []
    dicts: list[dict] = []
    for p in prepared.proposals:
        agent = agents.get(p.proposed_by_agent_id) if p.proposed_by_agent_id else None
        if agent is None:
            continue
        for_judge.append(
            ProposalForJudge(
                advisor_id=agent.id,
                advisor_axes={
                    "ethics": agent.ethics,
                    "temperament": agent.temperament,
                    "loyalty": agent.loyalty,
                },
                name=p.name[:80],
                description=p.description[:200],
                effects_preview=list(p.effects_preview),
                flavor=p.flavor.value,
            )
        )
        dicts.append(
            {
                "advisor_id": str(agent.id),
                "name": p.name,
                "description": p.description,
                "flavor": p.flavor.value,
                "effects_preview": list(p.effects_preview),
            }
        )
    return for_judge, dicts


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

    response = await call_fast(
        system_blocks=system_blocks,
        user_message=user_message,
        tools=AGENT_TOOL_SCHEMAS,
        max_tokens=2000,
    )
    parsed = parse_debate_response(response, alive_agents)

    proposals_for_judge, proposal_dicts = build_judge_inputs(
        state, parsed.tool_calls, parsed.per_agent_text.values()
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


# ---------------------------------------------------------------------------
# Unit test (default suite): seed -> judge-input path, no network.
# ---------------------------------------------------------------------------


def test_judge_inputs_use_engine_rendered_effects_not_llm_preview():
    seed = _load_seeds(iterating_only=True)[0]
    state = ColonyState.model_validate(seed["colony_state"])
    setting = setting_loader.load_setting(state.setting_id)
    agents = [a for a in state.agents if a.is_alive]
    res = next(r for r in setting.resources if state.resources[r.id].value >= 20)

    def call(agent, name, flavor, nested, **extra):
        return ToolCall(
            name="propose_action",
            caller_id=agent.id,
            args={
                "name": name,
                "description": f"{name} now.",
                "flavor": flavor,
                "tool_calls_on_pick": nested,
                **extra,
            },
        )

    def spend(delta):
        return [
            {
                "name": "modify_resource",
                "args": {"resource_id": res.id, "delta": delta, "reason": "r"},
            }
        ]

    calls = [
        call(agents[0], "Spend it", "DOMINANT", spend(-5), effects_preview=["+99 legitimacy"]),
        call(agents[1], "Share it", "MORAL", spend(-3)),
        call(agents[2], "Gamble it", "WILDCARD", spend(-2), effects_preview=["bogus"]),
    ]
    for_judge, dicts = build_judge_inputs(state, calls)

    assert len(for_judge) == 3
    first = for_judge[0]
    assert first.effects_preview == [f"{res.name.lower()} −5"]
    assert first.advisor_id == agents[0].id
    assert first.advisor_axes["ethics"] == agents[0].ethics
    assert first.flavor == "DOMINANT"
    assert dicts[0]["effects_preview"] == [f"{res.name.lower()} −5"]
    assert all(
        "+99" not in line and line != "bogus" for p in for_judge for line in p.effects_preview
    )
    assert all(p.effects_preview for p in for_judge)


def test_judge_inputs_keep_every_rendered_line_uncut():
    seed = _load_seeds(iterating_only=True)[0]
    state = ColonyState.model_validate(seed["colony_state"])
    setting = setting_loader.load_setting(state.setting_id)
    agents = [a for a in state.agents if a.is_alive]
    nested = [
        {"name": "modify_resource", "args": {"resource_id": r.id, "delta": -1, "reason": "r"}}
        for r in setting.resources
    ] + [
        {
            "name": "adjust_grievance",
            "args": {"target_id": str(agents[1].id), "delta": 1, "reason": "r"},
        }
    ]
    assert len(setting.resources) >= 5
    call = ToolCall(
        name="propose_action",
        caller_id=agents[0].id,
        args={
            "name": "Everything at once",
            "description": "d",
            "flavor": "DOMINANT",
            "tool_calls_on_pick": nested,
        },
    )
    prepared = prepare_proposals(state, [call], setting)
    (rendered,) = prepared.proposals
    for_judge, dicts = build_judge_inputs(state, [call])

    assert len(rendered.effects_preview) > 5
    assert any("grievance" in line for line in rendered.effects_preview)
    assert for_judge[0].effects_preview == rendered.effects_preview
    assert dicts[0]["effects_preview"] == rendered.effects_preview


def test_judge_inputs_fewer_than_three_surfaced_when_proposals_missing():
    seed = _load_seeds(iterating_only=True)[0]
    state = ColonyState.model_validate(seed["colony_state"])
    agent = next(a for a in state.agents if a.is_alive)
    bad = ToolCall(
        name="propose_action",
        caller_id=agent.id,
        args={"name": "n", "description": "d", "flavor": "DOMINANT", "tool_calls_on_pick": []},
    )
    for_judge, dicts = build_judge_inputs(state, [bad])
    # A fully-stripped option is still surfaced (as no_effect), never dropped.
    assert len(for_judge) == 1
    assert for_judge[0].effects_preview == ["no immediate effect"]
    assert dicts[0]["effects_preview"] == ["no immediate effect"]
