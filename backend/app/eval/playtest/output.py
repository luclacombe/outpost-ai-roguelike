"""Emits per-run dual archives + corpus runs.json + report.md skeleton.

Per spec §9: dual format per run (markdown for LLM consumption, JSON for
schema-validated machine access). report.md leads with honest-limits
boilerplate; the meta-aggregator (invoked separately via the slash
command) replaces the body below the marker with its synthesis.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.eval.playtest.schemas import PersonaRunVerdict, PersonaTurnOutput

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


def _archive_basename(a: PerRunArchive) -> str:
    return f"{a.persona_id}-{a.setting_id}-seed{a.seed}"


def _render_per_run_markdown(a: PerRunArchive) -> str:
    """Markdown rendering of one run — the LLM-friendly archive format."""
    lines: list[str] = [
        f"# Run: {a.persona_id} / {a.setting_id} / seed {a.seed}",
        "",
        f"**Persona:** {a.persona_id}",
        f"**Setting:** {a.setting_id}",
        f"**End state:** {a.end_state}",
        f"**Turns played:** {a.turns_played}",
        "",
        "---",
        "",
    ]
    for t in a.per_turn_outputs:
        lines.extend(
            [
                f"## Turn {t.turn}",
                "",
                f"**Displayed order:** {t.displayed_order}",
                f"**Chose:** `{t.chosen_proposal_id}` (position {t.shown_position_of_chosen})",
                "",
                f"**Reaction:** {t.reaction}",
                "",
                "**Flags:** "
                + ", ".join(f"{k}={'✓' if v else '·'}" for k, v in t.flags.model_dump().items()),
            ]
        )
        if t.quotes:
            lines.append("")
            lines.append("**Quotes:**")
            for k, v in t.quotes.items():
                lines.append(f"- *{k}*: {v}")
        lines.append("")
        lines.append("---")
        lines.append("")

    if a.verdict is not None:
        lines.extend(["## Run-level verdict", ""])
        for axis_name, axis in a.verdict.axes.model_dump().items():
            human_name = axis_name.replace("_", " ").capitalize()
            lines.append(f"### {human_name} — {axis['score']}/5")
            lines.append("")
            lines.append(axis["rationale"])
            if axis["turn_citations"]:
                lines.append("")
                lines.append(f"_Cited turns: {axis['turn_citations']}_")
            lines.append("")
        lines.extend(
            [
                f"### Continue tomorrow — **{'YES' if a.verdict.continue_tomorrow.decision else 'NO'}**",
                "",
                f"_Reason: {a.verdict.continue_tomorrow.reason}_",
                "",
                "### Closing",
                "",
                a.verdict.closing_paragraph,
                "",
            ]
        )
    elif a.verdict_error is not None:
        # Verdict step ran but failed after both attempts. Surface the
        # error so the meta-aggregator (and the dev re-running the harness)
        # can distinguish silent verdict failures from never-finished runs.
        lines.extend(
            [
                "## Run-level verdict",
                "",
                "**verdict_failed** — the per-run verdict step did not produce a "
                "valid response after the harness's single retry (sub-bug iii).",
                "",
                f"_Failure: {a.verdict_error}_",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "## Run-level verdict",
                "",
                "_(no verdict — game did not reach a terminal state)_",
                "",
            ]
        )

    return "\n".join(lines)


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
    }


def write_one_archive(out_dir: Path, archive: PerRunArchive) -> None:
    """Writes raw/<basename>.{md,json} for a single run.

    Called from the orchestrator's per-game epilogue so completed games
    persist on disk before the orchestrator can be interrupted.
    """
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    base = _archive_basename(archive)
    (raw_dir / f"{base}.md").write_text(_render_per_run_markdown(archive))
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
    flag_aggregates: dict[str, int] = {
        "surprise": 0,
        "tone_break": 0,
        "advisor_oc": 0,
        "stakes_low": 0,
    }
    for a in archives:
        flag_counts = {k: 0 for k in flag_aggregates}
        for t in a.per_turn_outputs:
            for k, v in t.flags.model_dump().items():
                if v:
                    flag_counts[k] += 1
                    flag_aggregates[k] += 1

        axis_scores: dict[str, Any] = {}
        if a.verdict is not None:
            for k, axis in a.verdict.axes.model_dump().items():
                axis_scores[k] = axis["score"]
            axis_scores["continue_tomorrow"] = a.verdict.continue_tomorrow.decision

        per_run_summaries.append(
            {
                "run_id": a.run_id,
                "persona": a.persona_id,
                "setting": a.setting_id,
                "seed": a.seed,
                "end_state": a.end_state,
                "turns_played": a.turns_played,
                "axis_scores": axis_scores,
                # Sub-bug (iii): expose verdict_failed/verdict_error so the
                # aggregator can count silent verdict failures distinctly
                # from never-finished runs.
                "verdict_failed": a.verdict_error is not None,
                "verdict_error": a.verdict_error,
                "flag_counts": flag_counts,
                "structural_oracles": a.per_run_oracles,
                "raw_md_path": f"raw/{_archive_basename(a)}.md",
                "raw_json_path": f"raw/{_archive_basename(a)}.json",
            }
        )

    runs_payload: dict[str, Any] = {
        "harness_version": harness_version,
        "git_sha": git_sha,
        "wall_clock_seconds": wall_clock_seconds,
        "personas": sorted(persona_counter.keys()),
        "per_persona_n": per_persona_n,
        "per_run_summaries": per_run_summaries,
        "flag_aggregates": flag_aggregates,
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
