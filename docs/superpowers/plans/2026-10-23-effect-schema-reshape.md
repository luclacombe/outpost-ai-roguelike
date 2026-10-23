# Effect Schema Reshape Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every effect field the model must send a required JSON-schema field, so V4.1 Flash stops sending resource effects without `resource` and person effects without `target` (F1: 2.6% of shown options had no effect).

**Architecture:** In the `submit_debate` tool, replace each option's single `effects` array (`{kind, resource?, target?, delta}`) with two arrays: `resource_effects` (`{resource, delta}`, both required) and `person_effects` (`{kind: health|grievance, target, delta}`, all required). `parse_submit_debate` maps both back to the same nested `modify_resource` / `modify_health` / `adjust_grievance` calls, so `DebateParse`, the validator and the engine are unchanged.

**Tech Stack:** Python 3.12, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-10-22-outpost-evolved-design.md` (W1b; F1 under "Still open"). Evidence: in the 2026-10-23 confirmation sweep all 108 incomplete effects sent only `['delta','kind']`.

## Global Constraints

- Invariants #1, #2, #11: the parser only reshapes. It never repairs, guesses or drops a field; the engine validator decides.
- Invariant #5: the tool schema is part of the cached prefix and must stay deterministic for a given colony (same alive agents and resource ids → identical dict).
- Old `effects` key is NOT accepted any more (no legacy path). An option whose two lists are both empty or missing yields `tool_calls_on_pick: []` (the validator already marks it `no_effect`).
- Combined effects per option stay capped at 5: `resource_effects` `maxItems: 5`, `person_effects` `maxItems: 3`; the parser keeps resource effects first, then person effects, and truncates the combined list to 5.
- Both arrays are in the option's `required` list; neither has `minItems` (an option may legitimately touch only resources or only people). Option `description` of the arrays says "at least one effect across resource_effects and person_effects".
- Python: 100-char lines; `uv run ruff check app/ tests/ scripts/` and `uv run ruff format --check app/ tests/ scripts/` both clean; full `uv run pytest tests/` green.
- No em dashes in prompt text.

## Review Focus

1. Option with `resource_effects: []` and person effects only → nested calls are only person calls (no phantom resource call).
2. Option with both lists missing or non-list values (e.g. a string) → `tool_calls_on_pick == []`, no exception.
3. Person effect with `kind` outside health|grievance, or a non-dict entry → skipped by the parser (validator never sees it), counted by `incomplete_effects`.
4. Six-plus combined effects → exactly 5 nested calls, resource ones first.
5. `target` of `"player"` / case-insensitive advisor name resolves the same way as before (`_nested_call` target logic reused).

---

### Task 1: Split `effects` into `resource_effects` and `person_effects`

**Files:**
- Modify: `backend/app/agent/debate_format.py` (`build_debate_tool`, `_nested_call`, `incomplete_effects`, `parse_submit_debate`, module docstring)
- Modify: `backend/app/agent/prompts.py` (~lines 86-101: the `effects` contract and the example option)
- Modify: `backend/app/agent/nodes.py` (`_debate_retry_note` text: "at least one effect" stays; the incomplete-effects sentence must name the new list fields)
- Modify: `backend/tests/fixtures/_factories.py` (line ~62 builds `"effects"`) and every test payload using `"effects"` in `tests/test_debate_format.py`, `tests/test_nodes.py`, `tests/test_graph.py`, `tests/test_agent_router.py`, `tests/test_llm_provider.py`
- Modify: `backend/app/agent/CLAUDE.md` (lines ~80-95, the debate retry / incomplete_effects paragraph)

**Interfaces:**
- Unchanged signatures: `build_debate_tool(alive_agents, resource_ids) -> dict`, `parse_submit_debate(response, alive_agents, *, allowed_terms=frozenset()) -> DebateParse`, `incomplete_effects(response) -> int`, `_debate_retry_note(valid, reactions, alive, incomplete) -> str`.
- Schema produced (per option):
  - `resource_effects`: array, `maxItems 5`, items `{"type":"object","required":["resource","delta"],"properties":{"resource":{"type":"string","enum":resource_ids},"delta":{"type":"integer","description":"Signed integer change."}}}`
  - `person_effects`: array, `maxItems 3`, items `{"type":"object","required":["kind","target","delta"],"properties":{"kind":{"type":"string","enum":["health","grievance"]},"target":{"type":"string","enum":[*names,"player"],"description":"An advisor name, or player (health only)."},"delta":{"type":"integer","description":"Signed integer change."}}}`
  - option `required`: `["flavor","advisor","title","description","resource_effects","person_effects"]`

- [ ] **Step 1: Write failing tests in `tests/test_debate_format.py`**
  - `test_debate_tool_splits_effect_lists`: schema option properties contain `resource_effects` and `person_effects`, not `effects`; item `required` lists are exactly `["resource","delta"]` and `["kind","target","delta"]`; both arrays in option `required`; `person_effects.items.properties.kind.enum == ["health","grievance"]`.
  - `test_parse_maps_both_lists`: option with `resource_effects=[{"resource":"food","delta":5}]`, `person_effects=[{"kind":"grievance","target":"<Name>","delta":1},{"kind":"health","target":"player","delta":-2}]` → nested names in order `["modify_resource","adjust_grievance","modify_health"]`, resource_id `"food"`, grievance target_id is that agent's id string, health target `"player"`, every `reason` equals the title (≤60 chars).
  - `test_parse_person_only_option`, `test_parse_missing_or_malformed_lists_give_no_effects` (lists absent; lists as strings), `test_parse_caps_combined_effects_at_five` (4 resource + 3 person → 5 calls: 4 resource then 1 person), `test_parse_skips_unknown_person_kind` (kind `"resource"` in person_effects → skipped).
  - `test_incomplete_effects_counts_new_shape`: resource entry without `resource`, person entry without `target`, person entry with bad `kind` → returns 3; logs one warning per entry with sorted key names only.
  - `test_legacy_effects_key_ignored`: an option sending only the old `effects` list → `tool_calls_on_pick == []`.
- [ ] **Step 2:** Run `uv run pytest tests/test_debate_format.py -v`; expect the new tests to FAIL.
- [ ] **Step 3: Implement** the schema, parser and `incomplete_effects` changes in `debate_format.py`. Reuse the existing target resolution in `_nested_call` (split it into a resource builder and a person builder, or pass the list name; implementer's choice). `incomplete_effects` logs `submit_debate: resource effect lacks 'resource'; keys sent: [...]`, `submit_debate: person effect lacks 'target'; keys sent: [...]`, and `submit_debate: person effect has kind %r; keys sent: [...]`.
- [ ] **Step 4: Update the prompt contract** in `prompts.py`: options have `resource_effects` and `person_effects` (together 1-5; at least one); show the two item shapes with the same delta ranges as today (resource [-50, 50], grievance [-3, 3]); rewrite the example option to use `resource_effects` with two entries and `"person_effects": []`. Keep the surrounding rules (resource ids from `<colony_state>`, caps, legitimacy not a resource).
- [ ] **Step 5: Update `_debate_retry_note`** so its incomplete-effects sentence says each `resource_effects` entry needs `resource` and each `person_effects` entry needs `target`. Update its test in `tests/test_nodes.py`.
- [ ] **Step 6: Migrate fixtures and the remaining tests** to the new shape (`tests/fixtures/_factories.py` and every file listed above). No test may still send `"effects"` except `test_legacy_effects_key_ignored`.
- [ ] **Step 7: Update `backend/app/agent/CLAUDE.md`** to describe the two lists, why they are split (all fields required so cheap models cannot omit them; F1 evidence), and the new `incomplete_effects` log lines.
- [ ] **Step 8: Verify:** `cd backend && uv run pytest tests/ -q` (expect all pass, 1 skipped), `uv run ruff check app/ tests/ scripts/`, `uv run ruff format --check app/ tests/ scripts/`. Also `grep -rn '"effects"' app/ tests/ scripts/` returns only the legacy test (and unrelated `effects_preview`).
- [ ] **Step 9: Commit**

```bash
git add backend/app/agent/debate_format.py backend/app/agent/prompts.py backend/app/agent/nodes.py backend/app/agent/CLAUDE.md backend/tests/
git commit -m "fix(debate): split effects into resource and person lists with all fields required"
```
