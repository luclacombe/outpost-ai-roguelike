# Axis-driven proposal generation — design

**Date:** 2026-05-02
**Status:** **Closed — premise not supported by measurement.** What shipped:
the eval infrastructure as regression armor (two Haiku judges + 30 seeds +
pipeline test). Prompt unchanged.
**Touches (final):** `backend/app/eval/` (new judges), `backend/tests/eval/`
**PR target (final):** `feat(eval): axis-diversity regression armor`

---

## 0. Outcome (added 2026-05-02 after measurement)

The original premise — that the proposal-construction prompt produces flat
decisions because flavor (DOMINANT/MORAL/WILDCARD) is the construction
seed instead of axes — turned out **not to be supported by measurement**.

Real-LLM baselines under the *current* (un-edited) prompt:

| Sample | n | mean axis_diversity | mean axis_attribution |
|---|---:|---:|---:|
| Iterating (all aftermath) | 8 | **0.917** | **0.833** |
| Gating (mixed fresh+aftermath) | 30 | **0.933** | **0.922** |

The threshold floor in both judges is **0.70**. The current prompt clears
it by **~0.20+** even on conditions designed to mirror the corpus's
flat-decision turns (turn 6, decayed legitimacy, advisors carrying
prior-attempt memory, repeat events with `EventOrigin.SEED_FIRED`).

**Why the corpus showed flat decisions then:** the failure mode lives at
the *engine* layer, not the prompt — the event scheduler kept cycling
the same template (e.g. "Recycler unit 3 anomaly" four turns running)
and the advisors had no fresh problem to read differently. A prompt
rewrite cannot fix orchestration.

**What shipped instead:**

1. `app/eval/axis_diversity.py` — Haiku judge classifying three proposals
   into §6.2 axis-quadrants; score = distinct_quadrants / 3.
2. `app/eval/axis_attribution.py` — Haiku judge scoring whether each
   proposal embodies its authoring advisor's axes; binary 1/0, turn-
   level mean.
3. `scripts/build_axis_seeds.py` — deterministic generator for 30 fixture
   seeds (15 fresh dilemmas + 15 aftermath/repeat conditions, 8 marked
   iterating-subset).
4. `tests/eval/test_axis_proposals.py` — pipeline-level eval (debate-only,
   real-LLM, gated by `OUTPOST_EVAL_REAL_LLM=1`) with iterating /
   gating / baseline-capture modes.
5. `tests/eval/fixtures/axis_baseline.json` — committed baseline so
   future-Claude can see what "good" looks like.

These artifacts are useful as **regression armor**: any future prompt
edit that drops axis_diversity or axis_attribution below 0.70 mean over
the 30-seed gating set will fail the eval. The thresholds intentionally
sit ~0.20 below today's measured baseline so they catch a real
regression (a 0.20-point drop on either judge), not noise.

**What did NOT ship:**

- The prompt rewrite. The original §3 design (PROPOSAL CONSTRUCTION,
  AXIS VALUE-RULES, AXIS + GRIEVANCE → POSTURE blocks) is preserved
  below for reference but was not applied — the data did not support it.

**Follow-ups (separate PRs):**

- **Engine event-pool rotation.** The corpus's "Four turns on the same
  recycler" pattern is engine behavior. Add: avoid back-to-back
  templates from the same category when ≥3 unfired alternatives
  exist; rotate aftermath events too. This is the actual fix for the
  flat-decision symptom the original spec was trying to address.
- **Self-preference bias check.** The judges and the debate prompt are
  both Haiku 4.5. Re-run a sub-sample with Sonnet judging Haiku to see
  if the absolute scores hold (spec §4 risks already noted this).

The original problem analysis below remains correct as a record of what
we *thought* was going on; the §3 design is preserved as a record of
what we *would have built* if the eval had backed the premise. Treat
sections 1–9 below as the historical brainstorming output, not the
shipped contract.

---

## 1. Problem

The proposal-generation prompt asks the model for *option-shape diversity* (DOMINANT / MORAL / WILDCARD), not *worldview diversity*. The flavor taxonomy is orthogonal to the §6.1 personality axes, so a turn can produce three on-flavor options that all share the same axis-quadrant — three engineering variants of "fix the recycler", different costs, same problem-frame. The player experiences this as a flat decision: efficacy ranking, not a moral choice.

Corpus evidence (`docs/playtest/2026-04-30-final/`):

- *Drop_off_risk arctic turn 3* — three options were "stay warm in the blizzard" (seal further / generator gamble / medicine pass). Tactic-diverse, frame-identical.
- *Shallow_reader orbital turn 5* — DOMINANT and MORAL both "fix unit 3"; only the WILDCARD broke the loop.
- *Optimizer desert turn 3* — three security responses (lockdown / patrol / honeypot). Nobody framed the break-in as a trade-relations problem or a moral question.
- *Brinksmanship arctic turn 4 (counter-example)* — Vera's DOMINANT proposed letting drifters die in the ice (Pragmatist+Cautious axis: ration-math says they're already lost) while three advisors verbally said "cannot abandon them." That's the working pattern: *one axis genuinely re-frames what the problem even is.*

Symptom — what to look for in evals: same problem-frame across the three options, all three live in the same §6.2 axis-quadrant, advisor-distinctness scores in the 3–4/5 band where 4–5/5 is target. Direct corpus signal: report.md §5.1 (Conscience embedding variance 2× others — broad value-line bleeds into per-turn drift).

## 2. Goals & non-goals

**Goals:**
- Each turn's three proposals span at least 2 different §6.2 axis-quadrants (target ≥ 0.7 mean axis_diversity over 30 deterministic seeds).
- Each proposal's content reflects the authoring advisor's axes (axis_attribution_correctness ≥ 0.7 mean).
- Verdict-prose `advisor_distinctness` improves vs the 3.84/5 corpus baseline on a 6-run sample.
- DOMINANT/MORAL/WILDCARD flavor labels remain the player-facing taxonomy (SPEC §7.3 calls for it). They become *downstream tags* of the axis-driven proposal, not the construction seed.

**Non-goals (this spec):**
- Engine resolution path (handled in another session).
- Persona prompt blocks beyond what's required to expose axes / grievance state.
- Grievance-curve tuning, end-state distribution rebalance, snowball/coast antipatterns from §7.2.
- Off-turn (autonomous) phase prompt edits.
- Adding a new tool or schema to `app/game/tools.py`.

## 3. Design

### 3.1 Invert the construction order in the prompt

**Current (flavor-first):** "produce three proposals, one DOMINANT, one MORAL, one WILDCARD; assign each to an advisor whose axes vaguely match."

**New (axes-first):** "each living advisor authors their axis-driven reading of this event; if four are alive, sit out the advisor whose axis triple sits closest to the centroid of the other three (least adversarial); label each resulting proposal's downstream flavor."

Concretely, restructure `_PREAMBLE_TEXT` so:

1. The DEBATE TARGETS section leads with **PROPOSAL CONSTRUCTION** instructions, not flavor counts.
2. A new sub-block, **AXIS → PROPOSAL VALUE-RULES**, lists one short rule per axis (six total: pragmatist / idealist / cautious / bold / individual / collective). One sentence each.
3. A new sub-block, **AXIS + GRIEVANCE → POSTURE**, gives 4 grievance bands (0–2 / 3–5 / 6–8 / 9–10) with one-line postures each (cooperative axis-read → sharper edge → contrarian extreme → coup-probe).
4. The flavor section moves *after* construction and frames flavor as a downstream tag: "after constructing your proposal from axes, label it DOMINANT (resource-efficient, pragmatic), MORAL (legitimacy-positive, principled), or WILDCARD (high-variance, unorthodox). At most one of each label across the three proposals; if two land in the same label, weaken one toward the missing one."
5. The user-message footer (`build_debate_user_message`) shifts emphasis: "three proposals, each authored from a different advisor's axes" first, "one DOMINANT, one MORAL, one WILDCARD" second.

**Token budget:** the preamble is ~3700 tokens currently; the additions below cost ~600 tokens. Total ~4300, comfortably above the 4096 cache-prefix floor (Haiku 4.5; see `app/agent/CLAUDE.md` cache layout). Re-measure with `client.messages.count_tokens` after the change to confirm.

### 3.2 AXIS → PROPOSAL VALUE-RULES (new block)

One sentence per pole. Stable across events. The model uses these as the lens through which an event becomes a proposal.

| Axis pole | Value-rule (in prompt) |
|---|---|
| Pragmatist | Costs visible today outweigh costs that haven't happened yet. |
| Idealist | The action that holds when stated aloud is the one that's worth taking. |
| Cautious | The unknown route is more expensive than its visible cost suggests. |
| Bold | The visible cost of inaction is real; the visible cost of action is paid once. |
| Individual | The named person in front of you outweighs the unnamed group behind them. |
| Collective | The group survives by paying together, even when one member doesn't want to. |

Each advisor's persona block already carries `ethics`, `temperament`, `loyalty` floats. The new block tells the model to dispatch on the sign of each axis to the matching rule. Three rules per advisor, applied in concert.

### 3.3 AXIS + GRIEVANCE → POSTURE (new block)

| Grievance | Posture in proposal |
|---|---|
| 0–2 | Cooperative axis-read. May propose actions that shore up the leader. Tone neutral. |
| 3–5 | Sharper axis-read. Includes a pointed reaction against any recent decision that violated their axis. May sit out (no proposal) rather than propose what they don't believe in. |
| 6–8 | Contrarian extreme. Will propose the option that costs the leader most, when a near-equal alternative exists. May target a rival advisor with a `speak_to_agent` or `adjust_grievance` call alongside the proposal. |
| 9–10 | Coup-probe. Proposal phrased to widen rifts between other advisors. Prefers `tool_calls_on_pick` that raise another advisor's grievance even when headline numbers look reasonable. |

Plus one worked example showing the same event resolving to different proposal flavors at grievance 1 vs grievance 7 for the same advisor.

### 3.4 What stays the same

- Tool surface (`propose_action`, `modify_resource`, `modify_health`, `adjust_grievance`, `speak_to_agent`, `adjust_relationship`). No schema changes.
- The `caller_agent_id` discipline.
- The two cache breakpoints (preamble, personas) and the user-message colony-state placement.
- The XML-delimited `<agent name="X">…</agent>` reaction format.
- The four-alive → one-sits-out rule (now reframed: "if four advisors are alive, the advisor whose axes least diverge from the other three sits out").
- Engine validation, `tool_calls_on_pick`, `effects_preview` matching, all unchanged.

## 4. Eval design

### 4.1 Two new Haiku judges

**`axis_diversity`** (per turn):
- Input: event text + three proposals (name, description, effects_preview, authoring advisor's axes).
- Output: integer 0–4 (count of distinct §6.2 axis-quadrants the three proposals occupy; max 3 for three options).
- Score: `count_distinct_quadrants / 3.0` → float in [0, 1].
- Quadrant assignment is over (ethics × temperament). Loyalty rolled out, deferred — see §6 below.
- Per-turn float averaged over the run; per-run float averaged over the sample.

**`axis_attribution_correctness`** (per proposal):
- Input: one proposal + the authoring advisor's axes (ethics/temperament/loyalty floats).
- Output: 1 if the proposal embodies the advisor's axes, 0 if not. (Binary judge with a one-paragraph rationale.)
- Score: mean over all proposals in the run.
- Catches the failure mode where diversity is high but the *attribution* is scrambled (Vera's DOMINANT proposes an Idealist+Bold action).

Both judges are Haiku, both seed-deterministic, both follow the existing `_judge_utils.py` pattern. Module location: `backend/app/eval/axis_diversity.py` (new), `backend/app/eval/axis_attribution.py` (new). Threshold constants live in those modules per the eval CLAUDE.md naming convention.

### 4.2 Test seeds and sample size

The eval runs **debate-phase only** against fixed event seeds — not full multi-turn games. Each seed is `(setting_id, persona_axis_config, event_template_id, colony_state_snapshot)`. The Anthropic call is one debate request per seed (Haiku, cached preamble); the judges are one Haiku call per seed each. No engine resolve, no autonomous phase, no narration — those are tested elsewhere.

- 30 deterministic seeds for the gating eval. Per-seed cost: ~1¢ debate + ~0.5¢ × 2 judges ≈ ~2¢. Total ~60¢ per CI run; cache-hit on the preamble drops this further on subsequent runs.
- 8 of those 30 act as the iterating subset for the Phase 2 inner loop — fast feedback without paying for the full 30 each iteration.
- Seeds picked to span all 3 settings (10 each) and 3 archetype-axis configurations per setting.
- Colony state snapshots span the grievance bands (§3.3): 4 seeds at grievance 0–2, 4 at 3–5, 4 at 6–8, 4 at 9–10, 14 mixed. This forces the eval to exercise the AXIS + GRIEVANCE → POSTURE block.

### 4.3 Threshold setting

Baseline must come from fresh measurement under the *current* prompt, before any prompt change. Because the eval is debate-phase only (§4.2), the proposals arrive directly in the test from the parsed Anthropic response — no harness change is needed to capture them. The test serialises them to a fixture JSON the first time it runs (under the current prompt) and uses that fixture as the baseline corpus going forward.

1. **Baseline pass first.** Run the test against the 8-seed iterating subset under current `prompts.py`. Save proposals + judge scores to `tests/eval/fixtures/axis_baseline.json`. This is the baseline.
2. After the prompt edit, the same 8-seed subset is re-run. Direct before/after on the same seeds — apples to apples.
3. **Threshold = max(0.70, baseline + 0.15).** If today's `axis_diversity` mean is 0.55, threshold becomes 0.70. If it's 0.62, threshold becomes 0.77. The +0.15 floor prevents declaring victory on a small relative gain.
4. Same rule for `axis_attribution_correctness`.

### 4.4 Verdict-prose check (Phase 3 only)

Run the existing playtest harness on a 6-run sample (2 per setting). Verdict prose is read end-to-end and the existing `advisor_distinctness` axis (Sonnet judge, scale 0–5) must score ≥ 4/5 on at least 4/6 runs. Corpus baseline is 3.84/5 mean — we want a visible bump (≥ 4.2/5 mean across the 6-run sample).

## 5. Test plan

### Phase 2 — TDD inner loop

1. **Add `axis_diversity.py` + `axis_attribution.py` + their unit tests** (mocked Haiku via `respx`, per `app/eval/CLAUDE.md`).
2. **Add 30 debate-phase seed fixtures** (event template + colony state + persona axes) under `tests/eval/fixtures/axis_seeds/`. 8 marked as iterating subset, 30 total for gating.
3. **Run baseline** against the 8-seed iterating subset under the current prompt. Save proposals + judge scores to `tests/eval/fixtures/axis_baseline.json`. Record `axis_diversity` and `axis_attribution_correctness` means.
4. **Set thresholds** per §4.3 rule; pin in module constants.
5. **Write the pipeline-level eval test** (`tests/eval/test_axis_proposals.py`, marked `eval`) that runs the 8-seed subset against the real-LLM debate call and asserts mean ≥ threshold. Test fails against the baseline (proves the prompt change is necessary).
6. **Iterate the prompt** in `prompts.py` until the 8-seed test passes.
7. **Run the 30-seed gating test** to confirm ≥ threshold on the broader sample. **PAUSE** here for user review of first eval pass.

### Phase 3 — Verify on harness

1. Pick a 6-run sample (2 per setting, varied personas — recommend optimizer/brinksmanship/drop_off_risk × arctic/orbital/desert).
2. Run the playtest harness with the new prompt.
3. Read the 6 archives end-to-end. For each turn, ask: do these three options represent three different worldviews?
4. Pass criteria: ≥ 80% of turns yes, AND `advisor_distinctness` ≥ 4/5 on ≥ 4/6 runs (corpus baseline 3.84/5).
5. If pass: open the PR. **PAUSE** for user before merge.
6. If fail: regression-iterate the prompt; loop back to §4 if eval thresholds also dropped.

## 6. Out of scope (logged for discipline)

- **Loyalty axis in `axis_diversity` judge.** The §6.2 grid uses ethics × temperament; loyalty is rolled independently per advisor. We start with the 4-quadrant judge and revisit if `axis_diversity` saturates while runs still feel monotone.
- **Engine resolution path.** `engine.derive_resolution_consequences` and the legitimacy/grievance application from picked proposals stay as-is. The prompt is the lever this session.
- **Grievance-curve tuning.** §3.3 surfaces grievance to the *prompt*, but does not change how grievance grows or drains in the engine.
- **Setting-specific prompt customization.** Value-rules and posture tables are setting-agnostic. If desert advisors should weight individualism differently than orbital advisors, that's a v2.
- **Persona-block widening.** We're not adding new fields to `Agent`, not adding per-advisor goal-statements, not changing portrait or memory shape. Only the preamble grows.
- **Off-turn / autonomous phase.** Autonomous-tool postures are separately driven by grievance already; we don't re-touch that prompt path here.
- **Proposal-logging in the playtest harness.** The harness still records advisor reactions but not proposals. Phase 2 is debate-only against fixtures, Phase 3 reads verdict prose for `advisor_distinctness`; neither needs proposal-logging in the harness. If Phase 3 fails and proposal-level diagnostics are needed, that becomes a separate small change.

## 7. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Preamble grows past the 4096-token cache floor → silent caching loss → ~4¢/run cost spike | Re-measure with `count_tokens` after each prompt edit. Test in `tests/test_prompts.py` asserts cumulative tokens ≥ 4096. |
| Axes-first prompt produces options that don't span flavor cleanly → engine drops one as duplicate flavor | Flavor-tag step (§3.1 step 4) is explicit in the prompt: "if two land in the same label, weaken one." If it still fails, fall back to weighted flavor-then-axes (hybrid). |
| `axis_attribution_correctness` judge confuses similar quadrants (Pragmatist+Cautious vs Pragmatist+Bold) → noisy score | Judge prompt instructs strict matching on both axes; tie cases (axis float in [-0.2, +0.2]) score 1 if either side fits. |
| New posture block triggers the model to write meta-narrative ("Vera, grievance 6, says…") into reactions | Voice section already forbids fourth-wall breaks; reinforce with "do not name your grievance number in prose." |
| Corpus baseline measurement is itself biased — we judge against a corpus the model already produced | The judge's job is relative diversity, not absolute. Baseline is a calibration anchor; the +0.15 floor prevents over-fitting to it. |

## 8. Pause points

1. After Phase 2 first eval pass (8-seed iterating subset passing).
2. After Phase 2 30-seed gating eval result.
3. Before merging the PR.

## 9. Done criteria

- PR titled `feat(agent): axis-driven proposal generation`.
- `axis_diversity` Haiku judge ≥ threshold mean on 30 deterministic seeds (threshold per §4.3).
- `axis_attribution_correctness` Haiku judge ≥ threshold mean on the same 30 seeds.
- Harness re-run on 6-run sample shows verdict prose `advisor_distinctness` ≥ 4/5 on ≥ 4/6 runs (corpus baseline 3.84/5).
- Cache-prefix token count still ≥ 4096; no per-run cost regression > 5% in the harness sample.
- All existing eval tests still pass; no regression in `personality_consistency` (≥ 0.72) or `tool_validity` (≥ 0.85).
