# Handoff: Outpost Evolved, 2026-10-22

Written at the end of a long session so the next session loses nothing. Read this first, then the umbrella design doc. Everything here is on disk; nothing depends on the old chat.

## 0. Where things live

| What | Path |
|---|---|
| Umbrella design (direction, order of work, targets, cost evidence) | `docs/superpowers/specs/2026-10-22-outpost-evolved-design.md` (this branch) |
| Implementation plan (W1b flat format, done) | `docs/superpowers/plans/2026-10-22-flat-debate-format.md` (this branch) |
| SDD ledger: every ruling and deferred minor | `.superpowers/sdd/2026-10-22-flat-debate-format/progress.md` in this worktree (gitignored) |
| Bake-off report: 20 models, blind-scored, plus the format test | `/Users/luclacombe/Documents/Outpost/docs/playtest/2026-10-22-model-bakeoff/report.md` (main checkout, gitignored, local only) |
| Bake-off kit, raw outputs, blind packets and scores | the same folder: `kit/`, `raw_*`, `blind/`, `format-test/` |
| Full-game evaluation kit | the same folder: `fullgame-kit/` (`objective.py`, `transcripts.py`, `RUBRIC_GAME.md`) |
| Full-game sweep outputs (in progress at handoff) | `docs/playtest/2026-10-22-fullgame-{mimo,flash,luna,pro}/` in this worktree (gitignored) |
| Sweep logs | `/tmp/claude-501/fullgame-{mimo,flash,luna,pro}.log` |
| Memory | `~/.claude/projects/-Users-luclacombe-Documents-Outpost/memory/` (`project_outpost_evolved_direction.md`, `reference_jev_typesafe.md`) |

**Branches:**
- `feat/truth-and-integrity` (Spec 1, 31 commits, worktree `.claude/worktrees/truth-and-integrity`): **not merged**.
- `feat/flat-debate-format` (this worktree, stacked on Spec 1 at `e177ff6`): the W1b structured-call work.
- The main checkout sits on `chore/test-llm-env-isolation`.

## 1. What the user wants (in their words, condensed)

- **Wrap up Outpost and make it a far more impressive, deeper project, not a bigger one.** The guiding principle: say what a top professional would do, start from the rawest material, name the methods and tools, and say what outside evidence shows it's right.
- **A strong LLM-ops story for recruiters:** traceability, evaluation, and a loop that measurably improves the game.
- **Branding:**
  - *"The colony you play today was evolved last night"* for the game;
  - *"Every number has a receipt"* for the engineering.
- **Use Jev heavily.** Jev is TypeSafe's System One model (docs.typesafe.ai), not JEPA or GEPA. Never send a Jev question over 32k tokens of context.
- **Cost:** get each game to about 1¢ or less while keeping it working, maintainable, scalable, secure and fun. The loop budget is at most $50 a month, ideally less, with the Claude subscription carrying everything Claude Code can do.
- **Cadence:** loop runs on demand first, nightly later.
- **Models:** DeepSeek or something cheaper. The user is sceptical of Claude Haiku and prefers **blind, pre-registered, cheapest-capable** selection.
- **Working style:** sceptical of single tests, so verify with full games and blind evaluators. Use subagents for the work: Sonnet as the default worker, Opus for judging and reviews, and the model and effort always stated. Decide and record rulings rather than stalling. Often follows along over Remote Control.
- **Writing rules:** no em dashes in outward-facing text; concise; explain the why.

## 2. Decisions made (all recorded in the design doc)

**Order of work:**
- W0: merge Spec 1.
- W1b: cost and structured calls (built here, ahead of W1).
- W1: trace foundation (turn record with causal "receipts", OTel tracing of model calls into Langfuse).
- W2: error analysis from raw material and human playtests.
- W3: replay simulator.
- W4: Jev head-to-head, then the judgment layer (spec 2), where the LLM writes, Jev judges, and the engine sets the cost. W4 also removes the autonomous LLM call and adds a Jev check that effects match descriptions.
- W5: evolution, with rules first in the simulator, then prompts, then content as a stretch.
- W6: surfaces (inspector, post-mortem "why" chain, public lab page, then home page and 3D).
- W7: story.

**Fixed targets** (design doc §7):
- SPEC §10 end-state bands.
- Play style changes the ending in at least 40% of paired seeds (23% today).
- No dominant play style.
- Cost at most 1¢ a game in real spend.
- Judges reach at least 0.8 true-positive and true-negative rates against hand labels.
- Decision-tension score correlates with human time-to-decide (Spearman at least 0.3).
- Of 5 or more strangers: at least 4 finish a run and at least 3 can retell it.

**Pending user decisions:**
- Merging Spec 1, which deploys production. Its deploy plan requires migration `a7c3e9d41f20` on prod before pushing main. Its last open item, the catchphrase-copy FAIL, sits in the "Truth and integrity implementation" session.
- Merging this branch after Spec 1.
- A one-cent Jev test through OpenRouter, with a direct TypeSafe key as the fallback.
- Who the 3–5 playtesters will be.

## 3. Research results worth keeping (2026-10-22)

**Real cost:**
- The 90-game sweep cost about **6¢ a game in real spend**, not the 19¢ the tracker reported. The tracker billed V4 Pro at Novita's rate with no cache discount.
- The debate actually ran on **DeepSeek V4 Pro** (`LLM_MODEL_FAST`); only narration ran on V4.1 Flash.
- OpenRouter key: $25 monthly limit, about $14 spent by the end of the session.

**Caching and billing:**
- Caching works through OpenRouter's Anthropic-compatible endpoint (V4 Pro read 4,096 tokens from cache).
- OpenRouter returns the billed USD cost in `response.usage.model_extra["cost"]`.

**Langfuse gap:**
- Langfuse is attached via LangChain callbacks, so the raw `client.messages.create` calls were never traced (no prompts, tokens or cost).
- The Hobby tier gives 50k units a month (traces, observations and scores combined) and 30 days of data access. Langfuse is a viewer, not the permanent record.

**Jev on OpenRouter:**
- The TypeSafe SDK works with an OpenRouter key: base URL `https://openrouter.ai/api`, models `jev-1.13` and `jev-latest`.
- The catalog lists `typesafe/jev-router`.
- Price $0.042 per million input tokens, output free, about 111 ms per call.
- Over 32k tokens per question the request is rejected, not charged more.

**DeepSeek direct:**
- Off-peak is half price; there is an Anthropic-compatible endpoint; data is probably hosted in China. Not used for live play.

**Libraries:** LiteLLM, semantic caches and LLMLingua are a poor fit; every game state is unique.

**Debate model bake-off:**
- 20 models on 12 real turns from the sweep, validated by the game's own `prepare_proposals`, then blind-scored by 4 Opus evaluators with a duplicate control as a consistency check (gap 0.00). Real spend $0.49.
- **The old format broke most models.** Free-text XML reactions plus three parallel tool calls with nested effects failed widely, and V4 Pro itself passed only 4 of 6.
- **The flat format fixed it:** one forced `submit_debate` tool with reactions and three options. 14 of 20 models passed at least 11 of 12.

  | Model | Blind quality (1–5) | $ per call | Speed (p50) |
  |---|---|---|---|
  | Qwen3.8 Flash | 4.00 | 0.00114 | 16 s |
  | DeepSeek V4.1 Flash 1017 | 3.83 | 0.00096 | 6 s |
  | **MiMo V2.6 Flash** | **3.83** | **0.00079** | **2 s** |
  | V4 Pro | 3.83 | 0.00317 | 17 s |
  | Haiku 5.5 | 3.75 | 0.00112 | 5 s |
  | GPT-6 Luna | 3.08 | 0.00064 | 5 s |

  Gemini, Llama, Kimi, gpt-oss and Nova all scored 2.75 or below. Mistral was rate-limited upstream.

**Format test** (MiMo, V4.1 Flash, Luna and V4 Pro × 12 turns × 3 formats, blind-scored together, $0.15):
- **The plain flat format won.** Pooled overall: flat 3.19, flat + per-effect why 3.02, flat + why + debate-only prompt 3.02.
- **Effects contradicting their own description** happen with every model and every format, so they go to W4's Jev check.
- **MiMo blips:** one empty reply, one reply with no reactions, and one stray "頻道".
- **V4.1 Flash** matched V4 Pro in both rounds.

## 4. What this branch built (feat/flat-debate-format, HEAD 1f9b4bd, verified green)

- **Status:** 1,017 unit tests pass; `ruff check` and `ruff format --check` are clean.
- **Process:** six tasks via subagent-driven development, a per-task review for each, a final Opus whole-branch review ("with fixes"), one fix wave, and a scoped re-review (all addressed).
- **Billed cost:** `anthropic_client.cost_cents_of` prefers the billed `usage.cost` when it is finite and non-negative. `usage_record(...)` records every LLM call in `turn_meta["llm_calls"]`.
- **Structured calls:** `app/agent/debate_format.py` holds `submit_debate` and `submit_offturn`.
  - Per-game enums.
  - Parsers rebuild the existing `DebateParse` / `ToolCall` shapes, so the engine is unchanged.
  - Malformed input never raises.
  - Non-Latin words are dropped unless they appear in the setting's or event's text.
- **Nodes:**
  - `nodes.build_debate_request` is shared with the axis eval.
  - `_call_structured` forces `tool_choice` and falls back to `auto` on host rejections, remembered per model for the process.
  - `turn_meta` gains `debate_tool_choice`, `autonomous_tool_choice` and `llm_calls`.
- **Docs:** updated, including invariant #6 reworded ("one batched structured call").

**Rulings made on the user's behalf** (full text in the ledger):
1. Build stacked on Spec 1, because merging Spec 1 is a production deploy.
2. Accept a cached prefix of about 2.9K tokens, below Claude Haiku 4.5's 4,096 minimum. The game runs on OpenRouter models with no such minimum. Re-measure if a Claude model is chosen.
3. `submit_offturn` field descriptions carry the per-kind guidance.
4. The non-Latin allowlist from setting and event text.
5. Invariant #6 reworded, with its meaning unchanged.
6. Detain/dismiss (`set_advisor_status`) is unreachable from the LLM under the flat schema. This is documented; W4 may revisit.
7. The final-review minors were folded into the fix wave.

**Known follow-up:** the eval-marked `test_run_simulator.py::test_tool_calls_round_trip_through_turn_logs` flakes by UTC date. It predates this branch.

## 5. In flight at handoff: the full-game test

**Done later on 2026-10-22.** V4.1 Flash (`deepseek/deepseek-v4.1-flash-1017`) chosen for debate, off-turn and narration, after one ruling on the breakage criterion. Report: `/Users/luclacombe/Documents/Outpost/docs/playtest/2026-10-22-fullgame-eval/report.md` (local). Design doc W1b and §14 updated, `.env` pin restored. Steps 1 to 5 of §6 are complete. **2026-10-23:** F2 to F4 fixed and confirmed by an 18-game sweep; F1 halved (2.6% of shown options still have no effect), with the remaining fix a schema reshape (report §8). Then finish the branch.

- **Goal:** decide the debate and narration model on whole games, not single turns.
- **Setup:** four sets of 18 games each (3 settings × 3 policies × 2 seeds, all sets on the same seeds), run in parallel from this worktree's `backend/` against local Postgres.
- **Background task:** `bijmdol7l` in the old session. The processes are independent and keep writing to disk.

| Set | `LLM_MODEL_FAST` | `LLM_MODEL_NARRATION` | Provider pin |
|---|---|---|---|
| mimo | xiaomi/mimo-v2.6-flash | same | none |
| flash | deepseek/deepseek-v4.1-flash-1017 | same | none |
| luna | openai/gpt-6-luna | same | none |
| pro (control) | deepseek/deepseek-v4-pro-0813 | deepseek/deepseek-v4.1-flash | `StreamLake,Novita` |

**Check completion:**
- `pgrep -fl scripts.sweep` shows nothing left running;
- each `docs/playtest/2026-10-22-fullgame-*/` has `summary.md`;
- the logs end with an "exit" line.

**Temporary change to undo afterwards:** the worktree `backend/.env` had its `LLM_PROVIDER_ORDER` line commented out for these runs. Restore it from `backend/.env.bak-before-fullgame`, or put back `LLM_PROVIDER_ORDER=StreamLake,Novita`, then delete the backup.

**Selection rule, fixed before results:** a model is eligible if all of these hold:
1. no aborted games;
2. no more breakage than the control, from the objective checks and the blind breakage counts;
3. a debate retry rate no worse than the control's plus 10 points;
4. blind whole-game overall at least 90% of the V4 Pro control's.

Choose the cheapest eligible model by billed cost per game. Narration follows the chosen model unless the narration scores argue otherwise.

## 6. Next steps, in order

1. **Wait for the four sweeps to finish.** Do not re-run them.
2. **Objective checks.**
   - From the worktree `backend/`, run:
     `uv run python /Users/luclacombe/Documents/Outpost/docs/playtest/2026-10-22-model-bakeoff/fullgame-kit/objective.py mimo=../docs/playtest/2026-10-22-fullgame-mimo flash=../docs/playtest/2026-10-22-fullgame-flash luna=../docs/playtest/2026-10-22-fullgame-luna pro=../docs/playtest/2026-10-22-fullgame-pro`
   - It reports end states, billed and tracker cost per game, cache-read share, retry and fallback rates, turns with fewer than 3 options, missing reactions, narration fallbacks and seconds per turn.
   - Also read each set's `summary.md`, and run the Spec 1 integrity audit per set:
     `DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:54322/postgres uv run python -m scripts.audit_integrity --game-ids <comma list from raw/*.json game_id>`
3. **Blind whole-game evaluation.**
   - Build the packets:
     `uv run python <fullgame-kit>/transcripts.py --runs mimo=…,flash=…,luna=…,pro=… --packets 6 --out <dir>/blind_eval_fullgame`
     This writes `fullgame_key.json` next to the packet folder; keep it away from the evaluators.
   - Copy `RUBRIC_GAME.md` into the packet folder.
   - Dispatch 6 fresh Opus evaluators (effort high), each reading only the rubric and its own packet and writing `scores_N.json`.
   - Write a small aggregator mapping group and letter to the set via the key: mean per criterion, breakage counts, rank percentile.
4. **Decide by the selection rule.**
   - Write the report to `docs/playtest/2026-10-22-fullgame-report.md` (local).
   - Update design doc W1b and §14 with the result and the chosen models (commit on this branch).
   - Update `.env.example` and the root CLAUDE.md LLM line if the default model changes.
5. **Restore the `.env` pin** (§5).
6. **Finishing the branch** (superpowers:finishing-a-development-branch): it merges right after Spec 1. Ask the user before any merge, push or deploy. Then delete the SDD workspace, after copying its `Ruling:` lines into the final message.
7. **Then W1** (trace foundation), per the design doc.

## 7. Approved run plan (user, 2026-10-23: "I approve everything... execute as much as you can without me. I pre approve it all.")

Execute in order; record each outcome here and in the design doc.
1. **F1 schema reshape** on this branch: split `effects` into `resource_effects` (`resource`, `delta`, both required) and `person_effects` (`kind` health|grievance, `target`, `delta`, all required) in `debate_format.build_debate_tool` / `parse_submit_debate` / `incomplete_effects`, the preamble contract in `prompts.py` (~lines 86-101), tests and `backend/app/agent/CLAUDE.md`. Write a plan in `docs/superpowers/plans/`, run it with subagent-driven development (Sonnet implementers and reviewers, Opus final review; state model and effort). Then an 18-game V4.1 Flash sweep, same seeds, unpinned: `cd backend && env DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:54322/postgres LLM_MODEL_FAST=deepseek/deepseek-v4.1-flash-1017 LLM_MODEL_NARRATION=deepseek/deepseek-v4.1-flash-1017 LLM_PROVIDER_ORDER=, uv run python -m scripts.sweep --seeds 2 --concurrency 4 --out ../docs/playtest/<date>-fullgame-flash-v3` (the lone comma overrides the `.env` V4 Pro pin; a blank value would not). Compare with `uv run python /Users/luclacombe/Documents/Outpost/docs/playtest/2026-10-22-fullgame-eval/kit/compare.py before=../docs/playtest/2026-10-23-fullgame-flash-v2 after=<new dir>` plus the integrity audit. Success: options with "no immediate effect" near 0 (was 2.6%), tool validity ≥ 0.85. **Done 2026-10-23:** 0 no-effect options, 0 strips, validity 0.910, retry 4.5%, 1.48¢/game, all audit checks PASS (`docs/playtest/2026-10-23-fullgame-flash-v3`; audit in the eval kit as `audit_flash_v3.txt`). Jev one-cent test (step 3) also done: works via OpenRouter, see design doc §8.
   **Blocked for ship (step 2), 2026-10-23:** prod Supabase project `Outpost` is INACTIVE (paused; the pooler answers "tenant not found") and the Railway CLI is not logged in. The user must restore the project in the Supabase dashboard and run `railway login` (or set the Railway variables in the dashboard). The deploy checklist also notes Railway lacks the `LLM_*` variables entirely; set `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_FAST`, `LLM_MODEL_NARRATION` (and leave `LLM_PROVIDER_ORDER` unset) before pushing main.
2. **Ship.** Check the Spec 1 session ("Truth and integrity implementation") for its open catchphrase FAIL first; settle or rule on it. Then: run migration `a7c3e9d41f20` on production BEFORE pushing main (Spec 1's deploy plan in `feat/truth-and-integrity` docs), merge `feat/truth-and-integrity` into main, push; merge `feat/flat-debate-format`, push; set Railway `LLM_MODEL_FAST` and `LLM_MODEL_NARRATION` to `deepseek/deepseek-v4.1-flash-1017` and clear `LLM_PROVIDER_ORDER`; verify the deploy (health, one real turn). Use superpowers:finishing-a-development-branch; copy the SDD ledgers' `Ruling:` lines into the final message, then delete both SDD workspaces. Stop and report if any step fails or needs credentials Claude cannot use.
3. **Jev 1¢ test** through OpenRouter (TypeSafe SDK, base URL `https://openrouter.ai/api`, model `jev-latest`, one question well under 32k). Record result in design doc §8/§13.
4. **Playtesters for W2:** only the user can choose; ask.
5. Then **W1** (trace foundation, includes F5 fractional cost) per the design doc: brainstorm/spec gaps, plan, execute.
