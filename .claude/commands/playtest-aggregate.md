---
description: Run the LLM playtest meta-aggregator over a run directory. Args: <run-dir-path>
argument-hint: docs/playtest/YYYY-MM-DD
---

You are the meta-aggregator for an Outpost play-test harness invocation. The
harness has already run; raw per-run archives + corpus runs.json are at:

`$ARGUMENTS`

# Your task

Read the harness output and produce a comprehensive `report.md` synthesis that
replaces everything in `$ARGUMENTS/report.md` BELOW the
`<!-- META_AGGREGATOR_OUTPUT_BELOW -->` marker. Do NOT touch the boilerplate
above the marker (it's the canonical honest-limits text and must stay verbatim).

# Inputs

1. `$ARGUMENTS/runs.json` — structured corpus aggregate including per-run summaries,
   axis means, flag aggregates, harness_findings, structural_oracles, position_bias.
2. `$ARGUMENTS/raw/*.md` — per-run narrative archives (LLM-friendly format).
   Read every one before synthesizing.
3. `docs/SPEC.md` — read §§4-7, 10, 11, 14. Map every finding to a SPEC section.
4. `docs/superpowers/specs/2026-04-26-llm-playtest-harness-design.md` —
   the design spec. §0 has the exact honest-limits wording. §8 lists every
   section your output must include.

# Output sections (replace everything below the marker, in this order)

1. **Header** — git SHA, wall clock, total runs, harness_findings summary in one line
2. **Top fixes — ranked 1-10** by severity × prevalence × confidence. Each:
   - **SPEC tag** — the §-numbered SPEC section the finding violates
   - **Group tag** — one of: writing-voice, balance, pacing, advisor-system,
     post-mortem, dashboard, retention
   - **Confidence label** — one of: `high_confidence`, `persona_only`,
     `oracle_only`, `disagreement`
   - **Evidence** — quotes (verbatim from raw/*.md) with `[persona:turn:seed]`
     citations + relevant structural oracle values from runs.json
   - **Proposed fix** — concrete, single-action where possible
   - **Self-rebuttal** (only on item #1) — the strongest counter-argument
3. **Cross-persona disagreements** — axis-by-axis splits with hypothesized cause
4. **Setting-specific findings** — per setting: end-state distribution vs SPEC §10
   bands, tone-coherence average, top 3 tone-break quotes. **Frame as ordinal
   ratios only** (e.g. "3× more tone-break flags in desert vs arctic"). Never as
   absolute predictions.
5. **Cross-run patterns** — opening-line repetition, advisor-line repetition,
   advisor drift across runs. Cite specific runs.
6. **Oracle-only findings** — patterns the structural oracles caught that no
   persona flagged. (Personality-consistency embedding spike on turn N where no
   persona reported `advisor_oc`, etc.)
7. **Blind spots** — patterns YOU surfaced without being explicitly asked for
8. **Per-persona summary** — one section per persona: characteristic quote, axis
   averages from runs.json, what they flagged most, position-bias check (Spearman
   ρ between display position and pick rate from runs.json)
9. **Run roster** — table linking each run to its raw markdown + JSON

# Confidence scaffolding (REQUIRED on every claim)

Every claim must cite:
- (a) prevalence as N/total ("4/6 runs flagged")
- (b) which personas flagged it (with run seeds)
- (c) ≥1 specific quote with turn number
- (d) confidence label per §7 of the design spec

A bare claim without all four is rejected — do not write it.

# Red-team requirements

- "What questions did this harness fail to ask?" — explicit blind-spots section
- "If someone hostile wanted to argue Outpost is unfit to ship, what evidence
  would they cite?" — adversarial framing; surface their strongest arguments
- "What's the strongest counter to your #1 finding?" — self-rebuttal on item #1

# Auto-promote rule

If `runs.json.harness_findings.by_persona[<persona>][<failure_type>] > 3`
(more than half of the persona's 6 runs hit it), elevate that as a top-fix
item with high priority — it's a prompt-or-persona bug, not a game bug.

# Aborted-runs honesty rule

If `len(runs.json.harness_findings.aborted_runs) > 0.05 * total_runs`
(>5% aborted), lead the report (immediately under the boilerplate's
"## Header") with: "**WARNING:** This invocation had a high failure
rate (X of Y runs aborted). Findings below are exploratory; treat
all claims with reduced confidence."

# Final step

After writing the synthesis, verify the report:
- All claims have all four confidence-scaffolding fields
- Every quote is resolvable in `$ARGUMENTS/raw/*.md` (the audit trail)
- Setting-specific claims are framed as ratios, not absolutes
- The boilerplate above `<!-- META_AGGREGATOR_OUTPUT_BELOW -->` is untouched

Then write the synthesis to `$ARGUMENTS/report.md`, replacing only the
content below the marker. Do not add files; do not modify raw/*. The
synthesis IS the deliverable.
