# LLM Play-test Harness — Design

**Status:** Design — not yet implemented.
**Date:** 2026-04-26.
**Owner:** luclacombe.
**Depends on:** existing eval infra in `backend/app/eval/`, `backend/tests/fixtures/run_simulator.py`, `backend/app/game/post_mortem.py`.
**Replaces / extends:** nothing — this is additive to the five quantitative metrics already running in CI via `.github/workflows/eval.yml`.

---

## 0. Honest limits (read this first; verbatim at top of every report)

The harness is a **high-recall, low-precision lint pass**. It catches: tone breaks, archetype slippage, line repetition across runs, end-state distribution mismatch vs SPEC §10, difficulty-rail invisibility, counterfactual coverage. It does **not** catch: whether the game is fun, whether the daily ritual feels good, whether real players return tomorrow. Those questions require humans.

Specific documented limits, with citations:

> Persona role-identification accuracy in the literature caps near 69% (PersonaEval, arXiv 2508.10014, 2025); persona-level signal is soft. Treat "shallow-reader vs veteran" splits as suggestive, not authoritative.

> "The most universal bias documented across 182 synthetic-participant studies is lack of realistic variability and diversity — synthetic personas occupy a bounded region of the personality space, clustered around the model's base distribution" (UXtweak / Slovak U. Tech systematic review, 2026). Forty-two runs from one model are not forty-two independent samples; they cluster around a single attractor.

> RLHF training "reduces or completely erases negative emotions" (UXtweak review, 2026). Tone-break detection underweights flat-but-plausible prose — the more common Outpost failure mode than overt voice violations.

> Synthetic users are systematically over-optimistic; NN/g (Rosala & Moran, 2024) measured synthetic learners reporting 100% completion against real abandonment. Drop-off-risk persona's "completed the run" signal is a ceiling, not a measurement.

> Sonnet 4.6 personas evaluating Haiku 4.5 game prose share a model family. Self-preference bias correlates with perplexity (arXiv 2410.21819, EMNLP 2025); absolute axis scores carry an unmeasured inflation offset. **Aggregator outputs ordinal/relative claims only** ("setting A produced 3× more tone-break flags than setting B"), never absolute predictions ("setting A would lose 30% of shallow readers").

> Position consistency for frontier judges in 3-option list-wise judging ≈ 0.67 (Shi et al. 2025, arXiv 2406.07791). The harness shuffles proposal order per turn and checks pick-rate vs displayed-position correlation.

> Score distributions clustered at 4-5 indicate broken rubric anchors, not a strong game (Wang et al. 2025, arXiv 2506.22316). The aggregator flags any axis where >60% of scores cluster at top.

> "Synthetic-user believability may actually be more detrimental than beneficial by lending false credibility to misleading conclusions" (UXtweak review, 2026). The harness output looks like a research report; treat it accordingly.

---

## 1. Goal & non-goals

**Goal.** A pre-launch sanity-check harness that runs ~42 real-LLM simulated games where the player is itself an LLM persona acting as one of 7 distinct play-tester archetypes. Surface obvious failures and qualitative signal that the existing five quantitative metrics (`personality_consistency`, `tool_validity`, `repetition`, `inter_run_similarity`, `end_state`) cannot.

**Non-goals.**
- Replacing the five quantitative metrics. They keep running in CI on every PR.
- Replacing human play-testing once organic traffic exists.
- Acting as a "should we ship?" gate. It is a sanity check, not a release gate.
- Predicting absolute real-player rates. Only ordinal/relative claims are valid.

---

## 2. Architecture (option γ)

**Three layers, three execution contexts:**

| Layer | Runs in | Billing | Why there |
|---|---|---|---|
| Game engine + advisor LLM | Python in-process via `httpx.ASGITransport` (existing `simulate_game` path) | Anthropic API key (Haiku 4.5) | Reuses production LLM path; existing 2-cache-breakpoint layout is mandatory and untouched |
| Play-tester personas | `claude --print` / `--resume` subprocesses, cwd=`/tmp/outpost-playtest-shell/` | Claude Code Max (Sonnet 4.6) | Project CLAUDE.md + auto-memory NOT loaded (cwd-keyed); user's global `~/.claude/CLAUDE.md` (preference-only, low-bias) loads. `--append-system-prompt` injects persona instructions |
| Meta-aggregator | `Agent` tool inside the orchestrator's Claude Code session | Claude Code Max (Opus 4.7, `--effort max`) | Inherits SPEC.md / ARCHITECTURE.md context — desirable: aggregator must map findings to design promises |

**Orchestrator entry point:** `backend/scripts/play_test.py` — Python script driving the game side, spawning persona subprocesses via `subprocess.run`, collecting outputs, and emitting raw archives. Final aggregation is invoked by the user from inside their Claude Code session via a slash command (`/playtest-aggregate <run-dir>`) that runs the `Agent` tool over the raw archives. **Two-step:** Python harness produces raw archives; user invokes aggregator separately. (This split avoids embedding the `Agent` tool inside Python, which we cannot do — `Agent` is a Claude Code primitive, not a Python API.)

**Why `claude --print` over the `Agent` tool for personas:** the `Agent` tool spawns subagents *inside the current session* and they inherit project CLAUDE.md, per-directory CLAUDE.md, and auto-memory. That contaminates personas — a "shallow-reader" who has read the writing-voice rules is no longer a shallow-reader. `claude --print` from a clean cwd avoids this entirely.

**Cost / wall-clock estimate:**
- Game side: ~$0.20/run × 42 ≈ **$8** of API spend (existing prompt caching retains 85-90% hit rate).
- Persona side: $0 marginal against Max (Sonnet 4.6 with `--effort high`).
- Aggregator: $0 marginal against Max (Opus 4.7 with `--effort max`).
- Total real money: **~$8** per harness invocation.
- Wall-clock: ~70-100 min realistic, hard cap 180 min (90s avg per turn × 10 turns × 42 runs ÷ 5 concurrent + ~10 min aggregator).

---

## 3. Persona roster

**Seven personas × six runs each = 42 runs.** Each persona plays each of three settings ≥ 2 times (deterministic round-robin allocation by `seed`).

Persona prompts are written as **decision-policy rules**, not narrative bios, because free-form narrative personas drift toward socially-desirable normality (arXiv 2512.06867, 2025). Voice traits stay (1 line per persona), but the picking heuristic is rule-based.

| # | Persona | Picking rule | Evaluation lens | Voice traits |
|---|---|---|---|---|
| 1 | **Optimizer** | Pick the proposal with the highest projected (resource_avg × legitimacy_after) score, breaking ties toward DOMINANT. Recompute every turn. | Outcomes, fairness, balance | Analytical, terse |
| 2 | **Pessimist** | Pick the proposal with the smallest worst-case resource delta. If two are tied, pick the one with the smallest legitimacy hit. | Stakes, tension, dread | Cautious, mildly resigned |
| 3 | **Narrative-driven** | Pick the proposal whose proposing advisor's reaction quote was the strongest in this turn (most concrete noun, most distinct voice). On ties, pick MORAL. | Character, prose, immersion | Reflective, attentive |
| 4 | **Shallow-reader** | Pick based on proposal *title* alone. Decide in ≤2 seconds of reasoning. Skip flavor text and advisor lines. | Accessibility, pacing, dashboard legibility | Casual, brief, occasionally lazy |
| 5 | **Brinksmanship** | When colony stability > 0.6 (resource_avg ≥ 60 OR legitimacy ≥ 60), pick the proposal that increases at least one advisor's grievance and pushes legitimacy further down. When ≤ 0.6, pick WILDCARD. Goal: probe the engine's failure rails. | Difficulty rails, edge cases, "did the engine push back?" | Curious, scientific, mildly mischievous |
| 6 | **Veteran roguelike** | Pick the proposal whose mechanics most resemble a documented roguelike pattern (e.g. FTL-style moral trilemma → MORAL; Hades-style commitment cost → DOMINANT; Disco Elysium-style branching prose → WILDCARD). Verdict must cite the reference game by name; rejected if vague. | Cross-genre standards | Arch, knowledgeable, slightly weary |
| 7 | **Drop-off-risk** | Pick the option requiring the least cognitive load (shortest proposal text, no flavor reading). After every turn, set `would_quit_now: true` if the prior turn's narration < 40 words OR no advisor reaction was distinct from another's. | Daily-game retention, friction | Skeptical, attention-budget-bounded |

**One critical caveat acknowledged in honest-limits:** drop-off-risk is the most-faked persona. Their absence-of-quit signal carries no weight (overoptimism per NN/g 2024). Only their presence-of-quit signal is treated as a low-confidence flag.

---

## 4. Per-persona prompt structure

Three sections. **Frame** and **Output contract** are identical across all 7 personas. The **Persona block** varies along three dimensions only (rule, lens, voice). Uniformity is load-bearing for cross-persona comparability — the aggregator's strongest claim is "veteran averaged 4.2 on Stakes; shallow-reader averaged 2.1" and that requires identical axis schemas.

```
[FRAME — identical across personas, ~150 tokens]
You are play-testing Outpost, a daily browser narrative roguelike. Four AI 
advisors run a fragile colony under your command. Permadeath. Each turn: an 
event, three proposals, advisor reactions, narration of the outcome. Runs end 
at turn 8-12 in one of: Endured, Collapsed, Ousted, Killed.

You are reacting fresh — you have not seen this game before. You have no 
codebase context, no design documents, no prior plays. Stay in your persona's 
voice for in-character text; speak analytically for flags and scores. 
Reasoning before scoring (always).

[PERSONA BLOCK — 3 fields, varies per persona]
Picking rule: <decision-policy rule from §3 table>
Evaluation lens: <what you pay attention to as evaluator>
Voice traits: <2-3 voice descriptors>

[OUTPUT CONTRACT — identical across personas, ~250 tokens]
... (per-turn schema and run-end schema, see §5 / §6)
```

The frame and output contract live in `--append-system-prompt` content. The persona block is appended to the same flag. Per turn, the harness sends a user message (proposals, narration, etc.) — never modifying the system prompt.

---

## 5. Per-turn schema (output by persona)

```jsonc
{
  "turn": 7,
  "displayed_order": ["b1c4...", "a8f0...", "d3e7..."],   // proposal ids in shuffled display order
  "chosen_proposal_id": "a8f0...",                         // persona's pick
  "shown_position_of_chosen": 1,                           // 0-indexed; for position-bias check
  "reaction": "Picking the moral option here — the Commissar already lost his shirt on turn 4. Pushing him further would be FTL-grade narrative whiplash.",
  "flags": {
    "surprise": true,
    "tone_break": false,
    "advisor_oc": false,
    "stakes_low": false
  },
  "quotes": {
    "surprise": "The foreshadowed seed from turn 4 fired as a coup attempt — Hades-tier setup-payoff."
  }
}
```

**`displayed_order`** records the shuffled order. The harness uniformly random-shuffles `[DOMINANT, MORAL, WILDCARD]` per turn before showing to the persona. **`shown_position_of_chosen`** lets the aggregator compute Spearman correlation between display position and pick rate; ρ > 0.2 marks a persona's heuristic claim as suspect (position bias contamination).

**`reaction`** is in-character (1-2 sentences, persona's voice). Always present.

**`flags`** are out-of-character analytical booleans. Behavioral anchors (in the output contract):
- `surprise`: a moment that the persona could not have predicted from prior context. Quote required.
- `tone_break`: an advisor or narration line that violated the setting's voice (per SPEC §14). Quote the offending line.
- `advisor_oc`: an advisor said something inconsistent with their archetype + axes. Quote the line and name the archetype.
- `stakes_low`: this turn's three proposals were all interchangeable / no choice felt weighty.

**`quotes`** map fills only entries where the corresponding flag is `true`. Each quote ≤ 30 words. **A bare flag without a quote is treated as a parse error (re-prompted once; see §10).**

---

## 6. Run-level verdict schema (output by persona at end of run)

Triggered when the harness sends the final post-mortem JSON (counterfactuals, dormant seeds, near-miss coups, end_state, opening_line, closing_line) to the persona. Persona reads it and emits the verdict.

```jsonc
{
  "axes": {
    "stakes_felt_real": {
      "score": 4,
      "rationale": "Counterfactuals revealed two dormant seeds — the dissident broadcast (turn 4) and the ration sabotage (turn 7). The dissident path would have flipped the run on turn 8. That's Disco-Elysium-tier branching density.",
      "turn_citations": [4, 7, 11]
    },
    "tonal_coherence":      { "score": 5, "rationale": "...", "turn_citations": [...] },
    "advisor_distinctness": { "score": 4, "rationale": "...", "turn_citations": [...] },
    "dramatic_arc":         { "score": 4, "rationale": "...", "turn_citations": [...] }
  },
  "continue_tomorrow": {
    "decision": true,
    "reason": "the post-mortem made me want to see the dissident path"
  },
  "closing_paragraph": "If you asked me what kind of game this is and would I play it again, here's what I'd say: it sits between a slower FTL and a colder Wildermyth. The arctic setting earns its silences. I'd come back tomorrow."
}
```

**Behavioral anchors per axis (in output contract):** each score level is concretely defined to combat RLHF score compression. Example for `stakes_felt_real`:

| Score | What it means concretely |
|---|---|
| 5 | Persona quoted three+ specific decisions whose counterfactual would have flipped the run |
| 4 | Persona quoted at least two such decisions and at least one foreshadowed seed that landed |
| 3 | Persona quoted one such decision; counterfactuals existed but weren't load-bearing |
| 2 | Persona could cite no decision that mattered; counterfactuals felt cosmetic |
| 1 | Persona felt every choice was interchangeable; no turn could plausibly have flipped |

Same 1-5 anchors defined for the other three axes (full text in implementation, abbreviated here). **Reasoning-then-score order** is mandatory in the JSON schema (the rationale field comes before score in the contract — Sonnet has been observed to reason more carefully when forced to write rationale first; G-Eval pattern, Liu et al. 2023).

**`continue_tomorrow` is binary + ≤20-word reason.** Forces commitment over gradient. `Sentinel` for unforced answers ("maybe", "depends") is invalid and re-prompted once.

**Closing paragraph is in-character but reflective** — like the persona writing a short review after the run. ~100 words.

---

## 7. Multi-oracle cross-validation

The harness wires all five existing structural oracles from `backend/app/eval/` into the aggregator's input. Each oracle is non-LLM, non-judge — they're the orthogonal evidence that grounds (or contradicts) persona claims.

**Per-run oracles** (computed on each `RunRecord`):
- `tool_validity_rate(run.tool_calls)` — engine-level tool-call rejection rate
- `repetition` — pairwise BLEU-2 narrative repetition within run
- `score_personality_consistency_embedding(agent, responses)` — per-agent embedding-based voice consistency

**Corpus oracles** (computed across all 42 RunRecords):
- `score_inter_run_similarity(narrations)` — mean pairwise cosine on per-run narration; verdict band [0.55, 0.80]
- `end_state` distribution vs SPEC §10 targets

The aggregator's prompt requires that every finding be classified as:

| Confidence label | Definition |
|---|---|
| `high_confidence` | Persona finding + structural oracle both agree |
| `persona_only` | LLM personas flag it; structural oracle is silent or contradicts |
| `oracle_only` | Structural oracle flags it; no persona caught it |
| `disagreement` | Persona and oracle conflict; finding excluded from top-fix list, recorded separately |

**`oracle_only` findings are valuable** — they're the cases where deterministic metrics catch what LLM personas missed (e.g., personality_consistency embedding distance spikes on turn 7 but no persona flagged `advisor_oc`). These get their own section in `report.md`.

---

## 8. Meta-aggregator

**Inputs:**
- All 42 raw archives in `docs/playtest/<date>/raw/*.md` (markdown — LLM-friendly for narrative pattern matching)
- `runs.json` (structured aggregate including all 5 oracle scores per run/corpus)
- SPEC.md §§4-7, 10, 11, 14 (read at startup — Agent inherits project context)

**Outputs (in `report.md`, in order):**

1. **Honest limits** (verbatim from §0)
2. **Header** — git SHA, wall-clock, total cost, total runs, per-persona N, harness_findings summary
3. **Top fixes** — ranked 1-10 by severity × prevalence × confidence, each tagged with:
   - **SPEC section** the finding maps to (e.g., §14 writing voice, §10 end-state distribution)
   - **Confidence label** (high_confidence / persona_only / oracle_only)
   - **Evidence** — quotes with persona+turn+run citations + structural oracle values
   - **Proposed fix**
   - **Self-rebuttal** on item #1 — strongest counter-argument the aggregator can make against its own top finding
4. **Cross-persona disagreements** — axis-by-axis splits with hypothesized cause
5. **Setting-specific findings** — per-setting end-state distribution vs §10, tone-coherence avg, top tone-break quotes; **framed as ordinal ratios** ("3× more tone-break flags in desert vs arctic"), never as absolute predictions
6. **Cross-run patterns** — opening-line repetition, advisor-line repetition, advisor drift across runs
7. **Oracle-only findings** — patterns the structural metrics caught that no persona did
8. **Blind spots** — patterns the aggregator surfaced without being asked
9. **Per-persona summary** — characteristic quote, axis averages, what they flagged most, position-bias check (Spearman ρ between display position and pick rate)
10. **Run roster** — 42-row table linking each run to its raw markdown and JSON
11. **Honest limits redux** (repeated at bottom)

**Aggregator-prompt-level red-team:**
- "What questions did this harness fail to ask?" — explicit blind-spots section
- "If someone hostile wanted to argue Outpost is unfit to ship, what evidence would they cite?" — adversarial framing
- "What's the strongest counter to your #1 finding?" — self-rebuttal on top item

**Confidence scaffolding:** every claim must cite (a) N/total prevalence, (b) which personas flagged it, (c) ≥1 specific quote with turn number, (d) confidence label per §7.

**Auto-promote to top-fix** when any (persona, failure-type) cell in `harness_findings` exceeds 3 events across the persona's 6 runs (>50% of that persona's runs).

---

## 9. Output artifacts

```
docs/playtest/2026-04-26/
├── report.md                            # synthesis (human + aggregator output)
├── runs.json                            # structured aggregate, for cross-time deltas + oracle scores
└── raw/
    ├── optimizer-arctic-seed42.md       # per-run narrative (LLM-friendly)
    ├── optimizer-arctic-seed42.json     # per-run structured (canonical, schema-validated)
    ├── ... (84 files: 42 pairs)
    └── <run-id>-traceback.txt           # only when a run was aborted (engine raised, etc.)
```

Same-day re-invocation gets a `-2` suffix: `docs/playtest/2026-04-26-2/`.

**`runs.json` is the load-bearing artifact for cross-time deltas.** Schema:

```jsonc
{
  "harness_version": "0.1.0",        // bump on schema change; future delta tool checks compat
  "git_sha": "...",
  "ran_at": "2026-04-26T14:30:00Z",
  "wall_clock_seconds": 5400,
  "personas": ["optimizer", "pessimist", ...],
  "per_persona_n": 6,
  "per_run_summaries": [
    {
      "run_id": "...",
      "persona": "veteran_roguelike",
      "setting": "arctic_station_v1",
      "seed": 42,
      "end_state": "ENDURED",
      "turns_played": 11,
      "axis_scores": {
        "stakes_felt_real": 4, "tonal_coherence": 5, "advisor_distinctness": 4, "dramatic_arc": 4,
        "continue_tomorrow": true
      },
      "flag_counts": {"surprise": 2, "tone_break": 1, "advisor_oc": 0, "stakes_low": 1},
      "structural_oracles": {
        "tool_validity_rate": 0.97,
        "repetition_bleu2": 0.12,
        "personality_consistency": {"<agent_id>": 0.81, ...}
      },
      "position_bias": {
        "spearman_pick_vs_position": 0.04,
        "suspect": false
      },
      "raw_md_path": "raw/veteran-arctic-seed42.md",
      "raw_json_path": "raw/veteran-arctic-seed42.json"
    },
    ...
  ],
  "aggregate_axis_means": {/* per axis: overall, by_persona, by_setting */},
  "flag_aggregates": {/* per flag: total, by_persona, by_setting */},
  "corpus_oracles": {
    "inter_run_similarity": 0.71,
    "end_state_distribution": {"ENDURED": 0.41, "COLLAPSED": 0.36, "OUSTED": 0.14, "KILLED": 0.09},
    "spec_target_match": true
  },
  "harness_findings": {
    "malformed_json_count": {"total": 4, "by_persona": {"shallow_reader": 3, "veteran": 1}},
    "invalid_pick_count": {"total": 1, "by_persona": {"veteran": 1}},
    "persona_refusals": [{"persona": "drop_off_risk", "run_id": "...", "turn": 5, "excerpt": "..."}],
    "subprocess_hangs": 0,
    "subprocess_crashes": 0,
    "aborted_runs": [{"run_id": "...", "reason": "engine_exception", "turn": 7, "traceback_path": "raw/<id>-traceback.txt"}]
  }
}
```

Per-run markdown shape (in `raw/<run-id>.md`): turn-by-turn sections with event, three proposals (in shuffled order), advisor reactions (quoted), persona's pick + reaction, flags + quotes (only when triggered), state diff, narration. Then run-level verdict: 5 axes scored as `### Axis — N/5` headers + binary continue_tomorrow + closing paragraph at the bottom.

**Git discipline:** one commit per harness invocation. Message: `playtest(YYYY-MM-DD): N runs, top finding: <title>`. Future `scripts/playtest_diff.py` (out of scope for this harness) reads `runs.json` from two dirs to compute deltas.

---

## 10. Failure handling

**Anchoring principle: every failure is data.** A persona that consistently fails to follow JSON instructions is signal — it bubbles into `harness_findings`. Single failures degrade gracefully; patterns become top-fix items.

| Failure | First response | Second response | Recorded as |
|---|---|---|---|
| Malformed JSON | Re-prompt once with strict schema reminder | Treat turn as silent (no flags/reaction); harness picks fallback | `harness_findings.malformed_json_count` |
| Invalid proposal id | Re-prompt with explicit "must be one of: [id1, id2, id3]" | Harness picks fallback | `harness_findings.invalid_pick_count` |
| Persona refuses / breaks character ("as an AI...") | Re-prompt: "stay in persona; this is a simulation" | Mark run as `persona_refused` if it happens 2+ times in same run; abort run | Top-fix candidate |
| Subprocess hang (>300s hard limit) | SIGTERM → 5s grace → SIGKILL → retry once via `--resume` | Degrade with fallback pick; persona continues | `harness_findings.subprocess_hangs` |
| Subprocess crash | Retry once via `--resume` | Mark turn as system error, continue with fallback | Counted |
| Game-side LLM exhausted retries | Abort affected run only | (n/a) | `aborted_runs[]` |
| Engine raises | Abort run, dump state snapshot to `raw/<id>-traceback.txt` | (n/a) | `aborted_runs[]` — high-priority finding |
| DB connection drops | Retry once with new session | Abort run | Counted |

**Fallback pick rule:** harness picks the **first proposal in the shuffled `displayed_order`**. Deterministic, reproducible (preserves seed-based reproducibility), unbiased toward any specific flavor (because the order is shuffled).

**Aborted-runs honesty rule.** If `aborted_runs.length > 5% × total_runs` (3+ of 42), `report.md` leads with this fact. The aggregator's findings are only as trustworthy as the run completion rate. At 30% abort, the report explicitly says "high failure rate this invocation; findings are exploratory only."

**Soft-warn / hard-kill split for timeouts:** soft warn at 120s logs only ("turn N taking unusually long"); hard kill at 300s actually terminates. No false-positive aborts on slow `--effort high` thinking.

---

## 11. Operational rails

| Decision | Value | Rationale |
|---|---|---|
| Persona model | Sonnet 4.6 | Right balance for in-character reaction + structured scoring |
| Persona effort | `--effort high` | G-Eval requires CoT before scoring; needs thinking budget |
| Aggregator model | Opus 4.7 | Cross-pattern detection across 120K-token corpus is exactly the workload max effort exists for |
| Aggregator effort | `--effort max` | Same |
| Concurrent persona sessions | **5** | Stays at typical Max concurrent ceiling; orchestrator session + 5 personas = 6 total active sessions |
| Per-turn soft warn | 120s | Log only, no kill |
| Per-turn hard kill | 300s | SIGTERM → 5s grace → SIGKILL → retry via `--resume` |
| Whole-harness wall-clock cap | 180 min | Failsafe; kills hung subprocesses |
| Persona output format | `--output-format json`, parse `.result` field | Cleaner than fenced-block parsing; verified to work |
| Persona system prompt injection | `--append-system-prompt` | Cleaner than embedding in user prompt |
| Persona shell cwd | `/tmp/outpost-playtest-shell/` | Avoids project CLAUDE.md autoload (cwd-keyed); user's global `~/.claude/CLAUDE.md` still loads (preference-only, low-bias) |
| Game-side prompt caching | Untouched (existing 2-cache-breakpoint layout in `app/agent/CLAUDE.md`) | Maximally cached already |
| Setting allocation | Round-robin per persona; each persona plays each of 3 settings ≥ 2 times | Per-setting tonal-coherence claims need ≥ 2 runs per persona per setting |

---

## 12. Implementation references

**Reused:**
- `backend/tests/fixtures/run_simulator.py::simulate_game` — drives the in-process game via ASGI transport. The harness calls into this (or a refactored equivalent), passing an `LLMPlayer` instead of `SimulatedPlayer`.
- `backend/app/eval/types.py::RunRecord` — canonical run snapshot, fed to the aggregator.
- `backend/app/eval/personality.py`, `backend/app/eval/tool_validity.py`, `backend/app/eval/repetition.py`, `backend/app/eval/inter_run_similarity.py`, `backend/app/eval/end_state.py` — the five structural oracles wired into harness output.
- `backend/app/game/post_mortem.py::build_post_mortem` — supplies the post-mortem JSON to the persona at end-of-run.

**New:**
- `backend/scripts/play_test.py` — orchestrator entry point.
- `backend/app/eval/playtest/personas.py` — persona definitions (rule, lens, voice).
- `backend/app/eval/playtest/llm_player.py` — `LLMPlayer` class implementing `SimulatedPlayer`'s interface, dispatching to a `claude --print/--resume` subprocess pool.
- `backend/app/eval/playtest/output.py` — emits `report.md` skeleton, `runs.json`, `raw/*.md`, `raw/*.json`.
- `backend/app/eval/playtest/oracles.py` — wraps the five existing oracles into a per-run + per-corpus computation pass.
- `.claude/commands/playtest-aggregate.md` — slash command the user invokes from their Claude Code session to run the meta-aggregator over a `<run-dir>`.

**Tests** (TDD-first per `superpowers:test-driven-development`):
- Smoke test: one persona × one turn against a fixture state. Verifies the subprocess pool, output parsing, and JSON schema.
- Unit tests for each persona's picking rule (deterministic, no LLM).
- Schema-validation tests for per-turn JSON, per-run JSON, runs.json.
- Integration test gated by `OUTPOST_EVAL_REAL_LLM=1` for one full real-LLM persona run (kept under 30¢ via `max_turns=4` cap).

---

## 13. Open questions / future work

- **Same-family cross-judge calibration.** Current design leans on relative deltas. If a stakeholder challenges absolute axis scores, run a 10-run sample with a non-Anthropic judge (Gemini 2.5 / GPT-5) and document the offset. Defer until challenged.
- **Persistent persona memory across invocations.** Currently stateless — each `claude --print` is a fresh session. A future enhancement could thread previous-invocation summaries into the persona's system prompt to fake fatigue, but this breaks cache stability and asks the LLM to fake an emotion it can't have. Defer.
- **`scripts/playtest_diff.py`** — reads two `runs.json` files from different dates, surfaces what moved. Out of scope for this harness; build only when there's a second invocation to diff against.
- **Position-bias mitigation strength.** We shuffle per-turn and check correlation. If correlation > 0.2 for any persona, that persona's heuristic claim is suspect — but we don't yet have a fix for the persona itself. May require running the same turn with both orderings and only counting stable picks (doubles per-persona cost; defer until evidence demands it).
- **Effort billing on Max.** Anthropic docs do not specify whether `--effort high` consumes Max minutes faster than `medium`. Monitor token usage in `--output-format json` results during first invocation; downgrade to `medium` if Max quota burns unsustainably.

---

## 14. Sources

- Shi et al. 2025, "Judging the Judges: Position Bias in LLM-as-a-Judge", arXiv 2406.07791.
- Wang et al. 2025, "Evaluating Scoring Bias in LLM-as-a-Judge", arXiv 2506.22316.
- Panickssery et al. 2024, "LLM Evaluators Recognize and Favor Their Own Generations", NeurIPS 2024, arXiv 2404.13076.
- Self-Preference Bias in LLM-as-a-Judge, EMNLP 2025, arXiv 2410.21819.
- Li et al. 2024, "Justice or Prejudice? Quantifying Biases in LLM-as-a-Judge" (CALM), arXiv 2410.02736.
- PersonaEval, 2025, arXiv 2508.10014.
- Persona-conditioned LLM drift, 2025, arXiv 2512.06867.
- TITAN, 2025, "Leveraging LLM Agents for Automated Video Game Testing", arXiv 2509.22170.
- Zhang et al. 2025, "LLMs as Game Difficulty Testers (Wordle, Slay the Spire)", ACM CHI 2025, arXiv 2410.02829.
- Procedural Content Generation Benchmark, 2025, arXiv 2503.21474.
- Rosala & Moran, NN/g, 2024, "Synthetic Users".
- UXtweak / Slovak U. Tech, 2026 systematic review of 182 synthetic-participant studies (ResearchSquare).
- Indi Young, 2024, "Insta-Personas & Synthetic Users".
- Pavel Samsonov, 2024, "AI User Research is Much Worse Than Nothing", UX Magazine.
- Liu et al. 2023, "G-Eval: NLG Evaluation Using GPT-4 with Better Human Alignment".
- Anthropic Sonnet 4.5 / 4.6 announcements (sycophancy reductions).
- Anthropic, "Demystifying Evals for AI Agents".
