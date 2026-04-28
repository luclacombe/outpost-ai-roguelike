"""Wraps the five existing structural oracles in app/eval/ into per-run +
per-corpus computations consumed by the meta-aggregator.

Per spec §7 — these are the orthogonal evidence base. The aggregator marks
every persona finding as high_confidence / persona_only / oracle_only /
disagreement based on whether the structural oracles agree.

Per-run oracles (one RunRecord input):
- tool_validity_rate
- repetition_bleu2 (within-run BLEU-2 narrative repetition)
- personality_consistency (per-agent embedding similarity)

Corpus oracles (list[RunRecord] input):
- inter_run_similarity_by_setting (the underlying metric requires single-
  setting input — we group and call per setting)
- end_state_distribution + spec_target_match (vs SPEC §10 bands)
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any
from uuid import UUID

from app.eval.end_state import end_state_distribution_check
from app.eval.inter_run_similarity import inter_run_narrative_similarity
from app.eval.personality import score_personality_consistency_embedding
from app.eval.repetition import narrative_repetition_index
from app.eval.tool_validity import tool_validity_rate
from app.eval.types import RunRecord
from app.game.schemas import Agent


def compute_per_run_oracles(
    run: RunRecord,
    *,
    agents: list[Agent],
) -> dict[str, Any]:
    """Compute per-run oracles for one RunRecord.

    `agents` must come from the final game-session state.
    """
    by_agent_id: dict[UUID, Agent] = {a.id: a for a in agents}
    personality_per_agent: dict[str, float] = {}
    for agent_id, responses in run.agent_responses_per_agent.items():
        agent = by_agent_id.get(agent_id)
        if agent is None:
            continue
        personality_per_agent[str(agent_id)] = float(
            score_personality_consistency_embedding(agent, responses)
        )

    return {
        "tool_validity_rate": float(tool_validity_rate(run.tool_calls)),
        "repetition_bleu2": float(narrative_repetition_index(run.narration_per_turn)),
        "personality_consistency": personality_per_agent,
    }


def compute_corpus_oracles(runs: list[RunRecord]) -> dict[str, Any]:
    """Compute corpus-level oracles across all runs.

    `inter_run_narrative_similarity` requires all runs to share the same
    `setting_id`, so we group by setting and call it per group. Returns a
    per-setting dict alongside the corpus-wide end-state distribution.
    """
    by_setting: dict[str, list[RunRecord]] = defaultdict(list)
    for r in runs:
        by_setting[r.setting_id].append(r)

    inter_run_by_setting: dict[str, dict[str, Any]] = {}
    for setting_id, group in by_setting.items():
        result = inter_run_narrative_similarity(group)
        inter_run_by_setting[setting_id] = {
            "n_runs": result.n_runs,
            "mean_pairwise_cosine": float(result.mean_pairwise_cosine),
            "band": list(result.band),
            "verdict": result.verdict,
        }

    end_state_result = end_state_distribution_check(runs)
    end_state_distribution = {
        state.value: freq for state, freq in end_state_result.observed.items()
    }
    spec_target_match = len(end_state_result.out_of_band) == 0

    return {
        "inter_run_similarity_by_setting": inter_run_by_setting,
        "end_state_distribution": end_state_distribution,
        "spec_target_match": spec_target_match,
        "n_finished_runs": end_state_result.n_runs,
    }
