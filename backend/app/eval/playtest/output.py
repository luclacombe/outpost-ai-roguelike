"""Emits per-run dual archives + corpus runs.json + report.md skeleton.

Per spec §9: dual format per run (markdown for LLM consumption, JSON for
schema-validated machine access). report.md leads with honest-limits
boilerplate; the meta-aggregator (invoked separately via the slash
command) replaces the body below the marker with its synthesis.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.eval.playtest.archive_markdown import render_per_run_markdown
from app.eval.playtest.schemas import PersonaRunVerdict, PersonaTurnOutput
from app.eval.playtest.summaries import corpus_aggregates, per_run_summary

HONEST_LIMITS_BOILERPLATE = """\
# Outpost play-test report

## Honest limits — read first

This harness is a **high-recall, low-precision lint pass** — an *obviousness detector,
not a taste detector*. It catches: tone breaks, archetype slippage, line repetition
across runs, end-state distribution mismatch vs SPEC §10, difficulty-rail invisibility,
counterfactual coverage. It does **not** catch: whether the game is fun, whether the
daily ritual feels good, whether real players return tomorrow.

- Persona role-identification accuracy in the literature caps near 69% (PersonaEval, 2025);
  treat persona-level granularity as soft.
- Forty-two runs from one model are not forty-two independent samples — synthetic
  personas occupy a bounded region of the personality space (UXtweak review, 2026).
- RLHF training reduces or erases negative emotion; tone-break detection underweights
  flat-but-plausible prose.
- Sonnet 4.6 personas evaluating Haiku 4.5 game prose share a model family. Self-preference
  bias inflates absolute scores by an unmeasured offset (arXiv 2410.21819). **Aggregator
  outputs ordinal/relative claims only**; never absolute predictions of real-player rates.
- Synthetic-user believability is itself a risk: it lends false credibility to
  misleading conclusions (UXtweak review, 2026).
"""

MARKER_AGGREGATOR_BEGIN = "<!-- META_AGGREGATOR_OUTPUT_BELOW -->"


@dataclass
class PerRunArchive:
    """Bundle of one run's harness output, ready to be written to disk."""

    run_id: str
    persona_id: str
    setting_id: str
    seed: int
    end_state: str
    turns_played: int
    per_turn_outputs: list[PersonaTurnOutput]
    verdict: PersonaRunVerdict | None
    run_record_summary: dict[str, Any]  # subset of RunRecord fields
    per_run_oracles: dict[str, Any]
    # Sub-bug (iii): when the verdict step fails twice (timeout / crash /
    # parse error), surface the failure message here so the meta-aggregator
    # can flag `verdict_failed` runs distinctly from "game didn't end".
    # `None` means either (a) the verdict succeeded or (b) the verdict
    # was never attempted (e.g. game didn't reach a terminal state).
    verdict_error: str | None = None
    # Engine/oracle context per turn (see summaries.build_turn_context):
    # what was shown, what was picked, what each option does in the engine.
    turn_context: list[dict[str, Any]] = field(default_factory=list)
    final_legitimacy: int | None = None
    final_resource_frac: float | None = None


def _archive_basename(a: PerRunArchive) -> str:
    return f"{a.persona_id}-{a.setting_id}-seed{a.seed}"


def _archive_to_dict(a: PerRunArchive) -> dict[str, Any]:
    return {
        "run_id": a.run_id,
        "persona_id": a.persona_id,
        "setting_id": a.setting_id,
        "seed": a.seed,
        "end_state": a.end_state,
        "turns_played": a.turns_played,
        "per_turn_outputs": [t.model_dump() for t in a.per_turn_outputs],
        "verdict": a.verdict.model_dump() if a.verdict else None,
        "verdict_error": a.verdict_error,
        "run_record_summary": a.run_record_summary,
        "per_run_oracles": a.per_run_oracles,
        "turn_context": a.turn_context,
        "final_legitimacy": a.final_legitimacy,
        "final_resource_frac": a.final_resource_frac,
    }


def write_one_archive(out_dir: Path, archive: PerRunArchive) -> None:
    """Writes raw/<basename>.{md,json} for a single run.

    Called from the orchestrator's per-game epilogue so completed games
    persist on disk before the orchestrator can be interrupted.
    """
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    base = _archive_basename(archive)
    (raw_dir / f"{base}.md").write_text(render_per_run_markdown(archive))
    (raw_dir / f"{base}.json").write_text(
        json.dumps(_archive_to_dict(archive), indent=2, default=str)
    )


def write_per_run_archives(out_dir: Path, archives: list[PerRunArchive]) -> None:
    """Writes raw/<basename>.{md,json} for each run. Batch wrapper around
    `write_one_archive`."""
    for a in archives:
        write_one_archive(out_dir, a)


def write_corpus_artifacts(
    out_dir: Path,
    *,
    archives: list[PerRunArchive],
    findings: dict[str, Any],
    corpus_oracles: dict[str, Any],
    git_sha: str,
    wall_clock_seconds: int,
    harness_version: str = "0.1.0",
) -> None:
    """Writes runs.json (structured corpus data) + report.md (skeleton with
    honest-limits boilerplate; aggregator overwrites body below marker)."""
    out_dir.mkdir(parents=True, exist_ok=True)

    persona_counter: Counter[str] = Counter(a.persona_id for a in archives)
    per_persona_n = max(persona_counter.values()) if persona_counter else 0

    per_run_summaries: list[dict[str, Any]] = []
    for a in archives:
        row = per_run_summary(a)
        row["raw_md_path"] = f"raw/{_archive_basename(a)}.md"
        row["raw_json_path"] = f"raw/{_archive_basename(a)}.json"
        per_run_summaries.append(row)
    aggregates = corpus_aggregates(archives)

    runs_payload: dict[str, Any] = {
        "harness_version": harness_version,
        "git_sha": git_sha,
        "wall_clock_seconds": wall_clock_seconds,
        "personas": sorted(persona_counter.keys()),
        "per_persona_n": per_persona_n,
        "per_run_summaries": per_run_summaries,
        **aggregates,
        "corpus_oracles": corpus_oracles,
        "harness_findings": findings,
    }
    (out_dir / "runs.json").write_text(json.dumps(runs_payload, indent=2, default=str))

    report_skeleton = (
        HONEST_LIMITS_BOILERPLATE
        + "\n\n"
        + "## Header\n\n"
        + f"- Git SHA: `{git_sha}`\n"
        + f"- Wall clock: {wall_clock_seconds // 60} min\n"
        + f"- Total runs: {len(archives)}\n"
        + f"- Personas × runs each: {len(persona_counter)} × {per_persona_n}\n"
        + "\n"
        + MARKER_AGGREGATOR_BEGIN
        + "\n\n"
        + "_(The meta-aggregator overwrites everything below this marker. "
        + "Until then, this section is empty.)_\n"
    )
    (out_dir / "report.md").write_text(report_skeleton)
