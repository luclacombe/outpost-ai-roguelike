# Truth & integrity: design (spec 1 of 4)

**Date:** 2026-10-07
**Status:** Draft for review
**Branch:** `feat/truth-and-integrity` (off local `main` `b3a6b30`)
**Source:** playtest baseline `docs/playtest/2026-10-06/report.md` (gitignored, local) and sweep `docs/playtest/2026-10-06-sweep/summary.md`

---

## 0. Where this fits

The 2026-10-06 baseline (36 persona runs, 90 scripted games) found ten ranked game bugs plus harness and eval gaps. They are split into four specs, each with its own plan, executed in order:

| # | Spec | Report items |
|---|---|---|
| **1** | **Truth & integrity (this doc)** | #2 previews, #7 silent rejections and phantom stats, #4 empty post-mortem seeds, #6 narration leaks, #10 truncated titles and literal tics, O1 unrecorded state changes |
| 2 | Systems wiring | #3 decision-driven seeds and aftermath bodies, #5 coups, transformations and grievance visibility, #8 escalation scheduling, #9 collapse rule and visibility, §5.3 legitimacy earn-back, MORAL-cost contradiction, advisor silence |
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
3. **The post-mortem's seed panels are always empty.**
   - `post_mortem.py:124-133` reads the `consequence_seeds` table, which no app code writes.
   - Seeds live only in `game_sessions.state.consequence_seeds`: 159 seeds, 47 of them dormant, in the corpus.
4. **Narration leaks.** `narrate_turn` (`nodes.py:447`) sends only the 60-char title, the picked description and the dead list, under the shared debate preamble that teaches `<agent name=…>` output. Measured:
   - non-roster names in 21/37 archives ("Ilya" in 10, partly from the prompt example at `prompts.py:179`);
   - "the player" in 9/319 narrations;
   - arctic imagery in desert (23 words) and orbital (12);
   - raw `<agent>` XML in 2 narrations;
   - a full prompt echo in 1.
5. **Titles are cut mid-word.** `events.py:240` takes `first_sentence[:60]`, giving "by his ol", "formali", "shutdow". 24/37 archives flagged it.
6. **Tics carry literals.**
   - The orbital Flight Engineer tic contains the literal "MET 14:07:32", reproduced 43 times.
   - The desert Sheriff tic ("uses a last name") made the model invent a new surname for Hector every turn.
   - Nadine's tic uses "his".
7. **State changes go unrecorded (O1, invariant #11).** Passive decay mutates legitimacy directly (`engine.py:422`). `turn_tool_calls` is cleared in `advance_turn` before persistence, so rejected and engine-derived calls never reach `turn_logs`. The RunRecord therefore reports `tool_validity_rate = 1.000` in 36/36 runs.

## 2. Goals and non-goals

**Goals**
- Everything numeric the player sees about an option equals what the engine applies when that option is picked.
- Every state change in a turn is a validated tool call and is persisted.
- Narration never shows markup, prompt text, fourth-wall slips or names the player has never seen.
- The post-mortem shows the seeds that exist.
- On-screen titles and advisor tics contain no truncation or copied literals.

**Non-goals (this spec)**
- What seeds contain, and what aftermath events say (spec 2).
- Whether legitimacy can rise (spec 2). Spec 1 only makes its movement honest.
- Any balance constant (spec 3).
- Persona-harness fixes, except the one RunRecord reader needed to verify O1 (spec 4).

## 3. Decisions taken in brainstorming

| Question | Decision |
|---|---|
| Spec shape | Four specs, one per workstream; this is spec 1 |
| How previews reach the player | Engine fills the existing `effects_preview: list[str]`; no structural API change |
| Speculative LLM effects | One optional qualitative `risk_note`; dropped if it contains a digit or stat word |
| Narration guard | Scaffold guard plus a name allowlist; one retry, then a deterministic fallback |
| Where the per-turn call record lives | New nullable JSONB column `turn_logs.tool_calls` (one manual prod migration) |

## 4. Design

### 4.1 Engine-rendered previews

**Module.** New pure module `backend/app/game/preview.py`. It moves `_option_outcome` out of `app/eval/choice_spread.py` as:

```python
def simulate_option(state: ColonyState, picked: Proposal, proposals: list[Proposal]) -> OptionOutcome
def render_effects(setting: Setting, outcome: OptionOutcome) -> list[str]
def outcome_from_calls(calls: list[ToolCall], agents: list[Agent]) -> OptionOutcome  # from recorded applied_delta values; used by the audit script and tests
```

- `simulate_option` applies `picked.tool_calls_on_pick` plus `engine.derive_resolution_consequences(...)`, then the end-of-turn decay call (§4.4), to a deep copy. It returns per-resource deltas (the *applied* delta, after clamping), legitimacy delta with its reasons, player and advisor health deltas, grievance deltas, status changes, and rejected calls.
- `app/eval/choice_spread.py` imports `simulate_option` instead of owning a copy. Its tests must still pass. Its docstring changes: outcomes now include the end-of-turn decay, which is the same for every option and so leaves spread unchanged.

**Where it runs: a shared preparation step.** `prepare_proposals(state, debate_tool_calls) -> PreparedTurn` is used by both `count_valid_proposals` (in `agent_debate`, which decides the retry) and the `apply_tool_calls` node. For each proposal the engine accepts, it does six things:

1. **Strip.** Simulate. Any nested call the dry run rejects is removed from `tool_calls_on_pick`. It is recorded on the turn as a rejected `ToolCall` with `phase="prepare"` and `rejection_reason=f"stripped:{reason}"`.
2. **Re-simulate** the trimmed proposal. Re-simulation is idempotent: a trimmed proposal has no rejects left.
3. **Validity.** The proposal counts as valid only if its *own* surviving calls produce at least one non-zero change: a resource, health, grievance or status delta. Engine-derived overrule costs don't count, since they would make any non-DOMINANT proposal "valid". This feeds the existing rule: fewer than 3 valid proposals → one debate retry. After the retry, no-effect proposals are still shown if they are needed to reach three options; their preview says so honestly (see rendering rules). This keeps §7.3's three choices.
4. **Render** `effects_preview = render_effects(setting, outcome)`. This overwrites whatever `_effect_propose_action` stored.
5. **Sanitize** `risk_note` (below).
6. **Strip scaffold prefixes** from `name` with regex `^\s*(rank\s+)?(flavor|dominant|moral|wildcard)\s*:\s*` (case-insensitive).

**Why preview equals outcome.** No node changes colony state between the pause (`interrupt_before=["resolve"]`) and `resolve`. `resolve` applies exactly the trimmed `tool_calls_on_pick` plus the same derived calls. The end-of-turn decay is a fixed call. So the preview is exact by construction, and §7's property test checks it.

**What the preview does not cover.** Autonomous advisor actions run after the pick (`autonomous_actions` node), so they are not previewable. They are already logged separately per turn. Making them visible as attributed off-turn changes is spec 2's job.

**Rendering rules (`render_effects`):**
- Order:
  1. resources, in setting YAML order;
  2. legitimacy;
  3. player health;
  4. advisor health;
  5. grievance;
  6. status changes.
- Zero deltas are omitted. If the option's own calls change nothing, the first line is `no immediate effect`, followed by any legitimacy line.
- Numbers use U+2212 for minus and an explicit `+`.
- Resources: lowercase setting display name, e.g. `fuel −10`. A clamped gain renders as `fuel +18 (at cap)`.
- Legitimacy: one line showing the total and its parts, e.g. `legitimacy −3 (overrules Astrid −2, end of turn −1)` or `legitimacy −1 (end of turn)`.
  - `derive_resolution_consequences` emits one `modify_legitimacy` call per triggered rule instead of one consolidated call, so each part keeps its own amount. Reasons gain the advisor's name ("overrules Astrid" instead of "overruled dominant advisor").
  - The other reasons ("heavy fuel drain", "severe harm to X") pass through unchanged.
- Health: `you −8 health` for the player, `Jaya −12 health` for an advisor.
- Grievance: `Astrid grievance +2`.
- Status: `Dmitri detained` / `dismissed` / `restored`, mapped from `set_advisor_status`.

**Overflow clamps, underflow rejects.**
- `validator._validate_modify_resource` stops failing `would_overflow`. The effect already clamps (`engine.py:205`).
- Underflow still fails `would_underflow`: §6.6 validates spending against inventory.
- **`ToolCall` gains `applied_delta: int | None`.** The engine sets it for every numeric tool (`modify_resource`, `modify_health`, `modify_legitimacy`, `adjust_grievance`, `adjust_own_grievance`), so the record shows what actually happened.

**`risk_note`:**
- The `propose_action` input schema drops `effects_preview` and gains optional `risk_note` (string, maxLength 120).
- `Proposal` gains `risk_note: str | None = None`.
- `sanitize_risk_note(note, setting) -> str | None` returns `None` if the note contains a digit or any of these, case-insensitive whole words: a resource id, a resource display name, `legitimacy`, `health`, `grievance`. The proposal itself is kept.

**API and frontend:**
- `schemas_io.py`: the live proposal model (line 76) and the post-mortem choice model (line 141) gain `risk_note: str | None = None`. `router.py:107` and `service.py:79` pass it through.
- `frontend/lib/api/client.ts` (lines 63 and 125) gains `risk_note?: string | null`.
- `ChoosePanel.tsx` renders `risk_note` in italics on its own line under the effects list, and the post-mortem choice row does the same.
- No other frontend change.

**Prompt changes (`prompts.py`):**
- Remove every `effects_preview` instruction and example (lines 91, 121, 131, 376).
- List the setting's five resource ids verbatim and state that legitimacy, health and grievance are engine-owned and not resources.
- Ask for effect sizes only in `tool_calls_on_pick`, never in `name` or `description`.
- Explain `risk_note` as "one short line hinting at what could go wrong. No numbers."

### 4.2 Narration

**Own system block.** `build_narration_system_block(setting, agents, player_title)` replaces the debate preamble in `narrate_turn`. It carries `cache_control: ephemeral` (stable per run, invariant #5) and contains:
- the setting `name` and `narrative_voice`;
- the roster as `name, role, alive|dead`;
- the instruction to address the player in second person, using `player_title` where a title is needed;
- the rules: 2–3 sentences; name only people on the roster; refer to anyone else by role or description ("a trucker", "the trader's cousin"); no markup, no headings, no commentary about the task.

**New setting fields** (`setting_loader.Setting`, schema-driven per invariant #7):
- `player_title: str`, required: Director (arctic), Director (orbital), Warden (desert), from SPEC §9's list. Orbital cannot use Commander: that is already an orbital advisor role (`orbital_colony_v1.yml:88`).
- `lexicon: list[str] = []`: proper nouns the narration may use, e.g. Vorgrad, HELO, MET, Helios-9, Carmine Junction, Corridor B. Authored for all three settings.

**User message:**
- `<event>` with the full title and body;
- `<choice proposer="Astrid" flavor="MORAL">name: description</choice>`;
- `<effects>` with the engine-rendered `effects_preview` of the picked option;
- `<dead>`.

**Guard: pure `validate_narration(text, allowed_terms) -> list[Violation]`** in `backend/app/agent/narration_guard.py`. Violation codes:

| Code | Rule |
|---|---|
| `markup` | matches `<[A-Za-z/][^>]*>` |
| `fourth_wall` | contains `the player` (case-insensitive) |
| `prompt_echo` | any line starting with `now write`, `write a`, `context:`, `characters:`, `recap`, `keep tone`, or containing `narrator-voice` (case-insensitive) |
| `too_long` | more than 4 sentences or more than 700 characters |
| `unknown_name:<Token>` | a Latin-script capitalized word, not sentence-initial (not after start-of-text, `.`, `!`, `?`, or an opening quote), not in `allowed_terms` |

`allowed_terms` is built per call from:
- roster names and role names;
- `player_title`, the setting name tokens and `lexicon`;
- capitalized tokens in this run's `event_log` (event titles, bodies, picked summaries);
- the current event;
- the current proposals' names and descriptions;
- the current advisor lines.

Non-Latin tokens (Cyrillic) are never flagged.

**Retry and fallback:**
- On any violation, retry once with a short corrective note that names the violations. The system blocks are unchanged, so the cache still hits.
- No retry once `cost_cents_this_turn >= MAX_COST_CENTS_PER_TURN`.
- If the second attempt also fails, or the call errors, use a deterministic line: `You backed {proposer}: {picked name}.`, or `You chose: {picked name}.` when there is no proposer. This also replaces today's `Turn N ended. Colony stands.` placeholder.
- Each failure logs a structured `narration_guard_failed` warning with the codes. The audit script recomputes violations from stored narration (the guard is pure), so no new column is needed.

### 4.3 On-screen text

- **Titles.** `EventCategory` gains `title: str | None` (≤ 48 chars).
  - Hand-write titles for every authored event in the three settings (about 13 each).
  - `_derive_title` remains only as a fallback for future settings. It cuts at the last space before 47 chars and appends `…`, never mid-word.
  - The loader test asserts every shipped event has a title.
- **Tics.** Rewrite the two leaking tics and audit all twelve:
  - Orbital Flight Engineer: "Sometimes opens with the current station time read off the board, when precision steadies them." No literal timestamp.
  - Desert Sheriff: "Addresses people by role (Doc, Trader) rather than first name; a first name means something is wrong." No surnames are implied.
  - Loader validation: `verbal_tic` contains no digits and none of `he|him|his|she|her`, because tics are per role and names are drawn per run.
- **Prompt examples.** Replace "Ilya finds the message in the queue" (`prompts.py:179`) with an example that names nobody ("the event says you found the message yourself"). A test asserts no prompt constant contains any name from any setting's `name_pool`.

### 4.4 Every state change is recorded

**Decay becomes a tool call.**
- New pure `engine.close_turn(state) -> ColonyState` applies `ToolCall(name="modify_legitimacy", caller_id=None, args={"delta": -1, "reason": "end of turn"}, phase="close")` through `apply_tool_call`.
- `engine.advance_turn` no longer subtracts legitimacy. It still increments the turn, evaluates end state, and clears per-turn fields.
- The `advance_turn` node calls `close_turn`, snapshots `closed.turn_tool_calls`, then calls `advance_turn(closed)`.

**Phase tag.** `ToolCall` gains `phase: Literal["debate", "prepare", "resolve", "autonomy", "close"] | None = None`. Each node stamps the calls it applies. The audit uses it to separate the picked option's effects from autonomy.

**Persistence.**
- `TurnLog` gains `tool_calls: Mapped[list | None]` (JSONB, nullable), via an Alembic migration that adds the column only.
- The node writes `[tc.model_dump(mode="json") for tc in snapshot]` in the same transaction as the turn log.
- Existing RLS on `turn_logs` covers the new column. No Supabase migration is needed.

**Consumer.**
- `tests/fixtures/run_simulator.py::_tool_calls_from_turn_log` reads `row.tool_calls` when non-null, and falls back to today's reconstruction otherwise.
- `tool_validity_rate` then counts stripped, rejected and derived calls.
- This one-function change is pulled forward from spec 4 to verify O1.

### 4.5 Post-mortem reads seeds from state

- `post_mortem.py` builds seed refs from `[schemas.ConsequenceSeed.model_validate(s) for s in session.state.get("consequence_seeds", [])]` instead of querying the table.
- `_seed_to_ref` keeps working on the pydantic model, which has the same field names.
- `seeds_fired`, `seeds_written` and `dormant_seeds` populate for new games *and* for every completed game, including shared links, with no backfill.
- The `consequence_seeds` table and ORM model stay, marked deprecated in the model docstring. Dropping them is a later cleanup.

## 5. Interfaces changed

| Surface | Change | Compatibility |
|---|---|---|
| `propose_action` tool schema | `effects_preview` removed, `risk_note` added | LLM-facing only |
| `ToolCall` | `+ applied_delta`, `+ phase` (both optional) | Old checkpoints load (defaults) |
| `Proposal` | `+ risk_note` (optional); `effects_preview` now engine-written | Additive |
| API proposal and post-mortem choice | `+ risk_note` | Additive; old frontend ignores it |
| `Setting` YAML | `+ player_title` (required), `+ lexicon`, `EventCategory + title` | All three shipped settings updated together |
| `turn_logs` table | `+ tool_calls JSONB NULL` | Old code ignores the column |
| `engine.advance_turn` | no longer decays; `engine.close_turn` added | Internal; callers updated in the same change |
| `engine.derive_resolution_consequences` | one `modify_legitimacy` per triggered rule; reasons name the advisor | Internal; its directional-band tests updated |
| `validator` | `would_overflow` no longer fails | Behavior change, intended |

## 6. Error handling

| Situation | Behavior |
|---|---|
| A proposal loses every effect to stripping | Counts as invalid. With fewer than 3 valid, the existing one-shot debate retry runs. Afterwards no-effect proposals fill any missing slots, previewed as `no immediate effect` |
| `risk_note` fails sanitization | Note dropped, proposal kept |
| Narration fails the guard twice, or errors | Deterministic fallback line; warning logged |
| Turn cost cap reached | No narration retry; guard failure falls straight to fallback |
| `turn_logs.tool_calls` missing (old rows) | Readers fall back to today's reconstruction |
| Old games in post-mortem | Seeds read from state JSON as for new games |

## 7. Testing

TDD per unit (`superpowers:test-driven-development`); mocks via `respx` as today.

- **preview:**
  - `simulate_option` parity (existing choice-spread tests pass after the move);
  - stripping and idempotent re-simulation;
  - validity ignores derived calls;
  - clamp renders `(at cap)` and records `applied_delta`;
  - end-of-turn decay is included;
  - golden `render_effects` output per setting, covering resource order, minus sign and the legitimacy breakdown.
- **Property test:** for generated mocked turns, picking each option and running `resolve` + `close_turn` yields applied deltas equal to `outcome_from_calls(...)`, and `render_effects` of that equals the option's `effects_preview`.
- **risk_note:** digits, resource ids, display names and stat words dropped; clean notes kept; schema rejects more than 120 chars.
- **Names:** scaffold-prefix stripping.
- **narration_guard:** one test per violation code. Also: allowlist built from roster, lexicon, event log and current proposals; sentence-initial capitals pass; Cyrillic passes.
- **narrate_turn:** first-pass success; retry then success; retry then fallback; no retry at the cost cap.
- **setting_loader:** `player_title` required; titles ≤ 48 chars and present on every shipped event; tics contain no digits or gendered pronouns; no prompt constant contains a `name_pool` name.
- **engine:** `close_turn` emits a validated `modify_legitimacy` with `phase="close"`; `advance_turn` no longer decays; end-state evaluation unchanged; `derive_resolution_consequences` emits one call per rule with named reasons.
- **persistence:** the migration upgrades and downgrades on local; `turn_logs.tool_calls` holds the full phase-tagged record, including stripped and rejected calls.
- **post_mortem:** a game with dormant seeds in state returns non-empty `dormant_seeds` and per-turn `seeds_fired`.
- **run_simulator:** prefers `turn_logs.tool_calls`; `tool_validity_rate < 1.0` when the record contains a reject.
- **frontend:** ChoosePanel renders `risk_note` when present and omits it when null.

Gates before "done":
- `cd backend && DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:54322/postgres uv run pytest tests/ -q`
- `uv run ruff check app/ tests/ scripts/` and `uv run ruff format --check app/ tests/ scripts/`
- `cd frontend && pnpm lint && pnpm test && pnpm build`

## 8. Exit gate

**New `backend/scripts/audit_integrity.py`.**
- Reads turn logs for given game ids (or `--since`) from the database.
- Refuses any non-localhost `DATABASE_URL`, using the same guard as the test suite.
- Prints a table.

**Run:** a fresh 90-game sweep against local after the branch lands (~$17), then the audit.

| Check | Target |
|---|---|
| Picked option: `effects_preview` ≠ render of its `resolve` + `close` calls | 0 |
| Picked calls rejected at `resolve` | 0 |
| Narrations with `markup`, `fourth_wall` or `prompt_echo` | 0 |
| Narrations with any `unknown_name` | ≤ 1% of turns |
| Narration fallbacks used | ≤ 5% of turns |
| Displayed event titles that are neither an authored `title` nor a fallback ending in `…` | 0 |
| Games with dormant seeds in state but empty post-mortem `dormant_seeds` | 0 |
| `tool_validity_rate` | computed from `turn_logs.tool_calls`; reported, no longer a constant 1.000 |
| Proposal names/descriptions stating a signed number next to a stat word | reported (informational) |
| Debate retry rate | ≤ 25% of turns (baseline 17%) |
| Cost per game | p95 ≤ 25¢ (SPEC §16) |

The full 42-run persona rerun waits for spec 4 (harness crash fix). A two-persona smoke run (`--personas optimizer,narrative_driven --runs-per-persona 1`) is part of this gate, to eyeball previews and narration through the persona view.

## 9. Rollout

- Implementation lands on local `main` by fast-forward. Nothing is pushed until the user says so.
- Production order:
  1. The user runs `uv run alembic upgrade head` against prod.
  2. Deploy the backend.
  3. Deploy the frontend.
- Every change is additive (a nullable column, optional fields), so a partial deploy does not break.
- Known separate prod blocker, not addressed here: Railway lacks the `LLM_*` variables.

## 10. Docs to update when this lands

- **Root `CLAUDE.md`**, new anti-pattern: "Letting the LLM author any number the player sees: previews are rendered by `app/game/preview.py` from validated calls."
- **`backend/app/agent/CLAUDE.md`:** narration has its own system block and passes `validate_narration`; prompt examples never use proper nouns.
- **`backend/app/game/CLAUDE.md`:**
  - every state change, decay included, is a phase-tagged tool call persisted to `turn_logs.tool_calls`;
  - overflow clamps, underflow rejects;
  - the post-mortem reads seeds from state.
- **`docs/PLAN.md` Errors & Learnings:**
  - the `effects_preview` and legitimacy mismatch;
  - the unwritten seeds table;
  - the narration context gap.
- **`docs/superpowers/specs/2026-05-02-axis-driven-proposals-design.md` §3.4:** one-line note that `effects_preview` is now engine-rendered.

## 11. Risks

| Risk | Mitigation |
|---|---|
| The no-effect validity rule raises debate retries and cost | Gate on retry rate ≤ 25% and p95 cost ≤ 25¢. If exceeded, relax the rule to "at least one proposal per turn may be no-effect" |
| The name guard over-fires and narration becomes the fallback line | Allowlist includes everything shown this run plus `lexicon`; gate on fallback ≤ 5%; add lexicon entries from guard logs |
| The model moves effect numbers into `description` once `effects_preview` is gone | Prompt forbids it; the audit reports it; enforcement deferred until measured |
| Players still see autonomy-driven changes they weren't previewed | Explicitly out of the preview's contract; spec 2 makes off-turn actions visible |
| The prod migration is forgotten before deploy | Readers tolerate a missing column, but the `advance_turn` writer does not: turns would fail to persist. The migration is a single additive statement; the §9 order is repeated in the PR description, and the plan's final task is a pre-deploy checklist |
