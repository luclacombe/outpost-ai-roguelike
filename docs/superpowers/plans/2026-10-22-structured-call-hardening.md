# Structured-call hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the four defects the 2026-10-22 full-game test found in our own code (F1 to F4), for every model.

**Architecture:** Three small changes to the structured-call layer from `docs/superpowers/plans/2026-10-22-flat-debate-format.md`. Off-turn actions stop getting a placeholder reason, so the validator rejects them like every other incomplete action. The debate retry also fires on missing reactions and tells the model exactly what was missing. The narration guard rejects invented non-Latin words and Markdown emphasis.

**Tech Stack:** Python 3.12, pytest (mocked LLM), ruff.

**Spec:** `docs/superpowers/specs/2026-10-22-outpost-evolved-design.md` (§W1b "Defects found"). Evidence: `/Users/luclacombe/Documents/Outpost/docs/playtest/2026-10-22-fullgame-eval/report.md` §6 (local).

## Global Constraints

- Work in `backend/`. Tests: `uv run pytest tests/ -q` (unit, mocked LLM). Lint: `uv run ruff check app/ tests/ scripts/` and `uv run ruff format --check app/ tests/ scripts/`; both must pass.
- Parsers never raise on malformed model output (`debate_format.py` contract).
- Invariants #2 and #11: the engine validator decides; never silently drop or repair a state-changing call in the parser to hide it from the tool-validity metric. A call the model left incomplete reaches the validator and is rejected with its `missing_*` / `stripped:*` reason.
- Invariant #5 (caching): system blocks and the tool schema stay identical between a first attempt and its retry; retry guidance goes in the user message only. Schema text is static per game.
- Prompt and schema text never contain a proper noun or a real resource id (`tests/test_prompts.py::test_no_prompt_text_contains_a_name_pool_name`).
- Conventional Commits; explicit `git add` paths; each commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Update `backend/app/agent/CLAUDE.md` where a convention it documents changes.

## Review Focus

1. A debate whose options are all valid but which has no reactions: it retries once, and when both attempts have 3 valid options the one with more reactions is kept.
2. The retry's system blocks and tool are identical to the first attempt's; only the user message differs.
3. An arctic narration quoting the setting's own СЕКРЕТНО passes the guard; one with an invented Cyrillic word (Коридор) fails it as a hard `script` violation, both live and in the integrity audit.
4. `*word*` and `**word**` in narration are a hard `markup` violation; ordinary prose without asterisks or backticks is not.
5. An off-turn `adjust_own_grievance` with no text reaches the validator and is rejected `missing_reason`; it is not applied with a placeholder.

---

### Task 1: Off-turn actions without text are rejected, not placeholdered (F2)

**Files:**
- Modify: `backend/app/agent/debate_format.py` (`build_offturn_tool`, `_offturn_args`)
- Test: `backend/tests/test_debate_format.py`
- Docs: `backend/app/agent/CLAUDE.md` if it mentions the placeholder

**Interfaces:** unchanged signatures.

- [ ] **Step 1: Write the failing tests**
  - `test_offturn_grievance_without_text_reaches_validator`: an `adjust_own_grievance` action with `delta: 2` and no `text` parses to args `{"delta": 2, "reason": ""}`; folding it through `engine.apply_tool_call` on a valid colony state records `rejection_reason == "missing_reason"`.
  - `test_offturn_schema_states_required_fields`: in `build_offturn_tool(...)`, the `text`, `target` and `delta` property descriptions each start with `"Required for "`.
- [ ] **Step 2: Run them; expect FAIL** (`"off-turn"` reason; old descriptions).
- [ ] **Step 3: Implement.** `_offturn_args` passes `text[:_FACT_MAX]` as the reason with no fallback. Property descriptions, verbatim:
  - `text`: `"Required for remember (the memory), speak_to_agent (the private message) and adjust_own_grievance (the reason). Those actions are rejected without it."`
  - `target`: `"Required for speak_to_agent and adjust_relationship: the other advisor."`
  - `delta`: `"Required for adjust_own_grievance and adjust_relationship: an integer from -3 to 3. For back_leader, the legitimacy bonus from 1 to 5."`
- [ ] **Step 4: Run the focused tests, then the full suite and both ruff commands; expect PASS.**
- [ ] **Step 5: Commit** `fix(agent): reject off-turn grievances without a reason instead of a placeholder`.

### Task 2: The debate retry fires on missing reactions and names what was missing (F1, F3)

**Files:**
- Modify: `backend/app/agent/debate_format.py` (`build_debate_tool`; new `incomplete_effects`)
- Modify: `backend/app/agent/nodes.py` (`agent_debate`; `_DEBATE_RETRY_NOTE` becomes `_debate_retry_note`)
- Test: `backend/tests/test_debate_format.py`, `backend/tests/test_nodes.py`
- Docs: `backend/app/agent/CLAUDE.md` "Debate retry" section

**Interfaces:**
- Produces: `debate_format.incomplete_effects(response: Any) -> int`; `nodes._debate_retry_note(valid: int, reactions: int, alive: int, incomplete: int) -> str`; `turn_meta["debate_retry_reasons"]: list[str]`.

- [ ] **Step 1: Write the failing tests**
  - `test_incomplete_effects_counts_missing_fields` (debate_format): over one `submit_debate` input, a resource effect with no `resource` (or an empty one) counts 1, a health or grievance effect with no `target` counts 1, complete effects count 0, an effect using another key name such as `resource_id` counts 1; a response with no tool call, non-list `options` or non-dict effects returns 0 without raising.
  - `test_debate_tool_effect_fields_describe_requirements`: the effect's `resource` description starts with `"Required when kind is resource"` and `target`'s with `"Required when kind is health or grievance"`.
  - `test_debate_retries_when_reactions_missing` (nodes): first response 3 valid options and no reactions, second 3 valid options and all reactions; the retry is kept, `turn_meta["debate_retried"] is True`, `turn_meta["debate_retry_reasons"] == ["reactions"]`.
  - `test_debate_keeps_first_on_tie`: both attempts 3 valid options and the same reaction count below alive; the first attempt's reactions are kept.
  - `test_debate_retry_note_names_incomplete_effects`: first response's resource effects lack `resource`; the second call's user message contains `every resource effect needs "resource"`, and its system blocks and tool equal the first call's.
  - `test_debate_no_retry_when_complete`: 3 valid options and every reaction; one call, `debate_retry_reasons == []`.
  - Existing tests that format `_DEBATE_RETRY_NOTE` move to `_debate_retry_note`.
- [ ] **Step 2: Run them; expect FAIL.**
- [ ] **Step 3: Implement.**
  - `build_debate_tool` effect property descriptions, verbatim: `kind`: `"resource changes a colony resource; health and grievance change a person."`; `resource`: `"Required when kind is resource: one of the colony's resource ids."`; `target`: `"Required when kind is health or grievance: an advisor name, or player for health."`; `delta`: `"Signed integer change."`
  - `incomplete_effects(response)`: reads the first `submit_debate` tool input the way `parse_submit_debate` does; counts effects whose `kind` is `resource` without a non-empty `resource`, or `health`/`grievance` without a non-empty `target`; logs one warning per incomplete effect with the effect's sorted keys (keys only), so sweep logs show what the model sent; never raises.
  - `agent_debate`: `reactions = len(parsed.per_agent_text)`; retry when `valid < DEBATE_TARGET_PROPOSALS or reactions < len(alive_agents)` (cost cap unchanged). `debate_retry_reasons` lists `"options"` (valid short) then `"reactions"` (reactions short), `[]` without a retry. Keep the retry when `(retry_valid, retry_reactions) > (valid, reactions)`. The warning log includes both reaction counts.
  - `_debate_retry_note(valid, reactions, alive, incomplete)`, text verbatim:
    `"\n\n<retry_note>Your previous reply had {valid} of 3 usable options and {reactions} of {alive} advisor reactions. Call submit_debate once with one reaction from every living advisor and exactly 3 options (one DOMINANT, one MORAL, one WILDCARD), each with a non-empty title, description and at least one effect."`, then only when `incomplete > 0`: `" {incomplete} effect(s) lacked a required field: every resource effect needs \"resource\" (a resource id from the colony state) and every health or grievance effect needs \"target\"."`, then `"</retry_note>"`.
- [ ] **Step 4: Run the focused tests, then the full suite and both ruff commands; expect PASS.**
- [ ] **Step 5: Commit** `fix(agent): retry the debate on missing reactions and name incomplete effects`.

### Task 3: Narration guard rejects invented foreign words and Markdown emphasis (F4)

**Files:**
- Modify: `backend/app/agent/narration_guard.py` (`validate_narration`)
- Modify: `backend/app/agent/nodes.py` (`narrate_turn`)
- Modify: `backend/scripts/audit_integrity.py` (both `validate_narration` calls)
- Test: `backend/tests/test_narration_guard.py`, `backend/tests/test_nodes.py`, and the audit's tests if they exist
- Docs: `backend/app/agent/CLAUDE.md` narration section

**Interfaces:**
- Produces: `validate_narration(text: str, allowed_terms: frozenset[str], roster_names: frozenset[str], *, foreign_ok: Iterable[str] = ()) -> list[Violation]`.

- [ ] **Step 1: Write the failing tests**
  - `test_invented_cyrillic_word_is_script`: a mostly English narration containing `Коридор Б` yields a hard `script` violation (share is under 20%).
  - `test_setting_foreign_word_is_allowed`: the same sentence shape quoting `СЕКРЕТНО`, with `foreign_ok=[<arctic lore text>]`, has no `script` violation.
  - `test_markdown_emphasis_is_markup`: `*медсестра*` (with `foreign_ok` covering the word) and `**Note**` each yield one hard `markup`; prose without asterisks or backticks yields none; a backtick span yields `markup`.
  - `test_script_reported_once`: text over the 20% share that also has an unknown word yields exactly one `script`.
  - nodes: `test_narration_retries_on_invented_foreign_word`: first narration contains an invented Cyrillic word, second is clean; the second is kept with `turn_meta["narration"]["source"] == "retry"`.
  - nodes: arctic narration quoting `СЕКРЕТНО` is kept on the first attempt.
- [ ] **Step 2: Run them; expect FAIL.**
- [ ] **Step 3: Implement.**
  - `validate_narration`: keep the share rule; otherwise, if `has_unknown_non_latin(norm, non_latin_words([*allowed_terms, *foreign_ok]))`, add `Violation("script")` (at most one `script` per text). Add `_MD_MARKUP = re.compile(r"\*[^*\n]+\*|`[^`\n]+`")`; one `markup` when `_MARKUP` or `_MD_MARKUP` matches.
  - `narrate_turn` passes `foreign_ok=(setting.lore, setting.narrative_voice)`.
  - `audit_integrity.py` passes the same `foreign_ok` for the game's setting to both calls, so live guard and audit agree.
- [ ] **Step 4: Run the focused tests, then the full suite and both ruff commands; expect PASS.**
- [ ] **Step 5: Commit** `fix(agent): narration guard rejects invented foreign words and markdown emphasis`.
