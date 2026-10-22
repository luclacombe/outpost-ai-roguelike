# Outpost Evolved: umbrella design

**Date:** 2026-10-22
**Status:** Draft for review
**Scope:** Direction, order of work, fixed targets and shared rules for everything after Spec 1. Each workstream below gets its own spec and plan when it starts; this document is what those specs are written against.
**Supersedes:** the spec 2 to 4 split in `docs/superpowers/specs/2026-10-07-truth-and-integrity-design.md` §0 (on branch `feat/truth-and-integrity`). Their scope items are reassigned in §6.

---

## 1. Summary

Outpost stops being "a game with an LLM in it" and becomes a game with a visible, measured improvement loop around it.

- **For players:** *The colony you play today was evolved last night.* Each day's run uses the latest ruleset and prompts that survived the loop.
- **For engineers and recruiters:** *Every number has a receipt.* Any stat change in any run can be traced to the rule, the judgment, the LLM output and the event behind it.

The core architecture is a three-way split per turn:

1. **The LLM writes.** Event framing, the advisors' debate, the options, the narration. It authors no numbers that change state.
2. **Jev judges.** TypeSafe's System One model answers narrow typed questions about what each option means to each advisor ("does this betray the Doctor's ethics?") and returns calibrated probabilities.
3. **The engine decides.** Fixed formulas in code turn judgments and option payloads into validated tool calls.

Around it sits a loop: **trace → analyse → judge → simulate → evolve → gate → ship.**

This is not a rewrite. Most of the backend, the engine pipeline, the settings and the harness stay. The work is: build the instrument that proves the game is fun, then fix the game through it.

## 2. Why

### 2.1 The game is honest but not yet a game

Spec 1 made the game's numbers truthful. The 2026-10-22 sweep (90 scripted games, 3 policies × 3 settings × 10 seeds, git `e177ff6`, summary at `docs/playtest/2026-10-22-sweep/summary.md` on `feat/truth-and-integrity`) measured what is left:

| | Measured | SPEC §10 target |
|---|---|---|
| Endured | 91% | 35–45% |
| Collapsed | 9% | 30–40% |
| Ousted | 0% | 10–15% |
| Killed | 0% | 5–10% |

- Every completed game ran to its final turn. No advisor died; no coup happened.
- Random picking endured 90%. Pragmatic play endured 100%; moral 83%.
- Policies reached different endings in 23% of paired seeds (40% on 2026-10-06).
- The orbital AI Specialist opens 96% of its lines with its catchphrase (Commissar 89%, Radio Operator 82%).
- One turn of 834 produced zero valid proposals, which would strand a real player.
- The cost tracker reports 18.9¢ a game (p95 23.5¢ against a 25¢ ceiling); real spend was about 6¢ (W1b). Tool validity 0.976.

### 2.2 Root causes found in code

- Advisors react to *whether* the player sided with them, not to *what* was decided. `resolution.derive_resolution_consequences` raises grievance only when the player overrules the DOMINANT proposer. Nothing judges whether a decision betrays an advisor's values or harms the group a collective-minded advisor protects.
- Transformations (`transformations._pick_shift`) choose a personality shift from the advisor's own axes and the event's severity. The player's decision is not an input.
- The debate LLM can still author `adjust_grievance` deltas inside `tool_calls_on_pick`.
- With grievance barely moving, the coup thresholds (grievance ≥ 9) are unreachable, so OUSTED and KILLED never occur.

### 2.3 Why this direction

The project has two audiences (SPEC §1): players, and people evaluating the builder's AI engineering. The game comes first. The user wants:

- a game that is fun and works;
- a deeper LLM-ops story than "agents plus a dashboard": traceability, evaluation, and a loop that measurably improves the product;
- one coherent story across the game, the repo, the website, the CV and LinkedIn.

The loop serves both audiences: it is how the game gets fixed, and it is the showcase.

## 3. Goals, non-goals, anti-goals

**Goals**
- The game hits the targets in §7, measured on seeds never used for tuning, and confirmed by real people playing.
- Every state change carries a structured receipt back to its causes.
- Advisors react mechanically to the content of decisions, through calibrated judgments priced by the engine.
- Balance and prompts improve through an automated loop that proposes changes with evidence; a human merges them.
- The loop is visible: in the game (inspector, post-mortem), on a public lab page, and in the README.

**Non-goals**
- Rewriting the stack. FastAPI, LangGraph, the raw Anthropic SDK, Supabase, Next.js 16, Railway and Vercel stay.
- Live online learning from player behaviour. The player base is too small; the loop learns from simulation, judges and periodic human playtests.
- Runtime image or video generation (invariant #8 still holds at runtime).
- New settings, new advisor archetypes, or multiplayer.

**Anti-goals** (failure even if it technically works)
- A loop that optimises judges instead of fun: metrics climb while humans find it worse.
- Self-improvement claims without measured numbers behind them. Every public claim cites a measurement.
- Shiny technology that moves no target. Any new tool must name the §7 target it moves or the failure it fixes.
- An inspector or lab page that is impressive while the game stays dull. Game work comes first in every trade-off.
- The loop shipping changes to production without human review.

## 4. Constraints

- **Stack and invariants:** root `CLAUDE.md` holds. Invariant changes are listed in §10 and need the user's approval in the workstream spec that introduces them.
- **Jev usage rules:** §8. Above all: no Jev question may exceed 32k tokens of context.
- **Langfuse Cloud Hobby:** 50k units a month (traces, observations and scores combined), 30 days of data access, 2 users. Langfuse is a viewer, not the permanent record.
- **Cost per game:** p95 ≤ 25¢ including Jev (existing ceiling).
- **Loop budget: at most $50 a month in API spend, ideally less.** This covers real-LLM sweeps, simulator Jev calls and prompt evolution. It does not cover live play (below).
- **Use the Claude subscription wherever it can carry work.** Anything that runs through Claude Code goes on the user's subscription, not the API budget: analysis and review sessions, the LLM persona players (`scripts/play_test.py` already drives them through the `claude` CLI), the reflection step of prompt evolution, writing generation PRs, and orchestrating the nightly loop. The game's own LLM calls (debate, narration) are API calls and cannot use the subscription.
- **Live play is a separate cost:** about 6¢ per run in real spend today, one run per player per day. At W1b's ≤ 1¢ target, five daily players cost about $1.50 a month and a thousand cost about $300.
- **LLM provider:** OpenRouter, via its Anthropic-compatible Messages API. Today debate and autonomous calls run on DeepSeek V4 Pro and narration on V4.1 Flash, thinking off. W1b picks at most two models (debate, narration) by bake-off, choosing the cheapest that holds quality; the user's direction is DeepSeek or cheaper. Prompt caching stays mandatory.
- **Daily gate:** one run per account per day stays. "Evolved last night" must stay truthful: the public copy says "evolved last night" only when the current generation merged in the previous 24 hours, and "generation N, evolved <date>" otherwise.
- **Conventions:** TDD for testable units, Conventional Commits, ruff check and format, CLAUDE.md upkeep at each workstream close (invariant #13).

## 5. The loop

```
 real runs ─┐
 sweeps ────┼─► turn records (Postgres, permanent) ──► Langfuse (browse, label, compare)
 sims ──────┘          │
                       ├─► error analysis (human) ──► failure list ──► judges (Jev, calibrated)
                       ├─► proposal library ──► replay simulator ──► evolution (rules, prompts)
                       │                                              │
                       │                     held-out seeds + CI gates┘
                       │                                              │
                       └─► inspector / post-mortem / lab page ◄── PR with evidence ──► human merge
                                                                      │
                                                         generation N+1 serves the daily run
```

## 6. Workstreams, in order

Each workstream gets its own spec (short; direction is set here) and plan. Exit criteria are part of each spec. W1b follows W1 directly, so every later workstream plays cheap games. W2 and W3 can then run in parallel; W3's proposal library is recorded after W1b settles the debate model, so it is not recorded twice. W6's home page and 3D work start only after W5's first evolved generation ships, except where noted.

### W0. Close Spec 1
- Merge `feat/truth-and-integrity` (31 commits) through the existing review flow. That branch's session owns the remaining decision on the tic-copy FAIL.
- Add a fallback for zero valid proposals (re-ask once, then serve a safe minimal triple from the event's `choice_templates`). Was spec 4.

### W1. Trace foundation ("receipts")
Purpose: one permanent, typed turn record that everything else reads.

Decided:
- **The turn record in Postgres is the source of truth.** `turn_logs` already stores event, proposals, chosen, agent responses, autonomous actions, narration, state snapshot, cost and the phase-tagged tool calls. Add:
  - every LLM call: purpose, prompt version (content hash), model, raw output, token buckets, cost, latency, attempt number;
  - every Jev request: question ids, the state slice sent (or its hash plus a stored copy), answers, probabilities, confidence, token count, latency, model version;
  - a structured `cause` on every tool call: the engine rule id, the judgment ids it consumed, the proposal and LLM call it came from;
  - version tags per turn: git sha, ruleset generation id, prompt hashes, model ids, Jev model id, setting id and seed;
  - cost as a fractional amount. Today each turn's cost is rounded to whole cents before it is added to the game total (`nodes.advance_turn`), so at about 0.15¢ a turn the stored total, the post-mortem and the audit's cost gate all read 0¢ (found 2026-10-22). The per-turn cost cap uses the unrounded value and still works.
- **Model calls are traced with OpenTelemetry.** Today Langfuse is attached through LangChain callbacks, which see LangGraph steps but not the raw `client.messages.create` calls in `app/agent/anthropic_client.py`, so prompts, outputs, tokens and cost never reach Langfuse. Use the Anthropic OTel instrumentation that Langfuse documents for its v4 SDK. The eval judges' calls get traced too.
- **Jev calls get a custom span**, emitted by the Jev wrapper (§8).
- **Two tiers.** Player runs go to Langfuse (with a configurable sample rate if quota requires). Sweeps and evolution write turn records only; a script pushes selected runs to Langfuse on demand.
- **Langfuse features used:** sessions per run, scores (judge outputs and human labels), annotation queues (§W2/W4), datasets and experiments (§W5).
- ARCHITECTURE.md §11 is rewritten to match what the code emits.

Builder's call: column vs table layout for the new records; whether a local self-hosted Langfuse is worth running for the lab.

Exit: one real run and one sweep game can each be walked from any stat change back to its event, in the turn record and (for the real run) in Langfuse.

### W1b. Cost: leaner calls, then the right model per role
Purpose: every real game the loop plays costs what a player's game costs. Cheaper games buy more evidence within the $50 budget and make public play affordable. Runs right after W1, before W2 and W3, because everything after it plays games.

**What was measured on 2026-10-22** (details in §14):
- Real spend for the 90-game sweep was at most $5.31 (OpenRouter key usage for that UTC day), so **about 6¢ a game, not the 19¢ the tracker reports**. The tracker bills V4 Pro at the fallback host's rate (Novita, $0.99/$2.97 per M) with no cache discount, while the pinned host (StreamLake) charges $0.66/$1.98 with cache reads at $0.022.
- The debate and autonomous calls run on **DeepSeek V4 Pro** (`LLM_MODEL_FAST=deepseek/deepseek-v4-pro-0813`); only narration runs on V4.1 Flash. Pro was chosen because V4.1 Flash often returned `propose_action` with an empty name or `{}` input.
- Per turn: a debate call (~4.5k cached prefix, ~1.1k fresh, ~650 output, retried on 21% of turns), an autonomous call (same ~4.5k prefix, ~1k fresh, ~290 output, mostly `remember` and `adjust_own_grievance`), and narration (~70 output tokens). Output and fresh input on Pro are most of the cost.

Decided, in order:
1. **Measure exactly.** Read OpenRouter's reported cost and cached-token count from every response (or the generation endpoint) and store them in the turn record (W1). Log why first debate attempts fail (which proposal class fell short). The cost tracker uses reported cost when available and keeps the price table only as a fallback.
2. **Leaner calls, same model.** In this order:
   - a flat debate schema: one forced tool (for example `submit_debate`) holding all three proposals with simple fields (name, description, flavor, advisor name, a list of resource deltas, optional health or grievance delta). No UUIDs in model output (the engine maps names to ids); no per-effect reason text. An adapter rebuilds the nested tool calls, so the validator and invariants #2 and #11 are unchanged;
   - debate gets only its own tool (the "send the full tool list" rule existed for Anthropic's 4096-token cache minimum, which DeepSeek does not have). The preamble stays whole, with the flat contract swapped in: a debate-only trim tested slightly worse on 2026-10-22, and cached tokens cost almost nothing;
   - shorter reactions, and a retry that asks only for the missing proposals;
   - compact state: names instead of UUIDs, fewer memories per advisor in the prompt.
   Estimated result on V4 Pro: about 2.7¢ a game, or about 1.8¢ once W4 replaces the autonomous call.
3. **Model per role, chosen by a bake-off.** At most two LLM models plus Jev:
   - **Debate** is the only call where model choice matters. **Bake-off done 2026-10-22** (§14; full report in `docs/playtest/2026-10-22-model-bakeoff/`, local and gitignored).
     - **Setup:** 20 models on 12 real turns, in both the current and the flat format.
     - **Blind quality scoring:** four Opus evaluators, with a duplicate control as a consistency check.
     - **Rules fixed in advance:** at least 11 of 12 first-attempt passes, quality at least 90% of V4 Pro's, then the cheapest.
     - **Single-turn result:** Xiaomi MiMo V2.6 Flash matched V4 Pro's blind quality, passed 12 of 12, cost a quarter as much per call and answered in about 2 seconds instead of 17. Qwen3.8 Flash and Claude Haiku 5.5 were also eligible but cost more. Superseded by the full-game test below.
     - **Format test, same day:** adding a per-effect `why` plus a sign rule, or trimming to debate-only instructions, did not beat the plain flat format; mechanics got slightly worse. Decided: keep the full preamble with the flat contract swapped in, and no per-effect reason in model output.
     - **Full-game test, 2026-10-22 (decides the model; §14):** 18 games each for MiMo, V4.1 Flash and GPT-6 Luna, with V4 Pro on the flat format as the control, same seeds. Objective checks plus a blind whole-game read by six Opus judges, under a selection rule fixed in advance.
     - **Chosen: DeepSeek V4.1 Flash (`deepseek/deepseek-v4.1-flash-1017`) for debate, off-turn actions and narration**, unpinned, thinking off. It matched the control's blind quality (108% point estimate; ahead in 13 of 18 groups, not significant, 95% CI −0.11 to +0.67 on a 1–5 scale), clearly beat MiMo (16 of 18) and Luna (18 of 18), showed players less breakage than the control, and costs 1.41¢ a game against V4 Pro's 6.04¢. MiMo fell to 89% over whole games and dropped reactions on 3 turns; Luna was the cheapest (0.59¢) and cleanest but flat (80%).
     - **Ruling:** the rule's breakage criterion counts what a player sees, as the rubric defines it. Most of Flash's raw breakage was in off-turn lines, which the UI never shows, and came from our parser's placeholder for empty text. Read literally, no candidate qualified.
     - **Defects found, to fix on this branch before merge:** the flat schema does not require a resource or target per effect kind, so options can reach the player with no effect; empty off-turn text gets a placeholder; the retry ignores missing reactions; narration is not filtered for non-Latin words or markup.
   - **Narration** uses the cheapest model that passes the narration guards. It costs well under 0.1¢ a game on any of them, so it never gets a third provider.
   - **Autonomous** stops being an LLM call in W4 (engine rules plus Jev questions). Until then it runs on the debate model.
   - **Eval judges** move to Jev where the W4 head-to-head allows.
   - **Offline sweeps** may use `:batch` variants (about half price) where OpenRouter offers them, plus an exact-match response cache for replays of identical prompts.
4. **One provider, one key.** Everything goes through OpenRouter, including Jev if the W4 check confirms it works there (§8). Hosts are pinned per role with `provider.order`, which also keeps DeepSeek's automatic prefix cache warm. DeepSeek-direct (half price off-peak, but data hosted in China, by inference) is not used for live play.

Every turn record carries its model (W1). A model switch starts a new baseline: the proposal library and fidelity check are re-recorded.

**Target: ≤ 1¢ per game in real spend, with tool validity and the quality judges no worse than V4 Pro.** Measured with V4.1 Flash and the flat format: 1.41¢ a game (debate 0.80¢, off-turn 0.53¢, narration 0.08¢), with 41% of prompt tokens read from cache. To reach the target: W4 removes the off-turn call (about 0.9¢), and a host pin for V4.1 Flash should raise the cache share (test it first; the V4 Pro pin `StreamLake,Novita` must not carry over).

Exit: per-call cost and cache-hit records; the flat schema live; the bake-off report; the chosen debate and narration models with their measured per-game cost.

### W2. Error analysis from raw material
Purpose: find what is actually wrong, in plain words, before building more metrics.

Decided:
- Inputs: full turn records for ~100 turns sampled across settings and policies from a fresh sweep on W1's tracing; three runs the user plays with a note per turn; 3–5 people who are not the user playing one run each while thinking aloud, with screen recording (consent recorded; recordings kept out of the repo).
- Method: open coding (describe each problem in plain words), then group into failure types, then count. Output: a ranked failure list with examples, stored under `docs/playtest/`.
- Human sessions also record time-to-decide per choice, whether they finished, and whether they can retell the run in 90 seconds. These are the human baselines for §7.

Exit: ranked failure list; human baseline numbers.

### W3. Replay simulator
Purpose: tune engine numbers over thousands of games with no LLM calls.

Decided:
- The engine is pure, events come from the YAML pools (`event_categories` with `prompt_template` and `choice_templates`), and each setting has four advisor quadrants. A **proposal library** keyed by (setting, event category, proposer quadrant) is built from recorded sweeps, storing each proposal's text and payload.
- The simulator plays games with the real engine and seeded event selection, drawing proposals from the library. Names and ids are remapped by quadrant.
- **Judgments are computed live, not replayed.** Jev's answers depend on state (advisor axes, memory, grievance), and that state changes as soon as a constant changes the trajectory, so stored answers would describe a game the simulator is not in. The simulator calls Jev through the §8 wrapper. Answers are memoised by (question id, question version, state slice hash), and state slices use bucketed values (for example grievance bands rather than raw numbers) so identical situations reuse answers. Jev cost per simulated game is reported. Because answers are not stored in the library, sweeps recorded before W4 remain usable.
- When a constant change sends a trajectory to a (category, roster) combination the library lacks, the simulator reports the gap; a top-up sweep fills it. It never invents proposals.
- **Fidelity check:** with the same constants, the simulator's end-state distribution matches a real LLM sweep within ±10 percentage points per end state, on a 90-game sweep (about $17 at today's cost). A ±5pp check needs roughly 350 games; once W1b reaches its target (about $3.50 a check at 1¢ a game), the ±5pp bar replaces the ±10pp one. Below the bar, the simulator does not gate anything. The check is re-run only when prompts or the model change. It passes twice: once on the pre-W4 engine (validates the machinery), and again with the W4 judgment layer on. The second pass gates W5, because the coup and grievance dynamics W5 tunes only exist after W4.

Builder's call: library storage, sampling strategy, how many sweeps to record before the fidelity check, simulator concurrency.

Exit: fidelity check passes (twice, as above); a 1,000-game run completes in under 30 minutes with its Jev cost reported.

### W4. Jev: head-to-head test, then the judgment layer (spec 2)
Purpose: make advisors react to what is decided; replace uncalibrated judges.

Step 1, head-to-head test (gate for everything else in W4):
- ~200 turns from W1 records, hand-labelled by the user on 3–4 questions taken from the W2 failure list and from the judgment layer (for example "does option X betray advisor Y's ethics?", "does this line sound like this advisor rather than a catchphrase?").
- Compare Jev, the current LLM judges (`app/eval/*`) and the embedding personality metric (`app/eval/personality.py`, cosine similarity, threshold 0.72) on agreement with the labels (true-positive and true-negative rates), calibration, latency and cost.
- Jev is adopted per question only where it wins. Results are written up; they feed W7.
- **If Jev loses on a question:** decompose or reword it and retest, up to two rounds (TypeSafe's own guidance is that narrower questions do better). If it still loses, that question uses a deterministic engine heuristic over proposal payloads and advisor axes. An LLM judge is never used in the live turn path (it would break the latency budget and add per-turn cost). W4 proceeds with partial adoption; the head-to-head report says which questions use which source.

Step 2, the judgment layer:
- After the debate and preparation, one Jev request (or a few, within §8 limits) asks atomic questions for every option × every active advisor, built from each advisor's axes (ethics, temperament, loyalty), role and recent memory.
- Answers are stored in the turn state. `preview.simulate_option` uses them to render advisor reactions in the option preview; `resolve` uses the same stored answers. Jev is never re-asked for the same turn.
- Engine formulas in `resolution.py` turn answers into `adjust_grievance` and other engine-emitted tool calls (`caller_id=None`), each with a `cause` (W1). The engine never reads unthresholded free text.
- `adjust_grievance` is removed from the debate's `_NESTED_TOOL_WHITELIST`; grievance is engine-priced only.
- **An effect-consistency check:** for each option, Jev asks whether each listed effect matches what the description says. The engine drops a mismatched effect, or flags the option for a retry. The 2026-10-22 format test showed every model and every format writing effects that contradict their own text, and prompt instructions did not fix it.
- **The non-Latin-script guard from Spec 1 also covers debate reactions** (MiMo produced a stray Chinese word in testing).
- Transformations take the decision's judgments as an input.
- **The autonomous LLM call is removed.** Measured on 167 turns, it mostly produced `remember` (61%), `adjust_own_grievance` (20%), `speak_to_agent` and `adjust_relationship`. Memories become engine templates built from the event, the choice and the judgments. Own-grievance and relationship changes come from judgment formulas. A coup attempt becomes a Jev question asked only when the engine's eligibility conditions already hold. The validator needs an engine origin that acts on an advisor's behalf, because `remember` and `adjust_own_grievance` reject `caller_id=None` today. If engine-written memories read flat in the W2 or playtest review, narration may add one short memory line per advisor (builder's call).
- If Jev fails or times out, the turn falls back to today's rules, the turn is flagged, and the preview omits reaction lines.
- Uncertain answers (probability in the middle band, initially 0.3–0.7) are routed to a Langfuse annotation queue; the user's labels there extend the ground-truth set.

Original spec 2 scope stays in this workstream: decision-driven seeds and aftermath bodies, coups, transformations and grievance visibility, escalation scheduling, collapse rule and visibility, legitimacy earn-back, the MORAL-cost contradiction in the engine, advisor silence, and unavoidable event damage.

Also folded in from spec 4: personality-metric calibration (step 1), position bias (Jev option shuffling and the harness's position-bias stat), fallback accounting.

Builder's call: question wording, formulas and initial constants (W5 tunes them), how state slices are built.

Exit: head-to-head report; judgment layer live behind a setting flag; sweep shows grievance moving from decision content; previews show reactions and match what resolve applies.

### W5. Evolution (spec 3 and beyond)
Purpose: improve the game through search, with evidence, under human merge.

Decided, in this order:
1. **Rules first.** Search over engine constants (drains, starting legitimacy, wealth scaling, judgment-to-grievance formulas, coup and collapse thresholds) in the W3 simulator. Fitness is the §7 targets. Method: an evolutionary strategy (CMA-ES or a simple genetic algorithm; builder's call). This is spec 3.
2. **Prompts second.** Search over the debate preamble and per-advisor personality prompts, scored by calibrated Jev judges (in character, catchphrase rate, options genuinely different, decision tension). GEPA-style reflective evolution is the default method; the judges' per-question answers are turned into text feedback. Each candidate is evaluated on recorded turn states, not full games. The reflection step (reading judge feedback and proposing the next prompt) runs through Claude Code on the subscription; only candidate evaluation (debate calls plus Jev) spends API budget. Capped at $15 per run and at most one run a month until W1b lowers per-game cost.
3. **Content, stretch.** Quality-diversity search (MAP-Elites) over events and dilemmas, keeping the best event per cell of (setting × dilemma type × decision tension). Only if 1 and 2 are done.

Rules for every search:
- Train seeds and held-out seeds are separate and fixed per generation. A candidate must improve on held-out seeds. Rules candidates are verified on held-out seeds in the simulator, then confirmed by one small real sweep (about 45 games, roughly $9) before the PR opens. Prompt candidates are verified by a real held-out sweep of the same size.
- **Cadence: on demand first, nightly later.** The loop runs from the user's laptop until 2–3 generations have been merged and trusted. Then a nightly scheduled run takes over: mostly simulator and Jev work, real-LLM sweeps only when a candidate is ready, opening a PR when one passes. The orchestration can run as a scheduled Claude Code routine on the subscription (builder's call); the game-LLM and Jev calls inside it still bill to the API budget.
- **Human playtests:** a round of 3–5 people for each rules generation; prompt generations rely on judges and hand-labelled spot checks.
- Multiple objectives are kept separate (Pareto), not summed into one score.
- Each accepted candidate becomes a **generation**: a PR containing the diff, the fitness vector on train and held-out seeds, the parent generation and what changed. CI eval gates run on it. The user merges.
- The lineage (parent, mutation, scores, decision) is stored so the lab page can draw the family tree.
- Original spec 4 items left over (tic frequency, debate retry rate) are prompt-evolution targets.

Exit for the first generation: rules generation passes held-out targets for end states, decisions-matter and no dominant style, and a human playtest round does not get worse on the W2 baselines.

### W6. Surfaces
- **Inspector panel** (can start after W1 and W4): for the current turn, the receipt chain: debate call, Jev judgments with probabilities, the engine rule each fed, resulting tool calls, cost and latency. Behind a dev/demo flag during play.
- **Post-mortem "why" chain:** each turn entry can expand to the receipt chain in in-world language ("the Doctor's grievance rose because you ordered the infirmary stripped"). Counterfactual lines use simulator replays of the paths not taken. Visible to all players.
- **Public lab page** under `frontend/app/(public)`: current generation and changelog, fitness over generations, the lineage tree, judge agreement with hand labels, cost per game, and a sample receipt. No personal data.
- **Home page:** "Play today's run" and "Generation N: what changed". Mobile-first, pixel-art theme kept.
- **3D hero, after the first evolved generation:** default technique is a frame-sequence scrub (an offline-generated turntable video of the outpost, cut into frames and scrubbed on drag or scroll). Three.js or Gaussian splats are alternatives if they beat it on load weight. Respects `prefers-reduced-motion`. Requires the invariant #8 amendment (§10).
- **New technology research** (Agent Reach for live practitioner sources, among others) happens here, under the anti-goal rule: each candidate names the §7 target it moves.

### W7. Story
- **README:** game GIF and one-line pitch first; then the loop diagram with real numbers; then a receipt-chain screenshot; links to the lab page.
- **CV bullet template** (numbers filled only once measured): "Built Outpost, a daily narrative game where LLM advisors debate and a calibrated judge model scores every decision; an evolutionary loop tunes rules and prompts against human-validated metrics, raising [metric] from [A] to [B] at [N]¢ per game."
- **LinkedIn:** build in public, one measured finding per post (for example, catching the optimiser gaming a judge).
- Outward-facing text follows the user's writing rules: no em dashes, no hyphen used as a dash, no AI-tell phrasing.

## 7. Targets, fixed in advance

All targets are measured on held-out seeds unless stated. "Now" is the 2026-10-22 sweep.

| Target | Now | Required | Measured by |
|---|---|---|---|
| End-state distribution | 91 / 9 / 0 / 0 | SPEC §10 bands (35–45 / 30–40 / 10–15 / 5–10), ±10pp tolerance as in the sweep summary | sweep, then simulator once W3 passes fidelity |
| Decisions matter (paired seeds where policies reach different endings) | 23% | ≥ 40% | paired sweep cells |
| No dominant play style | pragmatic 100% endured | best policy's endure rate ≤ overall endure rate + 15pp | sweep by policy |
| Random play loses more than deliberate play | random 90% endured | random's endure rate below both deliberate policies | sweep by policy |
| Tool validity | 0.976 | ≥ 0.85 (invariant #10) | turn records |
| Cost per game (real spend, from OpenRouter's reported cost) | about 6¢ (tracker says 18.9¢) | ≤ 1¢ after W1b, including Jev; the 25¢ p95 ceiling stays as a hard rail | turn records |
| Jev latency added per turn | n/a | p95 ≤ 300 ms | Jev spans |
| Catchphrase rate | up to 96% | ≤ 25% of an advisor's lines open with their tic | Jev judge, calibrated |
| Judge agreement with hand labels | not measured | every gating judge: true-positive and true-negative rates ≥ 0.8 on ≥ 100 labelled items | W4 head-to-head, then ongoing labels |
| Decision tension is real | not measured | Spearman correlation ≥ 0.3 between the tension score and human time-to-decide, over ≥ 60 decisions | W2 / later playtests |
| Real people | not measured | of ≥ 5 people who are not the user: ≥ 4 finish a run, ≥ 3 retell it in 90 s; next-day return recorded | playtest rounds |

**Decision tension score:** for each turn, ask Jev (Choice) which option each of several player framings would pick (for example cautious, idealist, ruthless, loyal-to-advisors). Tension is the mean normalised entropy of those distributions. A turn where every framing picks the same option with high probability is a fake choice.

## 8. Jev usage rules

Jev is TypeSafe's System One model (docs: https://docs.typesafe.ai). It takes a state plus typed questions (Choice, Score, Noul) and returns probabilities and, for Choice and Score, confidence. It generates no text. Price $0.042 per million input tokens, output free. Default limits 64k tokens per request and 32k per question.

**Access:** OpenRouter documents the TypeSafe SDK working with an OpenRouter key: base URL `https://openrouter.ai/api` (the SDK calls `/v1/systemone`), model `jev-1.13` or `jev-latest`, responses carry `usage.cost`. OpenRouter's catalog currently lists `typesafe/jev-router`, its guide does not mention Jev 1.14, the long-context or image headers, or rate limits, and a third-party guide calls the route beta. The first W4 task is a one-cent test through OpenRouter: confirm the answers, the model version, `usage.input_tokens`, and that the 32k per-question limit still rejects. If anything is missing, use a direct TypeSafe key.

- **One wrapper** (`app/agent/jev.py` or similar; builder's call on location, but it lives outside `app/game/` to keep the engine pure) is the only way to call Jev. It:
  - counts tokens per question and refuses to send any question whose state plus question exceeds 24k tokens (a margin under the 32k limit);
  - never sends the `TypeSafe-Danger-Zone-Long-Context` header;
  - emits the trace span (W1) and records `usage.input_tokens`;
  - shuffles Choice options and maps answers back, to counter Jev's documented first-option bias;
  - times out and returns a fallback signal instead of raising into the turn.
- Tests enforce the 24k cap and the absent header.
- Each question asks one thing and gets only the state slice it needs (TypeSafe's own guidance; accuracy drops with irrelevant state).
- No counting, arithmetic or date logic is delegated to Jev. The engine does all maths.
- Images (behind the experimental `TypeSafe-Danger-Zone-Images` header) are allowed only in the test harness, for example judging UI screenshots. Never in the live turn path.
- On the first integration, measure whether a shared state is billed once per request or once per question, and record it in `backend/app/agent/CLAUDE.md`.
- Thresholds (the 0.3–0.7 middle band, any act/escalate cut-offs) are tuned against hand labels, never picked once and left.

## 9. What stays the same

- The engine pipeline: tool call → validator → apply, phase tags, engine-only tools.
- One batched debate call per turn (invariant #6), LangGraph thread-id discipline (#12), prompt caching (#5).
- Settings as YAML, procedural generation by `(setting_id, seed)`.
- The scripted sweep policies (moral, pragmatic, random) as the baseline players. LLM personas stay available for cheap human-like reads.

## 10. Invariant changes (approved per workstream)

- **#2, strengthened (W4):** no LLM authors a grievance number; grievance comes only from engine formulas over stored judgments and proposal payloads.
- **#8, amended (W6):** still no runtime-generated images. Static media generated offline and committed as assets (for example home-page frames) are allowed.
- **#10, sharpened (W4):** a judge may gate merges only after its agreement with hand labels is measured. The current 0.72 embedding gate stays until W4 replaces it.
- **New: every stat change has a receipt (W1).** A tool call without a resolvable `cause` fails validation in tests.
- **New: judgments are asked once per turn and reused (W4).** Preview and resolve read the same stored answers.
- **New: the Jev 32k rule (W4).** §8.
- **New: the loop never merges its own changes (W5).**

## 11. Risks

| Risk | Mitigation |
|---|---|
| The optimiser games the judges | Held-out seeds, Pareto objectives, ongoing hand labels from the annotation queue, human playtest round per generation |
| Simulator diverges from real play | Fidelity check gates its use (W3); library gaps reported, filled by top-up sweeps, never invented |
| Jev outage or latency spike in the live turn | Fallback to rules-only consequences, flagged turn, timeout in the wrapper |
| Langfuse quota or 30-day retention | Turn record is the permanent store; Langfuse is sampled and on-demand |
| Prompt evolution cost | $15 per-run cap; turn-level evaluation on recorded states instead of full games; reflection on the Claude subscription |
| $50 a month buys limited statistical confidence (90 games give about ±10pp on an end-state share) | W1b lowers per-game cost; the simulator carries most search; error bars are stated wherever a number is published |
| No cheap debate model passes the bake-off | The leaner calls alone bring V4 Pro to about 1.8¢ a game; stay on Pro and retest new models as they ship |
| A cheap host's tool-call parser breaks silently (a known V4.1 Flash issue on hosts with older vLLM parsers) | Pin hosts per role; the first-attempt validity gate and per-call logging catch it |
| Scope creep from surfaces and 3D | Order in §6; anti-goal rule on new technology |
| "Evolved last night" becomes untrue | Public copy reads the actual current generation and its merge date |
| Human playtest data | Consent recorded; recordings and notes outside the repo; only aggregated numbers published |

## 12. Calls made in this document (for the user to check)

These were not explicitly decided in conversation; they are the author's defaults.

- Specs 2, 3, 4 are replaced by W0–W7; spec 4 items are folded into W0, W4 and W5.
- The 24k per-question cap (margin under Jev's 32k).
- Players see the receipt chain in the post-mortem; the live inspector stays behind a dev/demo flag.
- Rules evolve before prompts; content evolution is a stretch.
- All target numbers in §7 that were not discussed: the ±15pp dominance margin, the 300 ms Jev latency budget, the 25% catchphrase ceiling, the 0.8 judge agreement bar, the 0.3 Spearman bar.
- Simulator fidelity at ±10pp on 90 games for now, ±5pp once per-game cost allows.
- The $50 split at today's cost: about $17 per fidelity check (only when prompts or model change), $15 per prompt-evolution run (at most one a month), about $9 per held-out confirmation sweep. These fall roughly sixfold once W1b hits 1¢ a game.
- W1b added and moved ahead of W2 and W3, with a ≤ 1¢ real-spend target.
- At most two LLM models (debate, narration) plus Jev, all through one OpenRouter key; no DeepSeek-direct for live play.
- The bake-off rules for a debate model: at least 11 of 12 first-attempt passes, blind quality at least 90% of V4 Pro's, then the cheapest. Under these rules MiMo V2.6 Flash was chosen on single turns.
- The full-game rule (no aborts; breakage no worse than V4 Pro; retry rate at most V4 Pro's plus 10 points; blind overall at least 90% of V4 Pro's; then the cheapest) chose V4.1 Flash, after a ruling that breakage means what a player sees (§W1b, §14). Read literally, nothing qualified and the game would stay on V4 Pro at 6¢.
- The autonomous LLM call is removed in W4.
- If Jev loses the head-to-head on a question: reword or decompose twice, then a deterministic engine heuristic; never an LLM judge in the live turn.
- The simulator computes Jev answers live with memoisation instead of replaying stored ones; fidelity is checked before and after W4.
- Human playtest rounds for rules generations only.
- `adjust_grievance` leaves the debate's nested-call whitelist.
- Home page and 3D wait for the first evolved generation.

**Decided by the user in review:** loop budget at most $50 a month (ideally less), with the Claude subscription carrying everything it can; cadence on demand first, nightly later; DeepSeek or a cheaper model, to be investigated (W1b).

## 13. Open questions

- **Whether Jev works fully through OpenRouter** (§8). A direct TypeSafe key is the fallback.
- **Who the 3–5 W2 playtesters are** and when those sessions can happen; W2's human half depends on it.

## 14. Cost evidence (2026-10-22)

- **Real spend:** OpenRouter key usage for 2026-10-22 (UTC) was $5.31, all of it that day; the 90-game sweep ran 11:18–12:37 UTC, plus the integrity audit. Monthly key limit $25, $13.43 used.
- **Configuration:** `LLM_MODEL_FAST=deepseek/deepseek-v4-pro-0813` pinned to `StreamLake,Novita`; `LLM_MODEL_NARRATION=deepseek/deepseek-v4.1-flash`; `MODEL_PRICING` bills Pro at Novita's $0.99/$2.97 with cache reads at the full input rate.
- **Call structure** (offline measurement on the worktree, tiktoken `cl100k_base` as a proxy; output sizes from 167 turns in local Postgres):
  - preamble 2,635 tokens, tool schemas 1,342, personas about 510;
  - colony state 500 tokens at turn 1, rising to about 1,400 by turn 11, mostly memories;
  - debate output about 650 tokens, retried on 21% of turns;
  - autonomous output about 290 tokens;
  - narration output about 70 tokens.
- **Estimated cost per game after the W1b structure changes and W4's removal of the autonomous call:**

  | Debate model (price per M in / cache / out) | Estimated cost per game |
  |---|---|
  | V4 Pro (0.66 / 0.022 / 1.98) | about 1.8¢ |
  | V4.1 Flash (0.15 / 0.003 / 0.60) | about 0.5¢ |
  | Haiku 5.5 + a cheap narration model | about 0.4–0.5¢ |

  These are estimates until the per-call cache-hit rate is measured.
- **Debate model bake-off** (real spend $0.49, about 400 calls):
  - In the current format (XML reactions plus three parallel tool calls with nested effects), most models fail on format. V4 Pro itself passed 4 of 6 turns: models stop after one tool call, skip the reactions, or write no tool calls at all.
  - In the flat format (one forced `submit_debate` call), 14 of 20 models pass at least 11 of 12 turns, and V4 Pro passes 12 of 12.
  - Blind quality, overall 1–5 with V4 Pro at 3.83:

    | Model | Overall | $/call | p50 latency |
    |---|---|---|---|
    | Qwen3.8 Flash | 4.00 | 0.00114 | 16 s |
    | DeepSeek V4.1 Flash | 3.83 | 0.00096 | 6 s |
    | MiMo V2.6 Flash | 3.83 | 0.00079 | 2 s |
    | Claude Haiku 5.5 | 3.75 | 0.00112 | 5 s |
    | GPT-6 Luna | 3.08 | 0.00064 | 5 s |

    Gemini, Llama, Nova, gpt-oss and Kimi all scored 2.75 or below.
  - Effects matching their descriptions was the weakest criterion for every model, the best included.
  - **Format test** (real spend $0.15): four models × 12 turns × three formats, blind-scored together. Overall, pooled: flat 3.19, flat + per-effect why 3.02, flat + why + debate-only instructions 3.02; mechanics 3.10, 2.90 and 2.81. The plain flat format stays. Mechanics moves to W4's Jev effect-consistency check.
- **Full-game test** (72 games, real spend about $1.65; report in `docs/playtest/2026-10-22-fullgame-eval/report.md`, local and gitignored):
  - 4 sets × 18 games (3 settings × 3 policies × 2 seeds, same seeds), no aborts, every set passes the Spec 1 integrity audit. Every call carries the provider's billed cost.

    | Set | ¢ per game | Cached share | Retry | Blind overall (vs V4 Pro) | Visible breakage | Ranked 1st |
    |---|---|---|---|---|---|---|
    | **V4.1 Flash 1017** | **1.41** | 41% | 10.3% | **3.67 (108%)** | 38 | 11 / 18 |
    | V4 Pro (control) | 6.04 | 55% | 9.6% | 3.39 | 51 | 5 |
    | MiMo V2.6 Flash | 1.14 | 19% | 6.5% | 3.00 (89%) | 39 | 2 |
    | GPT-6 Luna | 0.59 | 95% | 0.6% | 2.72 (80%) | 33 | 0 |

  - Six blind Opus judges; two groups re-scored by a second judge moved by 0.25 on average with the same top pick both times.
  - Single-turn tests misled twice: the per-call price ignored caching, which decides the bill over a game, and MiMo's single-turn tie with V4 Pro did not hold over whole games.
- **Sources:** OpenRouter `/api/v1/models` and the endpoints API (live prices); OpenRouter prompt-caching and usage-accounting docs; DeepSeek API pricing and Anthropic-compatible API docs; the OpenRouter TypeSafe SDK guide.
