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

1. `$ARGUMENTS/runs.json` — structured corpus aggregate. Key fields:
   - `per_run_summaries[]`: end_state, turns_played, final_legitimacy,
     final_resource_frac, axis_scores (7 axes incl. `meaningful_choices`,
     `strategic_depth`, `loop_clarity`), `difficulty`, `bugs_noticed`,
     `flag_counts` (incl. `confused`, `suspected_bug`), `outcome_expectation`
     ({matched, mismatched, n_a}), `choice_spread` (per-run oracle summary),
     `structural_oracles`, `verdict_failed`.
   - Corpus: `flag_aggregates`, `verdict_axis_means` (overall / by_persona /
     by_setting), `difficulty` (centred scale: 1 trivially easy, 3 well tuned,
     5 unfairly hard; mean, distribution, by_persona, by_setting),
     `continue_tomorrow_rate`, `outcome_expectation` (mismatch_rate),
     `flagged_quotes` (verbatim `suspected_bug` / `confused` quotes with
     persona, turn, seed), `bugs_noticed`, `end_states`, `choice_spread`
     (zero_spread_turn_share, run_ending_option_turn_share,
     picked_rejected_rate), `paired` (per (setting, seed) cell: each
     persona's end state, whether they differ, legitimacy/resource/turn
     ranges), `corpus_oracles`, `harness_findings`.
2. `$ARGUMENTS/raw/*.md` — per-run narrative archives (LLM-friendly format).
   Read every one before synthesizing. Each turn shows what the persona
   picked (with the effects preview it saw), its outcome judgement, flags
   and quotes, then **"Engine options (oracle)"**: the engine's immediate
   result for all three options (→ marks the pick). The persona never saw
   the oracle lines; use them as ground truth for effects-preview honesty
   and choice impact.
3. Optional: a sibling `docs/playtest/<date>-sweep*/summary.json` +
   `summary.md` from `scripts/sweep.py` (scripted policies, same paired
   seeds). If one exists for the same commit, read it; it is the cheap,
   high-n balance signal the personas cannot give.
4. `docs/SPEC.md` — read §§4-7, 10, 11, 14. Map every finding to a SPEC section.
5. `docs/superpowers/specs/2026-04-26-llm-playtest-harness-design.md` —
   the design spec. §0 has the exact honest-limits wording. §8 lists every
   section your output must include.

Seeds are paired: every persona plays the same (setting, seed) cells, and a
cell means the same starting colony and RNG streams. Engine-LLM output is
still sampled, so treat within-cell differences as choice + noise.

# Output sections (replace everything below the marker, in this order)

1. **Header** — git SHA, wall clock, total runs, harness_findings summary in one line
2. **Baseline answers** — one short subsection per question, each opening
   with a one-line verdict, then evidence (numbers from runs.json / sweep,
   ≥1 verbatim quote with `[persona:turn:seed]`), then a confidence label:
   1. *How difficult is the game?* `difficulty` mean/distribution by persona
      and setting, end-state shares vs SPEC §10 bands (persona runs and
      sweep), turns survived, final legitimacy/resource levels.
   2. *Do decisions matter?* `paired` (share of cells where end states
      differ, legitimacy/resource ranges), `choice_spread`
      (zero_spread_turn_share, mean spread), `meaningful_choices` axis,
      oracle lines showing identical or near-identical options.
   3. *Can you win and can you lose?* end-state counts per persona/policy:
      is every terminal state reachable, does any strategy always win or
      always lose, `run_ending_option_turn_share`.
   4. *Is there strategy?* `strategic_depth` axis, whether persona picking
      rules (and sweep policies) separate in outcomes, persona reasoning.
   5. *Does the loop make sense?* `loop_clarity` axis, `confused` flags +
      quotes, `outcome_expectation.mismatch_rate`.
   6. *Are there bugs?* `suspected_bug` quotes, `bugs_noticed`, oracle
      evidence (effects preview vs engine result, rejected calls on picks,
      `picked_rejected_rate`), `harness_findings`. Separate game bugs from
      harness bugs.
   7. *Does the engine work?* tool validity, rejected-call rates, oracle
      errors, aborted runs, end states resolved by the harness, deaths and
      status changes that match the oracle.
3. **Top fixes — ranked 1-10** by severity × prevalence × confidence. Each:
   - **SPEC tag** — the §-numbered SPEC section the finding violates
   - **Group tag** — one of: writing-voice, balance, pacing, advisor-system,
     post-mortem, dashboard, retention
   - **Confidence label** — one of: `high_confidence`, `persona_only`,
     `oracle_only`, `disagreement`
   - **Evidence** — quotes (verbatim from raw/*.md) with `[persona:turn:seed]`
     citations + relevant structural oracle values from runs.json
   - **Proposed fix** — concrete, single-action where possible
   - **Self-rebuttal** (only on item #1) — the strongest counter-argument
4. **Cross-persona disagreements** — axis-by-axis splits with hypothesized cause
5. **Setting-specific findings** — per setting: end-state distribution vs SPEC §10
   bands, tone-coherence average, top 3 tone-break quotes. **Frame as ordinal
   ratios only** (e.g. "3× more tone-break flags in desert vs arctic"). Never as
   absolute predictions.
6. **Cross-run patterns** — opening-line repetition, advisor-line repetition,
   advisor drift across runs. Cite specific runs.
7. **Oracle-only findings** — patterns the structural oracles caught that no
   persona flagged. (Personality-consistency embedding spike on turn N where no
   persona reported `advisor_oc`; zero-spread turns no persona called
   cosmetic; picks whose engine result contradicts the effects preview but
   got `outcome_matched_expectation: true`.)
8. **Blind spots** — patterns YOU surfaced without being explicitly asked for
9. **Per-persona summary** — one section per persona: characteristic quote, axis
   averages from runs.json, what they flagged most, position-bias check (Spearman
   ρ between display position and pick rate from runs.json)
10. **Run roster** — table linking each run to its raw markdown + JSON

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
- All seven baseline questions are answered, each with numbers and a quote
- All claims have all four confidence-scaffolding fields
- Every quote is resolvable in `$ARGUMENTS/raw/*.md` (the audit trail)
- Setting-specific claims are framed as ratios, not absolutes
- The boilerplate above `<!-- META_AGGREGATOR_OUTPUT_BELOW -->` is untouched

Then write the synthesis to `$ARGUMENTS/report.md`, replacing only the
content below the marker. Do not add files; do not modify raw/*. The
synthesis IS the deliverable.
