# Truth & integrity: design (spec 1 of 4)

**Date:** 2026-10-07 (revised 2026-10-15 after review by three independent reviewers)
**Status:** Draft for review
**Branch:** `feat/truth-and-integrity` (off local `main` `b3a6b30`)
**Source:** playtest baseline `docs/playtest/2026-10-06/report.md` (gitignored, local) and sweep `docs/playtest/2026-10-06-sweep/summary.md`

---

## 0. Where this fits

The 2026-10-06 baseline (36 persona runs, 90 scripted games) found ten ranked game bugs plus harness and eval gaps. They are split into four specs, each with its own plan, executed in order:

| # | Spec | Report items |
|---|---|---|
| **1** | **Truth & integrity (this doc)** | #2 previews, #7 silent rejections and phantom stats, #4 empty post-mortem seeds, #6 narration leaks, #10 truncated titles and literal tics, O1 unrecorded state changes |
| 2 | Systems wiring | #3 decision-driven seeds and aftermath bodies, #5 coups, transformations and grievance visibility, #8 escalation scheduling, #9 collapse rule and visibility, §5.3 legitimacy earn-back, MORAL-cost contradiction in the engine, advisor silence, unavoidable event damage (replaces the removed ambient debate calls, §4.1) |
| 3 | Balance tuning | #1 starting legitimacy, drains, wealth scaling |
| 4 | Harness & eval | H1 persona crashes, fallback accounting, position-bias stat, personality-metric calibration, narration-vs-pick oracle, aborted-run archive |

Spec 1 goes first because everything after it is measured through previews, narration and the tool-call record. Tuning a game whose numbers misreport themselves measures the wrong thing.

## 1. Problem

What the baseline measured, and the cause verified in code:

1. **Previews are LLM free text.** `effects_preview` comes straight from the debate model (`tools.py:50`, `engine.py:194`) and is rendered as-is (`ChoosePanel.tsx:162`).
   - 87 of 312 picked options previewed a legitimacy gain. Across 936 options the engine could only produce legitimacy deltas of 0, −2 or −7.
   - 35/35 persona verdicts flagged the mismatch.
   - This breaks invariant #2 in spirit: the player reads numbers the engine never validated.
2. **Rejected effects vanish silently.** 233 `unknown_resource` rejections (fuel in desert and orbital, water in arctic, `legitimacy` sent as a resource) and 24 `would_overflow` rejections across 936 options. 74 of 1,258 picked calls (5.9%) did nothing while the preview still showed them. Debug strings reached the player: `player_health`, `-5 grep`, `legitimacy -`, `Rank flavor:`.
   - Cause of the phantom fuel: the cached, setting-agnostic debate preamble's worked example uses `resource_id: "fuel"` (`prompts.py:134`). The correct ids are already listed per turn in the colony-state block (`prompts.py:342`).
   - The preamble also calls MORAL "legitimacy-positive" and WILDCARD "legitimacy-negative" (`prompts.py:145-146`), and says the validator "clamps values at 0 and max" (`prompts.py:100`). Neither is true.
3. **Unpreviewed ambient changes.** The preamble invites standalone `modify_resource` / `modify_health` / `adjust_grievance` calls as "ambient cost" (`prompts.py:150-155`). The `apply_tool_calls` node folds every debate call before the pause (`nodes.py:265`), so stats move before the player chooses, with no preview and no attribution.
4. **The post-mortem's seed panels are always empty.**
   - `post_mortem.py:124-133` reads the `consequence_seeds` table, which no app code writes.
   - Seeds live only in `game_sessions.state.consequence_seeds`: 159 seeds, 47 of them dormant, in the corpus.
5. **Narration leaks.** `narrate_turn` (`nodes.py:447`) sends only the 60-char title, the picked description and the dead list, under the shared debate preamble that teaches `<agent name=…>` output. Measured:
   - non-roster names in 21/37 archives ("Ilya" in 10, partly from the prompt example at `prompts.py:179`);
   - "the player" in 9/319 narrations;
   - arctic imagery in desert (23 words) and orbital (12);
   - raw `<agent>` XML in 2 narrations;
   - a full prompt echo in 1 (in Russian).
6. **Titles are cut mid-word.** `events.py:240` takes `first_sentence[:60]`, giving "by his ol", "formali", "shutdow". 24/37 archives flagged it.
7. **Tics carry literals.**
   - The orbital Flight Engineer tic contains the literal "MET 14:07:32", reproduced 43 times.
   - The desert Sheriff tic ("uses a last name") made the model invent a new surname for Hector every turn.
   - Nadine's tic uses "his".
8. **State changes go unrecorded (O1, invariant #11).** Passive decay mutates legitimacy directly (`engine.py:422`). `turn_tool_calls` is cleared in `advance_turn` before persistence, so rejected and engine-derived calls never reach `turn_logs`. The RunRecord therefore reports `tool_validity_rate = 1.000` in 36/36 runs.

## 2. Goals and non-goals

**Goals**
- Everything numeric the player sees about an option equals what the engine applies when that option is picked.
- No stat changes between the event appearing and the player's pick.
- Every change to a numeric stat (resources, legitimacy, health, grievance) is a validated, phase-tagged tool call and is persisted.
- Narration never shows markup, prompt text, fourth-wall slips or non-Latin script, and rarely shows names the player has never seen.
- The post-mortem shows the seeds that exist.
- On-screen titles and advisor tics contain no truncation or copied literals.

**Non-goals (this spec)**
- What seeds contain, and what aftermath events say (spec 2).
- Whether legitimacy can rise, and the engine's −2 MORAL overrule cost (spec 2). Spec 1 only makes legitimacy's movement honest and removes the prompt's false claim about it.
- Unavoidable event damage as a replacement for ambient debate calls (spec 2).
- Any balance constant (spec 3).
- Persona-harness fixes, except the one RunRecord reader needed to verify O1 (spec 4).
- Non-numeric state that the engine still changes directly: deaths (`check_deaths`), the event log, seed scheduling and firing, `turn_cost_cents`. They stay as they are.

## 3. Decisions

| Question | Decision |
|---|---|
| Spec shape | Four specs, one per workstream; this is spec 1 |
| How previews reach the player | Engine fills the existing `effects_preview: list[str]`; no structural change to the proposal API |
| Speculative LLM effects | One optional qualitative `risk_note`, sanitized by the engine |
| Narration guard | Scaffold guard plus a name allowlist; hard and soft violations; one retry, then a deterministic fallback only on hard failures |
| Where the per-turn record lives | Two new nullable JSONB columns on `turn_logs`: `tool_calls` and `turn_meta` (one migration) |
| End-of-turn decay in the preview *(review)* | Shown once in the ChoosePanel header, not on every option |
| Ambient debate calls *(review)* | Forbidden: the debate phase applies `propose_action` only |
| Proposals with stripped calls *(review)* | Count as invalid for the retry; shown only as a last-resort filler |
| Unknown names in narration *(review)* | Soft violation: retry once, then keep the better attempt |

## 4. Design

### 4.1 Engine-rendered previews

**Module.** New pure module `backend/app/game/preview.py`. It moves `_option_outcome` out of `app/eval/choice_spread.py` as:

```python
def simulate_option(state: ColonyState, picked: Proposal, proposals: list[Proposal]) -> OptionOutcome
def render_effects(setting: Setting, outcome: OptionOutcome) -> list[str]
def outcome_from_calls(calls: list[ToolCall], state_before: ColonyState) -> OptionOutcome  # from recorded applied_delta values; used by the audit script and tests
def render_close(setting: Setting) -> list[str]  # the fixed end-of-turn line(s), e.g. ["−1 legitimacy at day's end"]
```

- `simulate_option` applies `picked.tool_calls_on_pick` plus `engine.derive_resolution_consequences(...)` to a deep copy, then runs `check_deaths` and `compute_end_state`, as `_option_outcome` does today. It does **not** apply the end-of-turn decay: autonomy runs between resolve and close, so near the legitimacy floor the clamped decay can differ from a dry run. The decay is shown once, as a rule, by `render_close`.
- `OptionOutcome` holds: per-resource *applied* deltas; legitimacy parts (reason, applied delta); player and advisor health deltas; deaths; grievance deltas; status changes; rejected calls; `ends_run`.
- `OptionOutcome.to_spread_dict()` returns the existing dict shape (`vector`, `n_calls`, `rejected_calls`, `ends_run`) so `summarize_choice_spread`, `sweep.py` and `play_test.py` keep working. `choice_spread.py` imports `simulate_option` instead of owning a copy.
- Tests that change with this spec, in the same commit as the validator change: `tests/test_eval_choice_spread.py` (the `would_overflow` cases at :28-30, :62-64 and :88 become clamp cases) and `tests/eval/playtest/test_summaries.py:98` (reject counts). `legitimacy_delta == 0` at :74 still holds because decay is excluded.

**Debate phase applies proposals only.**
- New `DEBATE_TOOL_NAMES = frozenset({"propose_action"})` in `nodes.py`, enforced in the `apply_tool_calls` node as `AUTONOMOUS_TOOL_NAMES` is for autonomy.
- Any other top-level debate call is not applied. It is recorded as a rejected `ToolCall` with `phase="debate"` and `rejection_reason="not_allowed_in_debate"`.
- The tool list offered to the debate call is unchanged (cache-stable); only the apply step filters.
- So the state at the pause equals the pre-debate state plus the proposals.

**Preparation step.** `prepare_proposals(state, debate_tool_calls) -> PreparedTurn` is used by both `count_valid_proposals` (in `agent_debate`, which decides the retry) and the `apply_tool_calls` node. `state` is the colony state after the whitelisted debate calls are applied. Steps:

1. **Normalize names.** Strip scaffold prefixes from each proposal's `name` with regex `^\s*(rank\s+)?(flavor|dominant|moral|wildcard)\s*:\s*` (case-insensitive). This runs before the validator's duplicate-name check.
2. **Strip.** For each proposal, in emission order, simulate it alone. Any nested call the dry run rejects is removed from `tool_calls_on_pick` and recorded as a rejected `ToolCall` with `phase="prepare"` and `rejection_reason=f"stripped:{reason}"`.
3. **Classify** on the trimmed proposal, using *applied* deltas (a call fully clamped to 0 is no change), and counting only the proposal's own calls (engine-derived overrule costs don't count):
   - `valid`: nothing stripped, at least one non-zero change;
   - `degraded`: something stripped, at least one non-zero change left;
   - `no_effect`: no non-zero change left.
4. **Retry decision.** Fewer than 3 `valid` → the existing one-shot debate retry. Only the kept debate response's strips and rejects are recorded; a discarded first attempt is counted in `turn_meta.debate_retried` but its calls are not persisted.
5. **Select** the surfaced triple: `valid` in emission order, then `degraded`, then `no_effect`; truncate to 3. This replaces the `turn_proposals[:3]` cut at `nodes.py:273`.
6. **Simulate and render** each selected proposal against the selected triple (the overrule rules depend on that list): `effects_preview = render_effects(setting, outcome)`. This overwrites whatever `_effect_propose_action` stored.
7. **Sanitize** `risk_note` (below).
8. **Write** the selected, rendered triple to both `state["proposals"]` and `colony_state.turn_proposals`. `GET /game/{id}` (`app/game/router.py:100-110`) reads the latter.

**Why preview equals outcome.** No node changes colony state between the pause (`interrupt_before=["resolve"]`) and `resolve`. `resolve` applies exactly the trimmed `tool_calls_on_pick` of the picked option plus derived calls computed from the same selected triple. So the resolve-phase outcome equals the preview by construction, and §7's property test checks it.

**What the preview does not cover.**
- Autonomous advisor actions (`back_leader`, `initiate_coup`, …) run after the pick, so they are not previewable. They are logged with `phase="autonomy"`. Making them visible is spec 2's job.
- End-of-turn decay is shown once in the ChoosePanel header from `render_close` (the nominal rule). Near legitimacy 0 the applied value can be smaller; the record shows it.

**Rendering rules (`render_effects`):**
- Order:
  1. resources, in setting YAML order;
  2. legitimacy;
  3. player health;
  4. advisor health;
  5. grievance;
  6. status changes;
  7. `run ends`, when `ends_run` is true.
- Zero applied deltas are omitted. If the option's own calls change nothing, the first line is `no immediate effect`, followed by any legitimacy line.
- Numbers use U+2212 for minus and an explicit `+`.
- Resources: lowercase setting display name, e.g. `fuel −10`. A gain clamped at max renders as `fuel +18 (at cap)`, with the applied value.
- Legitimacy: one line, the applied total and its non-zero parts in rule order, e.g. `legitimacy −9 (overrules Astrid −2, severe harm to you −7)` or `legitimacy −2 (overrules Astrid)` for a single part.
  - `derive_resolution_consequences` emits one `modify_legitimacy` call per triggered rule instead of one consolidated call, so each part keeps its own amount. Reasons gain the advisor's name ("overrules Astrid" instead of "overruled dominant advisor").
  - `_resolve_target_name` (`engine.py:479`) returns `"you"` instead of `"the player"`, so reasons read "severe harm to you". The other reasons ("heavy fuel drain") are unchanged.
  - Parts that clamp to 0 are omitted. If every part clamps to 0, the line is `legitimacy unchanged (at floor)`.
- Health: `you −8 health` for the player, `Jaya −12 health` for an advisor. A health gain clamped at max gets `(at cap)`. A lethal hit renders as `Jaya −40 health, dies`; for the player, `you −30 health` followed by `run ends`.
- Grievance: `Astrid grievance +2`, with `(at cap)` when clamped.
- Status: `Dmitri detained` / `dismissed` / `restored`, mapped from `set_advisor_status`.

**Overflow clamps, underflow rejects.**
- `validator._validate_modify_resource` stops failing `would_overflow`. The effect already clamps (`engine.py:205`).
- Underflow still fails `would_underflow`: §6.6 validates spending against inventory.
- **`ToolCall` gains `applied_delta: int | None`.** The engine sets it for every numeric tool (`modify_resource`, `modify_health`, `modify_legitimacy`, `adjust_grievance`, `adjust_own_grievance`, `back_leader`, `adjust_relationship`), so the record shows what actually happened.

**`risk_note`:**
- The `propose_action` input schema drops `effects_preview` and gains optional `risk_note` (string). The schema states "≤ 120 characters", but the provider doesn't enforce it, so the sanitizer does.
- `Proposal` gains `risk_note: str | None = None`.
- `sanitize_risk_note(note, setting, allowed_terms) -> str | None` returns `None` (the proposal itself is kept) if the note:
  - is longer than 120 characters;
  - contains a digit;
  - contains, as a case-insensitive whole word, `legitimacy`, `health`, `grievance`, or a resource id or display name from *another* shipped setting (the setting's own resource names are allowed: "water may run short" is qualitative);
  - fails the narration guard's `markup`, `fourth_wall`, `script` or `unknown_name` checks (§4.2).
- The audit reports the drop rate.

**API and frontend:**
- `schemas_io.py`: the live proposal model (line 76) and the post-mortem choice model (line 141) gain `risk_note: str | None = None`. `app/game/router.py:107`, `service.py:79` and `post_mortem.py:170-177` pass it through.
- The turn payload that carries proposals gains `close_preview: list[str]` from `render_close`.
- `frontend/lib/api/client.ts` (lines 63 and 125) gains `risk_note?: string | null` and `close_preview?: string[]`.
- `ChoosePanel.tsx` renders `close_preview` once in its header and `risk_note` in italics on its own line under each option's effects list.
- The post-mortem frontend (`TurnCard.tsx`) doesn't render previews today and is not changed; the API field is there for later.

**Prompt changes (`prompts.py`):**
- Remove every `effects_preview` instruction and example (lines 91, 121, 131, 376).
- Replace the worked example's `"resource_id": "fuel"` (line 134) with a placeholder (`"<resource_id from COLONY STATE>"`). State that the only valid resource ids are those listed in the COLONY STATE block, and that legitimacy, health and grievance are engine-owned and not resources. The preamble stays setting-agnostic so its cache key doesn't change per setting.
- Delete "legitimacy-positive" and "legitimacy-negative" from the flavor descriptions (lines 145-146).
- Replace "clamps values at 0 and max" (line 100) with "gains above max are capped; spending more than the colony has is rejected".
- Delete the ambient-cost paragraph (lines 150-155) and state that all effects go inside `tool_calls_on_pick`.
- Ask for effect sizes only in `tool_calls_on_pick`, never in `name` or `description`.
- Explain `risk_note` as "one short line hinting at what could go wrong. No numbers."

### 4.2 Narration

**Own system block.** `build_narration_system_block(setting, agents, player_title)` replaces the debate preamble in `narrate_turn`. It carries `cache_control: ephemeral` (stable per run, invariant #5). The block is likely below the provider's minimum cacheable size, so caching may not engage; that is accepted, since the block is small. It contains:
- the setting `name` and `narrative_voice`;
- the roster as `name, role, alive|dead`;
- the instruction to address the player in second person, using `player_title` where a title is needed;
- the rules: 2–3 sentences, in English; name only people on the roster; refer to anyone else by role or description ("a trucker", "the trader's cousin"); don't repeat the numbers from `<effects>`; no markup, no headings, no commentary about the task.

**New setting fields** (`setting_loader.Setting`, schema-driven per invariant #7):
- `player_title: str`, required: Director (arctic), Director (orbital), Warden (desert), from SPEC §9's list. Orbital cannot use Commander: that is already an orbital advisor role (`orbital_colony_v1.yml:88`). Test factories that build `Setting` inline (`tests/test_events.py:126`, `tests/test_difficulty.py:101`) gain the field.
- `lexicon: list[str] = []`: proper nouns the narration may use, e.g. Vorgrad, HELO, MET, Helios-9, Carmine Junction, Corridor B. Authored for all three settings.

**User message:**
- `<event>` with the full title and body;
- `<choice proposer="Astrid" flavor="MORAL">name: description</choice>`;
- `<effects>` with the engine-rendered `effects_preview` of the picked option;
- `<dead>`.

**Guard: pure `validate_narration(text, allowed_terms, setting) -> list[Violation]`** in `backend/app/agent/narration_guard.py`.

| Code | Severity | Rule |
|---|---|---|
| `markup` | hard | matches `<[A-Za-z/][^>]*>` |
| `fourth_wall` | hard | contains `the player` (case-insensitive, whole words) |
| `prompt_echo` | hard | any line starting with `now write`, `write a`, `context:`, `characters:`, `recap`, `keep tone`, or containing `narrator-voice` (case-insensitive) |
| `script` | hard | more than 20% of letters are outside Latin script |
| `too_long` | soft | more than 4 sentences or more than 700 characters |
| `unknown_name:<Token>` | soft | see the tokenizer below |

**Name tokenizer.**
- Normalize curly quotes and apostrophes to ASCII, then find tokens with `\b[A-Z][a-z]+(?:[-'][A-Za-z]+)*\b`. Strip a trailing `'s`.
- Ignore `I` and a fixed stop-list: weekday and month names, `Day`, `Turn`, `God`.
- A token is *sentence-initial* if it follows start-of-text, `.`, `!`, `?`, `:`, `;`, a newline, a dash (`—`, `–`, ` - `) or an opening quote. Sentence-initial tokens are not flagged, **except** a token that is in any shipped setting's `name_pool` and not on this run's roster.
- Matching against `allowed_terms` is case-sensitive, token by token (multi-word entries such as "Carmine Junction" are split into tokens).

`allowed_terms` is built per call from:
- roster names and role-name tokens;
- `player_title`, the setting name tokens and `lexicon`;
- capitalized tokens in this run's `event_log` (event titles, bodies, picked summaries);
- the current event;
- the current proposals' names and descriptions;
- the current advisor lines.

Because proposals and advisor lines are LLM-written, a name the debate invents becomes allowed in narration. That matches the goal (no names the player has *never seen*). The audit also reports a strict variant, against roster ∪ lexicon ∪ authored setting text, informationally.

**Retry and fallback:**
- On any violation, retry once with a short corrective note that names the violations. The system blocks are unchanged.
- No retry once `cost_cents_this_turn >= MAX_COST_CENTS_PER_TURN`.
- Choose the result: among the attempts with no hard violation, keep the one with fewer soft violations (the first, on a tie). If every attempt has a hard violation, or the call errors, use a deterministic line: `You backed {proposer}: {picked name}.`, or `You chose: {picked name}.` when there is no proposer. This also replaces today's `Turn N ended. Colony stands.` placeholder.
- Each violation logs a structured `narration_guard_failed` warning with the codes, and the outcome goes into `turn_meta.narration` (§4.4).

### 4.3 On-screen text

- **Titles.** `EventCategory` gains `title: str | None` (≤ 48 chars).
  - Hand-write titles for every authored event in the three settings (about 13 each).
  - `_derive_title` remains only as a fallback for future settings. When the first sentence is longer than 48 chars, it cuts at the last space before 47 chars and appends `…`, never mid-word. A first sentence of 48 chars or fewer is used unchanged.
  - The loader test asserts every shipped event has a title.
  - Other title sources stay as they are and are named in the audit: respite titles (`_RESPITE_TEMPLATES`, `events.py:245-300`) and aftermath titles (`"Aftermath of " + title`, `consequence.py:29`). Because authored titles are ≤ 48 chars, aftermath titles are ≤ 61.
- **Tics.** Rewrite the two leaking tics and audit all twelve:
  - Orbital Flight Engineer: "Sometimes opens with the current station time read off the board, when precision steadies them." No literal timestamp.
  - Desert Sheriff: "Addresses people by role (Doc, Trader) rather than first name; a first name means something is wrong." No surnames are implied.
  - Loader validation: `verbal_tic` contains no digits and none of `he|him|his|she|her` as whole words, because tics are per role and names are drawn per run.
- **Prompt examples.** Replace "Ilya finds the message in the queue" (`prompts.py:179`) with an example that names nobody ("the event says you found the message yourself"). A test asserts no prompt constant contains, as a whole word, any name from any setting's `name_pool`.

### 4.4 Every stat change is recorded

**Decay becomes a tool call.**
- New pure `engine.close_turn(state) -> ColonyState` applies `ToolCall(name="modify_legitimacy", caller_id=None, args={"delta": -1, "reason": "end of turn"}, phase="close")` through `apply_tool_call`.
- `engine.advance_turn` no longer subtracts legitimacy. It still increments the turn, evaluates end state, and clears per-turn fields.
- The `advance_turn` node calls `close_turn`, snapshots `closed.turn_tool_calls`, then calls `advance_turn(closed)`. `close_turn` runs on every turn, including one where a coup or death has already ended the run, exactly as the decay does today.

**Phase tag.** `ToolCall` gains `phase: Literal["debate", "prepare", "resolve", "autonomy", "close"] | None = None`. Each node stamps the calls it applies or rejects.

**Persistence.**
- One Alembic migration adds two nullable JSONB columns to `turn_logs`:
  - `tool_calls`: `[tc.model_dump(mode="json") for tc in snapshot]`, the full phase-tagged record, including stripped and rejected calls;
  - `turn_meta`: `{"debate_retried": bool, "proposal_classes": ["valid"|"degraded"|"no_effect", …], "risk_notes_dropped": int, "narration": {"source": "llm"|"retry"|"fallback"|"cost_cap", "attempts": int, "first_attempt_violations": [codes], "kept_violations": [codes]}}`.
- `turn_meta` is filled by `agent_debate`, `apply_tool_calls` and `narrate_turn` into a per-turn field on `GameTurnState`. It is written in the same transaction as the turn log.
- Existing RLS on `turn_logs` covers the new columns. No Supabase migration is needed.
- Once `TurnLog` maps these columns, every ORM `select(TurnLog)` selects them, including the post-mortem and `run_simulator`. **The migration must run before any backend that has this code**, for reads as well as writes (§9).

**Validity metric.** `tool_validity_rate` keeps its meaning: the share of *LLM-originated* calls that the engine accepted.
- Counted: `phase` in `debate`, `prepare` (each nested proposal call: kept counts valid, stripped counts invalid) and `autonomy`.
- Not counted: `resolve` and `close`. They are engine-derived, valid by construction, and would dilute the rate.
- `tool_validity_breakdown` reports counts per phase too.
- Projection for the 0.85 gate (invariant #10): in the baseline, 257 of roughly 3,700 nested calls were rejected (≈ 93% valid), and this spec removes the 24 overflow rejects and the main cause of the 233 unknown-resource rejects. `pytest -m eval` runs before merge.

**Consumer.**
- `tests/fixtures/run_simulator.py::_tool_calls_from_turn_log` reads `row.tool_calls` when non-null, and falls back to today's reconstruction otherwise. Its docstring and `app/eval/CLAUDE.md:65` are updated.
- This one-function change is pulled forward from spec 4 to verify O1.

### 4.5 Post-mortem reads seeds from state

- `post_mortem.py` builds seed refs from `session.state.get("consequence_seeds", [])`, validating each entry with `schemas.ConsequenceSeed.model_validate`. An entry that fails validation is skipped and logged, not raised, so old shared links never return 500.
- `_seed_to_ref` is rewritten to take `schemas.ConsequenceSeed` (today's version calls `.get` on ORM JSON with keys that don't exist, `post_mortem.py:396-403`):
  - `payload_summary = seed.payload.description`;
  - `origin_event_title` = the title of the event recorded at `seed.origin_decision.turn_number`, from the session's turn logs (or `state.event_log`), or `None` if not found.
- The fixture in `tests/test_post_mortem.py:315-340` inserts ORM rows with invented keys; it is rewritten to put real seed shapes into `game_sessions.state`.
- `seeds_fired`, `seeds_written` and `dormant_seeds` populate for new games *and* for every completed game, including shared links, with no backfill.
- The `consequence_seeds` table and ORM model stay, marked deprecated in the model docstring. Dropping them is a later cleanup.

## 5. Interfaces changed

| Surface | Change | Compatibility |
|---|---|---|
| `propose_action` tool schema | `effects_preview` removed, `risk_note` added | LLM-facing only |
| Debate apply step | Only `propose_action` applied; others rejected `not_allowed_in_debate` | Behavior change, intended |
| `ToolCall` | `+ applied_delta`, `+ phase` (both optional) | Old checkpoints load (defaults) |
| `Proposal` | `+ risk_note` (optional); `effects_preview` now engine-written | Additive |
| API proposal and post-mortem choice | `+ risk_note`; turn payload `+ close_preview` | Additive; old frontend ignores them |
| `Setting` YAML | `+ player_title` (required), `+ lexicon`, `EventCategory + title` | All three shipped settings and inline test settings updated together |
| `turn_logs` table | `+ tool_calls JSONB NULL`, `+ turn_meta JSONB NULL` | **Not** backward compatible for new code on an old schema: migrate first |
| `engine.advance_turn` | no longer decays; `engine.close_turn` added | Internal; callers updated in the same change |
| `engine.derive_resolution_consequences` | one `modify_legitimacy` per triggered rule; reasons name the advisor; "you" instead of "the player" | Internal; its directional-band tests updated |
| `validator` | `would_overflow` no longer fails | Behavior change, intended |
| `tool_validity_rate` | counts LLM-originated phases only | Same meaning as today; now measured on the full record |

## 6. Error handling

| Situation | Behavior |
|---|---|
| A proposal loses some effects to stripping | `degraded`: counts as invalid for the retry; surfaced only if fewer than 3 `valid` remain after the retry |
| A proposal loses every effect | `no_effect`: surfaced last, previewed as `no immediate effect` |
| A debate emits a non-`propose_action` call | Not applied; recorded as rejected `not_allowed_in_debate` |
| `risk_note` fails sanitization | Note dropped, proposal kept, counted in `turn_meta` |
| Narration has only soft violations after the retry | The attempt with fewer soft violations is kept |
| Narration has a hard violation on every attempt, or errors | Deterministic fallback line; warning logged |
| Turn cost cap reached | No narration retry; a hard violation falls straight to the fallback, a soft one is kept |
| `turn_logs.tool_calls` NULL (old rows) | `run_simulator` falls back to today's reconstruction |
| Old games in the post-mortem | Seeds read from state JSON as for new games; malformed seeds skipped and logged |

## 7. Testing

TDD per unit (`superpowers:test-driven-development`); mocks via `respx` as today.

- **preview:**
  - `simulate_option` parity through `to_spread_dict` (choice-spread tests pass, with the overflow cases updated to clamp);
  - stripping and idempotent re-simulation;
  - classification into valid, degraded and no_effect, on applied deltas, ignoring derived calls;
  - selection order and truncation;
  - clamp renders `(at cap)` and records `applied_delta`;
  - lethal hits render `dies` / `run ends`;
  - legitimacy line: multiple parts, a single part, parts clamped to 0, all clamped (`unchanged (at floor)`);
  - golden `render_effects` output per setting, covering resource order and the minus sign;
  - `render_close` output.
- **Property test:** for generated mocked turns, picking each surfaced option and running `resolve` yields `resolve`-phase applied deltas whose `outcome_from_calls(...)` render equals the option's `effects_preview`.
- **Debate whitelist:** a standalone `modify_resource` in the debate response is not applied and is recorded as `not_allowed_in_debate`.
- **risk_note:** each drop rule (length, digit, stat word, other-setting resource, guard checks); a clean note with the setting's own resource name is kept.
- **Names:** scaffold-prefix stripping runs before the duplicate-name check.
- **narration_guard:** one test per violation code and severity; tokenizer cases (`I`, possessives, hyphens, after `:` and newline, curly quotes, stop-list, sentence-initial `name_pool` name flagged); allowlist built from roster, lexicon, event log and current proposals; a whole Cyrillic narration fails `script`.
- **narrate_turn:** first-pass success; retry then success; soft violations kept after retry; hard violations fall back; no retry at the cost cap; `turn_meta.narration` filled for each case.
- **setting_loader:** `player_title` required; titles ≤ 48 chars and present on every shipped event; `_derive_title` never cuts mid-word; tics contain no digits or whole-word gendered pronouns; no prompt constant contains a `name_pool` name.
- **engine:** `close_turn` emits a validated `modify_legitimacy` with `phase="close"`, also when `end_state` is set; `advance_turn` no longer decays; end-state evaluation unchanged; `derive_resolution_consequences` emits one call per rule with named reasons and "you".
- **persistence:** the migration upgrades and downgrades on local; `turn_logs.tool_calls` holds the full phase-tagged record; `turn_meta` holds the retry, classification and narration fields.
- **post_mortem:** a game with dormant seeds in state returns non-empty `dormant_seeds` and per-turn `seeds_fired`, with `payload_summary` from the description and the origin title found; a malformed seed is skipped.
- **run_simulator / tool_validity:** prefers `turn_logs.tool_calls`; counts only LLM-originated phases; `tool_validity_rate < 1.0` when the record contains a strip.
- **frontend:** there is no frontend test runner today, so this spec doesn't add one. ChoosePanel is checked with a Playwright screenshot at 1440 and 768, with and without `risk_note`, showing the `close_preview` header.

Gates before "done":
- `cd backend && DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:54322/postgres uv run alembic upgrade head` (local only)
- `cd backend && DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:54322/postgres uv run pytest tests/ -q`
- `uv run ruff check app/ tests/ scripts/` and `uv run ruff format --check app/ tests/ scripts/`
- `uv run pytest -m eval` (tool validity ≥ 0.85, personality consistency ≥ 0.72; prompt and preview changes feed the axis judges)
- `cd frontend && pnpm lint && pnpm build`

## 8. Exit gate

**New `backend/scripts/audit_integrity.py`.**
- Reads turn logs for given game ids (or `--since`) from the database.
- Calls `scripts/_harness.py::require_local_database` and exits non-zero on any non-local host (localhost, 127.0.0.1, ::1, host.docker.internal).
- Skips turns without `turn_meta` (games paused before the branch landed).
- Prints a table.

**Run:** a fresh 90-game sweep against local after the branch lands (~$17), then the audit.

| Check | Target |
|---|---|
| Picked option: `effects_preview` ≠ render of its `resolve`-phase calls | 0 |
| Picked calls rejected at `resolve` | 0 |
| Stat changes recorded with `phase="debate"` (applied) | 0 |
| Stored narrations with a hard violation (`markup`, `fourth_wall`, `prompt_echo`, `script`) | 0 |
| First-attempt narrations with any hard violation | reported; ≤ 5% of turns |
| Stored narrations with any `unknown_name` | ≤ 2% of turns |
| Stored narrations with any strict-variant unknown name | reported (informational) |
| Narration source = `fallback` | ≤ 5% of turns |
| Displayed event titles: authored `title`, `_derive_title` output ending in `…` or ≤ 48 chars, a respite template title, or `"Aftermath of "` + one of those | 100% |
| Setting bleed: arctic cold words (fixed list: frost, ice, snow, blizzard, frozen, sub-zero) in desert and orbital narrations | reported; fewer than baseline (23 and 12) |
| Tic overuse: share of an advisor's lines that reproduce their tic's key phrase | reported per advisor; no literal tic text copied |
| Games with dormant seeds in state but empty post-mortem `dormant_seeds` | 0 |
| `tool_validity_rate` (LLM-originated phases) | ≥ 0.85, with per-phase breakdown |
| Proposal classes: share `degraded` and `no_effect` among surfaced options | reported |
| Proposal names/descriptions stating a signed number next to a stat word | reported (informational) |
| `risk_note` drop rate | reported |
| Debate retry rate (`turn_meta.debate_retried`) | ≤ 25% of turns (baseline 17%) |
| Cost per game | p95 ≤ 25¢ (SPEC §16; baseline p95 24¢) |

The full 42-run persona rerun waits for spec 4 (harness crash fix). A two-persona smoke run (`--personas optimizer,narrative_driven --runs-per-persona 1`) is part of this gate, to eyeball previews and narration through the persona view. `app/eval/playtest/turn_view.py` and `turn_message.py` pass `risk_note` and `close_preview` through so the persona sees them.

## 9. Rollout

- Implementation lands on local `main` by fast-forward. Nothing is pushed until the user says so.
- Production order, strict:
  1. The user runs `uv run alembic upgrade head` against prod.
  2. Deploy the backend.
  3. Deploy the frontend.
- **Step 1 must come first.** The new backend selects the new `turn_logs` columns on every read, so on an unmigrated database the post-mortem, share links and turn persistence all fail. The migration itself is additive, so running it before the deploy is safe for the old backend.
- Games in progress across the deploy: a turn paused under the old code resumes under the new code with LLM-written previews and untrimmed calls for that one turn. This is accepted; the audit skips those turns.
- Known separate prod blocker, not addressed here: Railway lacks the `LLM_*` variables.

## 10. Docs to update when this lands

- **Root `CLAUDE.md`**, new anti-patterns:
  - "Letting the LLM author any number the player sees: previews are rendered by `app/game/preview.py` from validated calls."
  - "Deploying a backend that maps new `turn_logs` columns before the migration: every `select(TurnLog)` fails, reads included."
- **`backend/app/agent/CLAUDE.md`:**
  - narration has its own system block and passes `validate_narration` (hard vs soft violations);
  - the debate phase applies `propose_action` only;
  - prompt examples never use proper nouns or a setting's resource ids.
- **`backend/app/game/CLAUDE.md`:**
  - every stat change, decay included, is a phase-tagged tool call persisted to `turn_logs.tool_calls`;
  - overflow clamps, underflow rejects;
  - the post-mortem reads seeds from state.
- **`backend/app/eval/CLAUDE.md`:**
  - `tool_validity_rate` counts LLM-originated phases from `turn_logs.tool_calls` (replaces the gap note at :65);
  - choice-spread outcomes exclude end-of-turn decay.
- **`docs/PLAN.md` Errors & Learnings:**
  - the `effects_preview` and legitimacy mismatch;
  - the phantom fuel from the preamble example;
  - the unwritten seeds table and `_seed_to_ref`'s invented keys;
  - the narration context gap.
- **`docs/superpowers/specs/2026-05-02-axis-driven-proposals-design.md` §3.4:** one-line note that `effects_preview` is now engine-rendered.

## 11. Implementation order

Each step lands with its tests:

1. Schema fields: `ToolCall` (`applied_delta`, `phase`), `Proposal` (`risk_note`), `Setting` (`player_title`, `lexicon`, `EventCategory.title`), and the YAML for all three settings, including titles and tics.
2. Engine: `close_turn`, per-rule legitimacy with named reasons and "you", `applied_delta`, and the overflow clamp. The validator change and the choice-spread test updates land in the same commit.
3. `preview.py`, with the `_option_outcome` move and `to_spread_dict`.
4. Nodes and prompts: debate whitelist, `prepare_proposals`, selection, prompt edits.
5. Migration and persistence (`tool_calls`, `turn_meta`), `run_simulator` reader, validity metric.
6. Narration: system block, guard, retry and fallback.
7. Post-mortem seeds from state.
8. API and frontend: `risk_note`, `close_preview`, ChoosePanel, persona view passthrough.
9. Audit script.
10. Docs (§10), and a pre-deploy checklist with the §9 order for the PR description.

## 12. Risks

| Risk | Mitigation |
|---|---|
| The stricter validity rule (degraded counts as invalid) raises debate retries and cost | The prompt fixes remove the main strip cause. Gate on retry rate ≤ 25% and p95 cost ≤ 25¢. If exceeded, relax: `degraded` counts as valid when it has two or more surviving effects |
| Narration retries push cost past the 25¢ p95 (baseline 24¢) | Narration is a small call and retries only on violations; the cost cap stops retries. If the gate fails, drop the retry for soft-only violations |
| The name guard over-fires | `unknown_name` is soft, so it never forces the fallback; the stop-list and sentence-initial rule cut false positives; add `lexicon` entries from guard logs |
| The model moves effect numbers into `description` once `effects_preview` is gone | Prompt forbids it; the audit reports it; enforcement deferred until measured |
| Removing ambient debate calls makes crises feel cheaper | Unavoidable event damage moves to spec 2; spec 3 tunes the balance |
| Players still see autonomy-driven changes they weren't previewed | Explicitly out of the preview's contract; spec 2 makes off-turn actions visible |
| The prod migration is forgotten before deploy | Every read of `turn_logs` would fail. §9 order is a hard requirement, repeated in the PR description and the plan's final checklist task |
